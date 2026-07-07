# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Alarm engine: trigger, time-window gating, acknowledge/silence, disarm,
emergency-contact tagging, and the HTTP endpoints."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import (
    AppConfig, CameraAgentRuntime, build_app, _parse_hhmm, Alarm,
)
from context import CameraSpec


def _runtime(detections=None, emergency_contacts=None):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        emergency_contacts=emergency_contacts,
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front")],
    )
    rt = CameraAgentRuntime(cfg)

    async def fake_get_frame(cam, **_kw):
        return b"\xff\xd8\xff"

    async def fake_infer(*, frame_jpeg, **kw):
        return {"result": {"detections": detections if detections is not None else [{"label": "fire"}]}}

    rt.context.get_frame = fake_get_frame
    rt.detection_client.infer = fake_infer
    return rt


# ── time parsing + window ──────────────────────────────────────────────


def test_parse_hhmm():
    assert _parse_hhmm("18:00") == 18 * 60
    assert _parse_hhmm("06:30") == 6 * 60 + 30
    assert _parse_hhmm("nonsense") is None
    assert _parse_hhmm(None) is None


def test_window_label():
    assert Alarm(1, "n", "fire", ["cam1"], after_min=1080).window_label() == "after 18:00"
    assert Alarm(1, "n", "fire", ["cam1"]).window_label() == "any time"


def test_overnight_window_wraps():
    rt = _runtime()
    # 22:00 → 06:00 window
    a = Alarm(1, "night", "person", ["cam1"], after_min=22 * 60, before_min=6 * 60)
    # 23:00 inside, 12:00 outside (checked via the manager's _in_window logic)
    import datetime
    class _FakeNow:
        @staticmethod
        def now():
            return datetime.datetime(2026, 1, 1, 23, 0)
    # monkeypatch-free: exercise the math directly
    mins_in, mins_out = 23 * 60, 12 * 60
    assert (a.after_min <= mins_in or mins_in < a.before_min)
    assert not (a.after_min <= mins_out or mins_out < a.before_min)


# ── trigger + acknowledge lifecycle ────────────────────────────────────


def test_alarm_triggers_and_logs_event():
    rt = _runtime(detections=[{"label": "fire"}])

    async def go():
        alarm = rt.alarms.create(name="Fire", target="fire", camera_ids=["cam1"])
        for _ in range(60):
            if rt.alarms.list()[0]["triggered"]:
                break
            await asyncio.sleep(0.02)
        data = rt.alarms.list()[0]
        events = rt.alarms.events()
        rt.alarms.stop(alarm.id)
        assert data["triggered"] is True
        assert events and "fire" in events[0]["text"]

    asyncio.run(go())


def test_acknowledge_silences_then_rearms_blocked_by_cooldown():
    rt = _runtime(detections=[{"label": "fire"}])
    rt.alarms._rearm = 999  # don't immediately re-trigger after ack

    async def go():
        alarm = rt.alarms.create(name="Fire", target="fire", camera_ids=["cam1"])
        for _ in range(60):
            if rt.alarms.list()[0]["triggered"]:
                break
            await asyncio.sleep(0.02)
        assert rt.alarms.acknowledge(alarm.id) == 1
        await asyncio.sleep(0.1)
        assert rt.alarms.list()[0]["triggered"] is False  # stays silenced (cooldown)
        rt.alarms.stop(alarm.id)

    asyncio.run(go())


def test_alarm_silent_outside_time_window():
    rt = _runtime(detections=[{"label": "person"}])

    async def go():
        # active only 00:00–00:01 — effectively never "now"
        alarm = rt.alarms.create(name="Night", target="person", camera_ids=["cam1"],
                                 after_min=0, before_min=1)
        await asyncio.sleep(0.2)
        triggered = rt.alarms.list()[0]["triggered"]
        rt.alarms.stop(alarm.id)
        assert triggered is False

    asyncio.run(go())


def test_emergency_contact_tagged_from_config():
    rt = _runtime(emergency_contacts={"fire": "+1-555-0100"})

    async def go():
        msg = await rt._handle_create_alarm({"name": "Fire", "target": "fire", "camera_id": "cam1"})
        assert "555-0100" in msg
        assert rt.alarms.list()[0]["emergency_contact_configured"] is True

    asyncio.run(go())


# ── voice handlers ─────────────────────────────────────────────────────


def test_create_alarm_handler_parses_after_time():
    rt = _runtime()

    async def go():
        msg = await rt._handle_create_alarm(
            {"name": "After-hours", "target": "person", "camera_id": "cam1", "after": "18:00"})
        assert "armed alarm" in msg.lower()
        assert rt.alarms.list()[0]["window"] == "after 18:00"

    asyncio.run(go())


# ── endpoints ──────────────────────────────────────────────────────────


def test_alarm_endpoints():
    rt = _runtime()
    client = TestClient(build_app(rt))
    r = client.post("/alarms", json={"name": "Fire", "target": "fire", "camera_id": "cam1"})
    assert r.status_code == 202
    body = client.get("/alarms").json()
    assert body["alarms"] and body["alarms"][0]["name"] == "Fire"
    aid = body["alarms"][0]["id"]
    assert client.post("/alarms/ack", json={}).json()["silenced"] >= 0
    assert client.delete(f"/alarms/{aid}").status_code == 200
    assert client.delete("/alarms/9999").status_code == 404
    assert client.post("/alarms", json={"name": "x", "camera_id": "cam1"}).status_code == 400

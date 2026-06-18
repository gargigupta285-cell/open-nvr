# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""External webhook notifications: fan-out, category filtering, best-effort
failure handling, alarm/monitor wiring, and the /notify endpoints."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import AppConfig, CameraAgentRuntime, Notifier, build_app
from context import CameraSpec


def _runtime(webhooks=None, events=None, detections=None):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        notify_webhooks=webhooks, notify_events=events,
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front")],
    )
    rt = CameraAgentRuntime(cfg)

    async def fake_get_frame(cam):
        return b"\xff\xd8\xff"

    async def fake_infer(*, frame_jpeg, **kw):
        return {"result": {"detections": detections if detections is not None else [{"label": "fire"}]}}

    rt.context.get_frame = fake_get_frame
    rt.detection_client.infer = fake_infer
    return rt


class _FakeResp:
    def __init__(self, code=200):
        self.status_code = code


def _capture_client(rt, posts, code=200):
    class _Client:
        async def post(self, url, json=None):
            posts.append((url, json))
            return _FakeResp(code)
    rt.notifier._client = _Client()


# ── payload + fan-out ──────────────────────────────────────────────────


def test_send_fans_out_to_all_webhooks_with_slack_and_discord_keys():
    rt = _runtime(webhooks=["http://a/hook", "http://b/hook"])
    posts = []
    _capture_client(rt, posts)
    ok = asyncio.run(rt.notifier.send({"type": "alarm", "title": "Fire", "text": "fire on cam1"}))
    assert ok == 2 and len(posts) == 2
    body = posts[0][1]
    assert body["text"] == "Fire: fire on cam1"      # Slack
    assert body["content"] == "Fire: fire on cam1"    # Discord
    assert body["type"] == "alarm" and body["camera"] is None


def test_category_filter_drops_unsubscribed_events():
    rt = _runtime(webhooks=["http://a"], events=["alarm"])  # not 'notify'
    posts = []
    _capture_client(rt, posts)
    assert asyncio.run(rt.notifier.send({"type": "notify", "title": "x"})) == 0
    assert posts == []
    assert asyncio.run(rt.notifier.send({"type": "alarm", "title": "y"})) == 1


def test_test_event_always_allowed():
    rt = _runtime(webhooks=["http://a"], events=["alarm"])
    posts = []
    _capture_client(rt, posts)
    assert asyncio.run(rt.notifier.send({"type": "test", "title": "ping"})) == 1


def test_delivery_failure_is_best_effort():
    rt = _runtime(webhooks=["http://a", "http://b"])

    class _Client:
        async def post(self, url, json=None):
            if "a" in url:
                raise RuntimeError("connection refused")
            return _FakeResp(200)

    rt.notifier._client = _Client()
    ok = asyncio.run(rt.notifier.send({"type": "alarm", "title": "z"}))
    assert ok == 1  # one failed, one succeeded — no exception raised


def test_disabled_when_no_webhooks():
    rt = _runtime(webhooks=None)
    assert rt.notifier.enabled is False
    rt.notifier.fire({"type": "alarm", "title": "x"})  # no-op, must not raise


# ── wiring: alarm trigger fans out ─────────────────────────────────────


def test_alarm_trigger_fires_notification():
    rt = _runtime(webhooks=["http://hook"], detections=[{"label": "fire"}])
    posts = []
    _capture_client(rt, posts)

    async def go():
        alarm = rt.alarms.create(name="Fire", target="fire", camera_ids=["cam1"])
        for _ in range(80):
            if posts:
                break
            await asyncio.sleep(0.02)
        rt.alarms.stop(alarm.id)
        assert posts and posts[0][1]["type"] == "alarm"
        assert "fire" in posts[0][1]["text"].lower()

    asyncio.run(go())


# ── endpoints ──────────────────────────────────────────────────────────


def test_notify_endpoints():
    rt = _runtime(webhooks=["http://hook"])
    posts = []
    _capture_client(rt, posts)
    client = TestClient(build_app(rt))

    status = client.get("/notify").json()
    assert status["enabled"] is True and status["channels"] == 1

    r = client.post("/notify/test")
    assert r.status_code == 200 and r.json()["delivered"] == 1
    assert posts and posts[0][1]["type"] == "test"


def test_notify_test_400_when_unconfigured():
    rt = _runtime(webhooks=None)
    client = TestClient(build_app(rt))
    assert client.post("/notify/test").status_code == 400

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The merged Events feed: alarms + watch notifications + relayed app
alerts, newest-first, plus the tap-to-open deep link on notifications."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import AlertRecord, CameraSpec


def _runtime(**cfg_extra):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front")],
        **cfg_extra,
    )
    return CameraAgentRuntime(cfg)


def _seed(rt):
    import time
    now = time.time()
    rt.alarms._events.append({"id": 1, "alarm_id": 1, "name": "After-hours",
                              "text": "person on cam1", "camera": "cam1",
                              "ts": now - 300, "emergency_contact": None})
    rt.monitors._notifications.append({"id": 1, "monitor_id": 1,
                                       "text": "Heads up — I see a car on cam1.",
                                       "camera": "cam1", "ts": now - 100})
    # NOTE: the app-alert stream is windowed (last 24 h) — seeds must be
    # recent or the feed correctly drops them.
    rt.context.record_app_alert(AlertRecord(
        received_at=now - 200, app_id="smart-doorbell", camera_id="cam1",
        title="Unknown visitor", severity="high", summary="stranger at the door"))
    return now


def test_feed_merges_three_streams_newest_first():
    rt = _runtime()
    now = _seed(rt)
    feed = rt.events_feed()
    assert [e["kind"] for e in feed] == ["watch", "app", "alarm"]   # -100, -200, -300
    assert [round(now - e["ts"]) for e in feed] == [100, 200, 300]
    app_ev = feed[1]
    assert app_ev["title"] == "Unknown visitor" and app_ev["severity"] == "high"
    assert app_ev["camera_id"] == "cam1" and app_ev["source"] == "smart-doorbell"
    assert feed[2]["severity"] == "critical"                        # alarms ring loud


def test_feed_caps_and_endpoint_shape():
    rt = _runtime()
    for i in range(60):
        rt.monitors._notifications.append(
            {"id": i, "monitor_id": 1, "text": f"n{i}", "camera": "cam1",
             "ts": 1000.0 + i})
    assert len(rt.events_feed()) == 50
    client = TestClient(build_app(rt))
    body = client.get("/events").json()
    assert body["events"][0]["ts"] == 1059.0


def test_notification_payload_carries_deep_link_when_configured():
    rt = _runtime(agent_public_url="https://agent.nvr.example/")
    payload = rt.notifier._format({"type": "alarm", "title": "Fire",
                                   "text": "fire", "camera": "cam1"})
    assert payload["link"] == "https://agent.nvr.example/demo/camera/cam1"
    # no camera → no link; no base → no link
    assert "link" not in rt.notifier._format({"type": "alarm", "title": "x"})
    rt2 = _runtime()
    assert "link" not in rt2.notifier._format(
        {"type": "alarm", "title": "x", "camera": "cam1"})

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The app door — ALERT RELAY (read side).

The OpenNVR Agent subscribes to alerts fired by installed catalog apps
(``opennvr.alerts.app.>`` on the bus) and surfaces them both
conversationally (the ``recent_app_alerts`` tool) and proactively (the
notification feed the demo polls + the Notifier webhook fan-out).

Read/relay only: the agent reports app alerts, it never acts on the app.

Covered here:

* ``_parse_app_alert``: a valid §11.5 envelope parses; non-alert / bad
  payloads are dropped.
* the alert ring is bounded and newest-first deterministic under equal
  timestamps (the seq tiebreak), mirroring the event-ring test.
* the ``recent_app_alerts`` tool: filter by app_id + window, graceful
  when the bus is unwired.
* the on_alert notification bridge: a relayed alert appears in the
  notification feed the UI polls.
* ``run_app_alert_subscriber`` is graceful when nats-py is absent.
* the read/relay boundary: no path acts on an app.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from camera_agent import AppConfig, CameraAgentRuntime
from context import (
    AlertRecord,
    CameraContext,
    _app_id_from_subject,
    _camera_from_alert_subject,
    _parse_app_alert,
    _summarise_app_alert,
    run_app_alert_subscriber,
)
from context import CameraSpec


def _make_ctx(ring_size: int = 32) -> CameraContext:
    spec = CameraSpec(camera_id="front-door", frame_url="http://x", role="entrance")
    return CameraContext(cameras=[spec], event_ring_size=ring_size)


def _envelope(
    *,
    title="PPE violation",
    description="worker without hard hat",
    severity="high",
    camera_id="front-door",
    app_name="ppe-detection",
) -> dict:
    return {
        "alert_id": "alrt_abc123",
        "fired_at": "2026-07-04T10:00:00+00:00",
        "title": title,
        "description": description,
        "severity": severity,
        "source": {"kind": "app", "name": app_name, "version": "1.0.0"},
        "camera_id": camera_id,
        "correlation_id": None,
        "evidence": {},
        "tags": ["ppe"],
    }


# ── subject helpers ────────────────────────────────────────────────────


def test_app_id_and_camera_from_subject():
    subj = "opennvr.alerts.app.ppe-detection.cam-front"
    assert _app_id_from_subject(subj) == "ppe-detection"
    assert _camera_from_alert_subject(subj) == "cam-front"
    # non-app subject → None
    assert _app_id_from_subject("opennvr.inference.yolov8.cam.completed") is None
    assert _camera_from_alert_subject("garbage") is None


def test_camera_from_subject_survives_extra_trailing_segment():
    # A future 5th token (e.g. track_id) must not lose the camera.
    subj = "opennvr.alerts.app.loitering-detection.cam-back.track-7"
    assert _app_id_from_subject(subj) == "loitering-detection"
    assert _camera_from_alert_subject(subj) == "cam-back.track-7"


# ── _parse_app_alert ───────────────────────────────────────────────────


def test_parse_app_alert_valid_envelope():
    rec = _parse_app_alert(
        "opennvr.alerts.app.ppe-detection.front-door", _envelope()
    )
    assert rec is not None
    assert rec.app_id == "ppe-detection"
    assert rec.camera_id == "front-door"
    assert rec.title == "PPE violation"
    assert rec.severity == "high"
    assert "hard hat" in rec.summary
    assert rec.raw["alert_id"] == "alrt_abc123"


def test_parse_app_alert_app_id_falls_back_to_subject():
    env = _envelope()
    env.pop("source")
    rec = _parse_app_alert("opennvr.alerts.app.loiter.cam-1", env)
    assert rec is not None
    assert rec.app_id == "loiter"


def test_parse_app_alert_camera_falls_back_to_subject():
    env = _envelope()
    env.pop("camera_id")
    rec = _parse_app_alert("opennvr.alerts.app.ppe.cam-9", env)
    assert rec is not None
    assert rec.camera_id == "cam-9"


def test_parse_app_alert_drops_non_alert_payloads():
    # No title → not an app alert (guards against inference/other bus traffic).
    assert _parse_app_alert("opennvr.alerts.app.x.cam", {"severity": "high"}) is None
    assert _parse_app_alert("opennvr.alerts.app.x.cam", {"title": "   "}) is None
    assert _parse_app_alert("opennvr.alerts.app.x.cam", []) is None  # type: ignore[arg-type]


def test_parse_app_alert_default_severity():
    env = _envelope()
    env.pop("severity")
    rec = _parse_app_alert("opennvr.alerts.app.ppe.cam", env)
    assert rec is not None
    assert rec.severity == "high"


def test_summarise_app_alert_prefers_description():
    assert "worker" in _summarise_app_alert(_envelope())
    # No description → title.
    env = _envelope(description="")
    assert _summarise_app_alert(env) == "PPE violation"


# ── alert ring ─────────────────────────────────────────────────────────


def _alert(app_id: str, seconds_ago: float, title: str, camera="front-door") -> AlertRecord:
    return AlertRecord(
        received_at=time.time() - seconds_ago,
        app_id=app_id,
        camera_id=camera,
        title=title,
        severity="high",
        summary=title,
    )


def test_alert_ring_filters_by_window():
    ctx = _make_ctx()
    ctx.record_app_alert(_alert("ppe", 5, "recent"))
    ctx.record_app_alert(_alert("ppe", 100, "old"))
    out = ctx.recent_app_alerts(app_id="ppe", window_seconds=30)
    assert [a.title for a in out] == ["recent"]


def test_alert_ring_newest_first():
    ctx = _make_ctx()
    ctx.record_app_alert(_alert("ppe", 30, "older"))
    ctx.record_app_alert(_alert("ppe", 1, "newer"))
    out = ctx.recent_app_alerts(app_id="ppe", window_seconds=60)
    assert [a.title for a in out] == ["newer", "older"]


def test_alert_ring_filter_by_app_id():
    ctx = _make_ctx()
    ctx.record_app_alert(_alert("ppe", 1, "ppe-alert"))
    ctx.record_app_alert(_alert("loiter", 1, "loiter-alert"))
    out = ctx.recent_app_alerts(app_id="ppe", window_seconds=60)
    assert [a.title for a in out] == ["ppe-alert"]


def test_alert_ring_none_means_all_apps():
    ctx = _make_ctx()
    ctx.record_app_alert(_alert("ppe", 1, "a"))
    ctx.record_app_alert(_alert("loiter", 2, "b"))
    out = ctx.recent_app_alerts(app_id=None, window_seconds=60)
    assert {a.title for a in out} == {"a", "b"}


def test_alert_ring_bounded():
    ctx = _make_ctx(ring_size=3)
    for i in range(10):
        ctx.record_app_alert(_alert("ppe", 0, f"a{i}"))
    out = ctx.recent_app_alerts(app_id="ppe", window_seconds=60)
    assert [a.title for a in out] == ["a9", "a8", "a7"]


def test_alert_ring_deterministic_under_equal_timestamps():
    """Equal received_at across a burst → the monotonic seq tiebreak keeps
    the order deterministic newest-first (last inserted first). Mirrors the
    event-ring determinism guarantee."""
    ctx = _make_ctx()
    fixed = time.time()
    for i in range(6):
        ctx.record_app_alert(
            AlertRecord(
                received_at=fixed,  # identical timestamps
                app_id="ppe",
                camera_id="front-door",
                title=f"a{i}",
                severity="high",
                summary=f"a{i}",
            )
        )
    out = ctx.recent_app_alerts(app_id="ppe", window_seconds=60)
    assert [a.title for a in out] == ["a5", "a4", "a3", "a2", "a1", "a0"]


def test_alert_ring_unknown_app_returns_empty():
    ctx = _make_ctx()
    assert ctx.recent_app_alerts(app_id="nope", window_seconds=60) == []


# ── recent_app_alerts tool + notification bridge ───────────────────────


def _runtime(*, nats_url=None):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        nats_inference_url=nats_url,
        cameras=[CameraSpec(camera_id="front-door", frame_url="http://x/1.jpg", role="front")],
    )
    return CameraAgentRuntime(cfg)


def test_recent_app_alerts_tool_filters_and_windows():
    rt = _runtime(nats_url="nats://x")
    rt.context.record_app_alert(_alert("ppe", 5, "PPE violation"))
    rt.context.record_app_alert(_alert("loiter", 5, "loitering"))
    rt.context.record_app_alert(_alert("ppe", 10_000, "stale"))

    # filter by app_id + default window (1h) drops the stale one
    out = asyncio.run(rt._handle_recent_app_alerts({"app_id": "ppe"}))
    assert "PPE violation" in out
    assert "stale" not in out
    assert "loitering" not in out
    assert "app:ppe" in out

    # no filter → both apps in-window
    out_all = asyncio.run(rt._handle_recent_app_alerts({"window_seconds": 3600}))
    assert "PPE violation" in out_all and "loitering" in out_all


def test_recent_app_alerts_tool_graceful_when_empty():
    rt = _runtime(nats_url="nats://x")
    out = asyncio.run(rt._handle_recent_app_alerts({"window_seconds": 60}))
    assert "No alerts" in out


def test_recent_app_alerts_tool_graceful_when_bus_unwired():
    rt = _runtime(nats_url=None)
    out = asyncio.run(rt._handle_recent_app_alerts({}))
    assert "alert bus isn't configured" in out


def test_recent_app_alerts_tool_rejects_bad_window():
    rt = _runtime(nats_url="nats://x")
    assert "must be a number" in asyncio.run(
        rt._handle_recent_app_alerts({"window_seconds": "soon"})
    )
    assert "must be positive" in asyncio.run(
        rt._handle_recent_app_alerts({"window_seconds": -1})
    )


def test_relay_bridge_pushes_alert_into_notification_feed():
    """The on_alert bridge: a relayed app alert appears in the SAME
    notification feed the demo polls, labelled app:<id>."""
    rt = _runtime(nats_url="nats://x")
    rec = _alert("ppe-detection", 0, "PPE violation")
    rt._relay_app_alert(rec)

    notes = rt.monitors.notifications()
    assert len(notes) == 1
    assert notes[0]["source"] == "app:ppe-detection"
    assert "PPE violation" in notes[0]["text"]


def test_relay_bridge_fires_webhook_fanout():
    """The relay also fans out via the Notifier webhook path (labelled
    source app:<id>, severity carried through)."""
    rt = _runtime(nats_url="nats://x")
    rt.notifier._webhooks = ["http://hook"]
    posts: list = []

    class _Client:
        async def post(self, url, json=None):
            posts.append((url, json))

            class _R:
                status_code = 200
            return _R()

    rt.notifier._client = _Client()

    async def _drive():
        rt._relay_app_alert(_alert("ppe-detection", 0, "PPE violation"))
        # let the fire-and-forget task run
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_drive())
    assert posts, "expected a webhook delivery"
    body = posts[0][1]
    assert body["source"] == "app:ppe-detection"
    assert body["severity"] == "high"
    assert "PPE violation" in body["title"]


# ── subscriber wiring path (end-to-end via a fake NATS) ────────────────


def test_subscriber_records_and_bridges_via_fake_nats(monkeypatch):
    """Drive run_app_alert_subscriber with a fake nats module: a published
    §11.5 envelope on opennvr.alerts.app.> lands in the ring AND invokes
    on_alert. Confirms the subject subscribed + both relay legs."""
    import sys
    import types

    captured = {}

    class _FakeMsg:
        def __init__(self, subject, data):
            self.subject = subject
            self.data = data

    class _FakeSub:
        async def unsubscribe(self):
            pass

    class _FakeNC:
        async def subscribe(self, subject, cb=None):
            captured["subject"] = subject
            captured["cb"] = cb
            return _FakeSub()

        async def drain(self):
            pass

    async def _connect(**kw):
        captured["connect_kwargs"] = kw
        return _FakeNC()

    fake_nats = types.SimpleNamespace(connect=_connect)
    monkeypatch.setitem(sys.modules, "nats", fake_nats)

    ctx = _make_ctx()
    got: list = []
    stop = asyncio.Event()

    import json

    async def _drive():
        task = asyncio.create_task(
            run_app_alert_subscriber(
                context=ctx, nats_url="nats://x", nats_token="tok",
                stop_event=stop, on_alert=got.append,
            )
        )
        # let it connect + subscribe
        for _ in range(5):
            await asyncio.sleep(0)
            if "cb" in captured:
                break
        # deliver an envelope
        await captured["cb"](
            _FakeMsg(
                "opennvr.alerts.app.ppe-detection.front-door",
                json.dumps(_envelope()).encode("utf-8"),
            )
        )
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_drive())

    assert captured["subject"] == "opennvr.alerts.app.>"
    assert captured["connect_kwargs"]["token"] == "tok"
    # recorded in the ring
    out = ctx.recent_app_alerts(app_id="ppe-detection", window_seconds=60)
    assert len(out) == 1 and out[0].title == "PPE violation"
    # bridged to on_alert
    assert len(got) == 1 and got[0].app_id == "ppe-detection"


def test_subscriber_graceful_when_nats_absent(monkeypatch):
    """nats-py not installed → the subscriber just waits on stop_event and
    returns cleanly (the tool stays empty). Never crashes the agent."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "nats":
            raise ImportError("no nats-py")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    ctx = _make_ctx()
    stop = asyncio.Event()

    async def _drive():
        task = asyncio.create_task(
            run_app_alert_subscriber(
                context=ctx, nats_url="nats://x", nats_token=None,
                stop_event=stop, on_alert=None,
            )
        )
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_drive())  # returns without raising


def test_subscriber_swallows_on_alert_callback_errors(monkeypatch):
    """A throwing on_alert callback must not crash the subscriber — the
    alert is still recorded in the ring."""
    import sys
    import types
    import json

    captured = {}

    class _FakeMsg:
        def __init__(self, subject, data):
            self.subject, self.data = subject, data

    class _FakeSub:
        async def unsubscribe(self):
            pass

    class _FakeNC:
        async def subscribe(self, subject, cb=None):
            captured["cb"] = cb
            return _FakeSub()

        async def drain(self):
            pass

    async def _connect(**kw):
        return _FakeNC()

    monkeypatch.setitem(sys.modules, "nats", types.SimpleNamespace(connect=_connect))

    ctx = _make_ctx()
    stop = asyncio.Event()

    def _boom(_alert):
        raise RuntimeError("bridge exploded")

    async def _drive():
        task = asyncio.create_task(
            run_app_alert_subscriber(
                context=ctx, nats_url="nats://x", nats_token=None,
                stop_event=stop, on_alert=_boom,
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
            if "cb" in captured:
                break
        await captured["cb"](
            _FakeMsg(
                "opennvr.alerts.app.ppe-detection.front-door",
                json.dumps(_envelope()).encode("utf-8"),
            )
        )
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_drive())
    # despite the callback blowing up, the alert was recorded.
    assert len(ctx.recent_app_alerts(app_id="ppe-detection", window_seconds=60)) == 1


# ── read/relay boundary ────────────────────────────────────────────────


def test_no_app_action_path_exists():
    """The relay is read-only: no tool/handler acts on an app (arm, silence,
    ack, reconfigure). Only the read/relay tools exist."""
    rt = _runtime(nats_url="nats://x")
    names = {t["function"]["name"] for t in rt.tool_definitions}
    for forbidden in (
        "silence_app", "ack_app_alert", "arm_app", "disable_app",
        "configure_app", "acknowledge_alert",
    ):
        assert forbidden not in names
        assert forbidden not in rt.tool_handlers
    # recent_app_alerts is the only new app-alert tool, and it's read-only.
    assert "recent_app_alerts" in rt.tool_handlers

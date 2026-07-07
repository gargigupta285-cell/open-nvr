# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Camera strip + ask-about-this-frame: the GET /frame/{id} thumbnail
endpoint, context frame pinning, and the /ask pinned-frame turn."""
from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec
from frame_sources import FrameSourceError

JPEG = b"\xff\xd8\xff\xe0FAKEJPEG"


def _runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front"),
                 CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="gate")],
    )
    return CameraAgentRuntime(cfg)


class _Source:
    def __init__(self, jpeg=JPEG, fail=False):
        self.jpeg, self.fail = jpeg, fail
    def fetch(self):
        if self.fail:
            raise FrameSourceError("camera off")
        return self.jpeg


# ── context pinning ────────────────────────────────────────────────────


def test_pin_frame_bypasses_fetch_and_ttl():
    rt = _runtime()   # no frame sources registered at all
    rt.context.pin_frame("cam1", JPEG)
    assert asyncio.run(rt.context.get_frame("cam1")) == JPEG
    assert rt.context.get_cached_frame("cam1") == JPEG   # chat shows the pin
    rt.context.clear_pins()
    # pin gone; nothing cached either — pins deliberately never touch the
    # shared frame cache (autonomous callers read it).
    assert rt.context._pinned == {}
    assert rt.context.get_cached_frame("cam1") is None


def test_pin_frame_rejects_unknown_camera():
    rt = _runtime()
    with pytest.raises(LookupError):
        rt.context.pin_frame("nope", JPEG)


# ── GET /frame/{camera_id} ─────────────────────────────────────────────


def test_frame_endpoint_serves_current_jpeg():
    rt = _runtime()
    rt.context.register_frame_source("cam1", _Source())
    client = TestClient(build_app(rt))
    r = client.get("/frame/cam1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.content == JPEG


def test_frame_endpoint_404_unknown_503_unreachable():
    rt = _runtime()
    rt.context.register_frame_source("cam1", _Source(fail=True))
    client = TestClient(build_app(rt))
    assert client.get("/frame/nope").status_code == 404
    assert client.get("/frame/cam1").status_code == 503


# ── /ask with a pinned frame ───────────────────────────────────────────


def test_ask_pinned_frame_visible_during_turn_and_cleared_after(monkeypatch):
    rt = _runtime()
    seen = {}

    async def fake_turn(runtime, history, text, *, preferred_camera=None, **kw):
        seen["frame"] = await runtime.context.get_frame("cam1")
        seen["camera"] = preferred_camera
        runtime.tools.last_cameras_used = ["cam1"]
        return "ok"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))
    r = client.post("/ask", json={"text": "what is this?", "camera": "cam1",
                                  "pinned_jpeg_b64": base64.b64encode(JPEG).decode()})
    assert r.status_code == 200
    assert seen["frame"] == JPEG and seen["camera"] == "cam1"
    # the reply's frames block carries the SAME pinned frame ("here's what I saw")
    frames = r.json()["frames"]
    assert frames and base64.b64decode(frames[0]["jpeg_b64"]) == JPEG
    # cleared after the turn — the next /ask invalidates the cache first,
    # so a following live question can't reuse the pinned frame.
    assert rt.context._pinned == {}


def test_ask_pinned_frame_validation(monkeypatch):
    rt = _runtime()

    async def fake_turn(runtime, history, text, **kw):  # pragma: no cover
        return "ok"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))
    bad = client.post("/ask", json={"text": "x", "camera": "cam1",
                                    "pinned_jpeg_b64": "!!!not-base64!!!"})
    assert bad.status_code == 400
    huge = client.post("/ask", json={"text": "x", "camera": "cam1",
                                     "pinned_jpeg_b64": base64.b64encode(b"j" * 2_000_001).decode()})
    assert huge.status_code == 400


# ── /agent deep-link base ──────────────────────────────────────────────


def test_agent_exposes_opennvr_ui_url():
    rt = _runtime()
    client = TestClient(build_app(rt))
    assert client.get("/agent").json()["opennvr_ui_url"] == ""
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    opennvr_ui_url="https://nvr.example",
                    cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")])
    client2 = TestClient(build_app(CameraAgentRuntime(cfg)))
    assert client2.get("/agent").json()["opennvr_ui_url"] == "https://nvr.example"


# ── review ring + timeline (per-camera scrub-back) ─────────────────────


def test_review_ring_fills_on_real_fetches_only():
    rt = _runtime()
    rt.context.register_frame_source("cam1", _Source())
    # two real fetches (cache expires in between), one cache hit, one pin
    async def go():
        await rt.context.get_frame("cam1")
        await rt.context.get_frame("cam1")            # cache hit — no ring entry
        rt.context.invalidate_frame_cache()
        await rt.context.get_frame("cam1")            # real fetch #2
    asyncio.run(go())
    rt.context.pin_frame("cam1", b"PINNED")           # pins don't feed the ring
    ts = rt.context.review_timestamps("cam1")
    assert len(ts) == 2 and ts == sorted(ts)
    assert rt.context.review_frame_at("cam1", ts[-1]) == JPEG
    assert rt.context.review_frame_at("cam2", 0.0) is None   # empty ring


def test_frame_at_serves_ring_and_timeline_shape():
    rt = _runtime()
    rt.context.register_frame_source("cam1", _Source())
    asyncio.run(rt.context.get_frame("cam1"))
    client = TestClient(build_app(rt))
    ts = rt.context.review_timestamps("cam1")[0]
    r = client.get(f"/frame/cam1?at={ts}")
    assert r.status_code == 200 and r.content == JPEG
    assert client.get("/frame/cam1?at=1.0").status_code == 200   # nearest match
    assert client.get("/frame/cam2?at=1.0").status_code == 404   # empty ring
    tl = client.get("/timeline/cam1").json()
    assert tl["camera_id"] == "cam1" and tl["frames"] == rt.context.review_timestamps("cam1")
    assert isinstance(tl["events"], list)
    assert client.get("/timeline/nope").status_code == 404


def test_demo_camera_deep_link_route():
    rt = _runtime()
    client = TestClient(build_app(rt))
    ok = client.get("/demo/camera/cam1")
    assert ok.status_code == 200 and "camScreen" in ok.text
    assert client.get("/demo/camera/nope").status_code == 404


# ── review fixes: pins vs autonomous pollers, tokens, ring bounds ──────


def test_autonomous_callers_bypass_pins():
    """An operator pin must never reach alarm/monitor polls or the live
    thumbnail endpoint — only the conversation path sees it."""
    rt = _runtime()
    rt.context.register_frame_source("cam1", _Source(jpeg=JPEG))
    rt.context.pin_frame("cam1", b"HISTORICAL")
    # conversation path (default) sees the pin
    assert asyncio.run(rt.context.get_frame("cam1")) == b"HISTORICAL"
    # autonomous path sees the REAL frame
    assert asyncio.run(rt.context.get_frame("cam1", allow_pinned=False)) == JPEG
    # the live /frame endpoint rides the autonomous path
    client = TestClient(build_app(rt))
    assert client.get("/frame/cam1").content == JPEG


def test_pin_tokens_scope_cleanup_to_owner():
    rt = _runtime()
    t1 = rt.context.pin_frame("cam1", b"A")
    t2 = rt.context.pin_frame("cam2", b"B")
    rt.context.clear_pins(t1)              # only cam1's pin goes
    assert rt.context.get_cached_frame("cam2") == b"B"
    assert asyncio.run(rt.context.get_frame("cam2")) == b"B"
    rt.context.clear_pins(t2)
    rt.context.clear_pins()                # no-arg still clears everything


def test_ask_pin_without_camera_400s(monkeypatch):
    rt = _runtime()

    async def fake_turn(runtime, history, text, **kw):  # pragma: no cover
        return "ok"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))
    r = client.post("/ask", json={"text": "x",
                                  "pinned_jpeg_b64": base64.b64encode(JPEG).decode()})
    assert r.status_code == 400
    assert "camera" in r.json()["error"]


def test_review_ring_respects_byte_budget_and_frame_cap():
    rt = _runtime()
    rt.context._review_byte_budget = 25          # tiny budget for the test
    src = _Source(jpeg=b"0123456789")            # 10 bytes/frame
    rt.context.register_frame_source("cam1", src)

    async def fetch():
        rt.context.invalidate_frame_cache()
        await rt.context.get_frame("cam1")

    for _ in range(5):
        asyncio.run(fetch())
    # 5 × 10B fetched, budget 25B → only the newest 2 frames survive
    assert len(rt.context.review_timestamps("cam1")) == 2
    assert rt.context._review_bytes["cam1"] == 20
    # an oversized frame is skipped entirely
    rt.context._review_frame_cap = 5
    asyncio.run(fetch())
    assert len(rt.context.review_timestamps("cam1")) == 2

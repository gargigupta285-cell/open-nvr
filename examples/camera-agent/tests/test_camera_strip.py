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
    # pin gone; the seeded cache entry remains (the turn's "what I saw"),
    # and the next /ask invalidates it before fetching live.
    assert rt.context._pinned == {}
    assert rt.context.get_cached_frame("cam1") == JPEG


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

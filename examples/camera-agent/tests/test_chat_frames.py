# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The chat shows the frame the agent looked at: /converse and /ask return the
JPEG(s) the vision tools fetched this turn (base64), read from the per-turn
frame cache — so the UI can render 'here's what I saw'."""
from __future__ import annotations

import base64

from camera_agent import AppConfig, CameraAgentRuntime, _frames_for
from context import CameraSpec


def _runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[
            CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front door"),
            CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="back yard"),
        ],
    )
    return CameraAgentRuntime(cfg)


def test_frames_for_returns_cached_jpeg_with_role(monkeypatch):
    rt = _runtime()
    rt.tools.last_cameras_used = ["cam1", "cam1"]   # duplicate → de-duped
    monkeypatch.setattr(rt.context, "get_cached_frame",
                        lambda cid: b"JPEGBYTES" if cid == "cam1" else None)
    frames = _frames_for(rt)
    assert len(frames) == 1
    assert frames[0]["camera_id"] == "cam1"
    assert frames[0]["role"] == "front door"
    assert base64.b64decode(frames[0]["jpeg_b64"]) == b"JPEGBYTES"


def test_frames_for_empty_when_nothing_cached(monkeypatch):
    rt = _runtime()
    rt.tools.last_cameras_used = ["cam1"]
    monkeypatch.setattr(rt.context, "get_cached_frame", lambda cid: None)
    assert _frames_for(rt) == []


def test_frames_for_skips_oversized(monkeypatch):
    rt = _runtime()
    rt.tools.last_cameras_used = ["cam1"]
    monkeypatch.setattr(rt.context, "get_cached_frame", lambda cid: b"x" * 2_000_001)
    assert _frames_for(rt) == []


def test_frames_for_caps_count(monkeypatch):
    rt = _runtime()
    rt.tools.last_cameras_used = ["cam1", "cam2"]
    monkeypatch.setattr(rt.context, "get_cached_frame", lambda cid: b"J")
    assert len(_frames_for(rt, max_frames=1)) == 1

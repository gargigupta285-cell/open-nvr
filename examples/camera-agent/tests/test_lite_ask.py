# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Lite text-mode agent: the POST /ask path (no STT/TTS), text_mode config,
and /agent reporting the mode (issue #82 — the fast, low-resource on-ramp)."""
from __future__ import annotations

import camera_agent as ca
from fastapi.testclient import TestClient

from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec


def _runtime(text_mode=True):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t", text_mode=text_mode,
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front"),
                 CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="gate")],
    )
    return CameraAgentRuntime(cfg)


def test_agent_reports_text_mode():
    client = TestClient(build_app(_runtime(text_mode=True)))
    assert client.get("/agent").json()["text_mode"] is True
    client2 = TestClient(build_app(_runtime(text_mode=False)))
    assert client2.get("/agent").json()["text_mode"] is False


def test_ask_runs_a_text_turn(monkeypatch):
    rt = _runtime()

    async def fake_turn(runtime, history, text, *, preferred_camera=None, **kw):
        runtime.tools.last_cameras_used = ["cam1"]
        return f"answer: {text} (cam={preferred_camera})"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))

    r = client.post("/ask", json={"text": "how many people on cam2?", "camera": "cam2"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "answer: how many people on cam2? (cam=cam2)"
    assert data["cameras_used"] == ["cam1"]
    assert "latency_ms" in data


def test_ask_requires_text():
    client = TestClient(build_app(_runtime()))
    assert client.post("/ask", json={}).status_code == 400
    assert client.post("/ask", json={"text": "   "}).status_code == 400


def test_ask_ignores_bogus_camera_hint(monkeypatch):
    rt = _runtime()
    seen = {}

    async def fake_turn(runtime, history, text, *, preferred_camera=None, **kw):
        seen["preferred"] = preferred_camera
        return "ok"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))
    client.post("/ask", json={"text": "hi", "camera": "nope"})
    assert seen["preferred"] is None     # unknown camera ignored
    client.post("/ask", json={"text": "hi", "camera": "cam2"})
    assert seen["preferred"] == "cam2"


def test_ask_persists_history(monkeypatch):
    rt = _runtime()

    async def fake_turn(runtime, history, text, *, preferred_camera=None, **kw):
        return "reply-" + text

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))
    client.post("/ask", json={"text": "first"})
    # second turn should see the first in history (proves it's threaded)
    captured = {}

    async def fake_turn2(runtime, history, text, *, preferred_camera=None, **kw):
        captured["history_len"] = len(history)
        return "ok"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn2)
    client.post("/ask", json={"text": "second"})
    assert captured["history_len"] >= 2  # user+assistant from the first turn

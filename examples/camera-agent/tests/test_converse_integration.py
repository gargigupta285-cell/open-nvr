# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""End-to-end HTTP integration tests for the camera-agent server.

Drives the real FastAPI request path through a TestClient — including the
real ffmpeg transcode in /converse — with the model clients (Whisper STT,
Ollama LLM, Piper TTS) and the vision tools stubbed. This exercises the
production turn: audio in → transcode → STT → tool-calling loop (incl.
camera hint + forced grounding) → TTS → JSON out.

Requires the ``ffmpeg`` binary (shipped in the camera-agent image).
"""
from __future__ import annotations

import base64
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available"
)


def _wav_blob(seconds: float = 0.4) -> bytes:
    """A real (silent-ish) WAV so the actual ffmpeg transcode path runs."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}",
         "-ac", "1", "-ar", "44100", "-f", "wav", "pipe:1"],
        capture_output=True,
    )
    assert proc.returncode == 0 and proc.stdout, proc.stderr
    return proc.stdout


@pytest.fixture
def harness(monkeypatch):
    cfg = AppConfig(
        kaic_url="http://kaic", kaic_api_key="k",
        whisper_url="http://w", ollama_url="http://o", piper_url="http://p",
        system_prompt="test",
        # These tests exercise the STT→LLM→tool→TTS pipeline, not the wake-word
        # gate (which has its own tests). Turn it off so a plain transcript is
        # answered; the gate is tested explicitly with ?wake=1 below.
        wake_word_required=False,
        cameras=[
            CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front door"),
            CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="back yard"),
        ],
    )
    rt = CameraAgentRuntime(cfg)

    state = {"transcript": "what do you see", "detect_args": None,
             "chat": None}

    async def fake_transcribe(_wav_bytes):
        return state["transcript"]

    async def fake_synth(_text):
        return b"FAKEWAVBYTES"

    async def fake_detect(args):
        state["detect_args"] = args
        return "1 person"

    monkeypatch.setattr(rt.whisper, "transcribe", fake_transcribe)
    monkeypatch.setattr(rt.piper, "synthesize", fake_synth)
    rt.tool_handlers["detect_objects"] = fake_detect

    def set_chat(fn):
        monkeypatch.setattr(rt.ollama, "chat", fn)
    state["set_chat"] = set_chat

    # No context manager → skip startup() (NATS + LLM prewarm) which we
    # don't need for endpoint tests.
    client = TestClient(build_app(rt))
    return client, state


# ── basic endpoints ───────────────────────────────────────────────────


def test_health_ok(harness):
    client, _ = harness
    assert client.get("/health").status_code == 200


def test_cameras_lists_configured(harness):
    client, _ = harness
    body = client.get("/cameras").json()
    ids = [c["camera_id"] for c in body["cameras"]]
    assert ids == ["cam1", "cam2"]
    assert body["cameras"][0]["role"] == "front door"


def test_reset_ok(harness):
    client, _ = harness
    assert client.post("/reset").json()["status"] == "ok"


def test_converse_empty_body_400(harness):
    client, _ = harness
    assert client.post("/converse", content=b"").status_code == 400


# ── full turn: model invokes a tool, then answers ──────────────────────


def test_converse_full_tool_turn(harness):
    client, state = harness

    async def chat(*, messages, tools=None, temperature=0.4, max_tokens=256, **kw):
        # Second pass (tool result present) → final answer.
        if any(m.get("role") == "tool" for m in messages):
            return {"message": {"role": "assistant", "content": "I see one person."}}
        # First pass → invoke detect_objects on cam1.
        return {"message": {"role": "assistant", "content": "",
                            "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {"name": "detect_objects",
                                                         "arguments": {"camera_id": "cam1"}}}]}}
    state["set_chat"](chat)

    resp = client.post("/converse", content=_wav_blob())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["transcript"] == "what do you see"
    assert data["reply"] == "I see one person."
    assert data["audio_b64"] == base64.b64encode(b"FAKEWAVBYTES").decode()
    assert state["detect_args"] == {"camera_id": "cam1"}
    # per-phase timing breakdown is reported for the latency harness
    assert set(data["timings_ms"]) >= {"transcode", "stt", "llm", "tts", "total"}


# ── camera dropdown hint drives the forced-grounding default ───────────


def test_converse_camera_hint_selects_camera(harness):
    client, state = harness
    # A detection-style question so forced grounding picks detect_objects
    # (vs describe_camera for open-ended "what do you see").
    state["transcript"] = "how many people are there"

    # Model answers WITHOUT calling a tool → anti-fabrication forces a
    # detection. With ?camera=cam2 the forced detection must target cam2.
    async def chat(*, messages, tools=None, temperature=0.4, max_tokens=256, **kw):
        if any(m.get("role") == "tool" for m in messages):
            return {"message": {"role": "assistant", "content": "Grounded answer."}}
        return {"message": {"role": "assistant", "content": "There is a dog."}}
    state["set_chat"](chat)

    resp = client.post("/converse?camera=cam2", content=_wav_blob())
    assert resp.status_code == 200, resp.text
    assert state["detect_args"] == {"camera_id": "cam2"}


def test_converse_bogus_camera_hint_falls_back(harness):
    client, state = harness
    state["transcript"] = "how many people are there"

    async def chat(*, messages, tools=None, temperature=0.4, max_tokens=256, **kw):
        if any(m.get("role") == "tool" for m in messages):
            return {"message": {"role": "assistant", "content": "Grounded."}}
        return {"message": {"role": "assistant", "content": "There is a cat."}}
    state["set_chat"](chat)

    # Unknown camera id is ignored → falls back to first camera (cam1).
    resp = client.post("/converse?camera=does-not-exist", content=_wav_blob())
    assert resp.status_code == 200, resp.text
    assert state["detect_args"] == {"camera_id": "cam1"}


# ── empty transcript short-circuits before the LLM ─────────────────────


def test_converse_empty_transcript_returns_blank(harness):
    client, state = harness
    state["transcript"] = ""   # Whisper found no speech

    called = {"chat": False}

    async def chat(**kw):
        called["chat"] = True
        return {"message": {"role": "assistant", "content": "should not run"}}
    state["set_chat"](chat)

    data = client.post("/converse", content=_wav_blob()).json()
    assert data["transcript"] == "" and data["reply"] == "" and data["audio_b64"] is None
    assert called["chat"] is False


# ── wake-word gate (voice): only answer when addressed by name ─────────


def test_converse_wake_gate_ignores_unaddressed(harness):
    client, state = harness
    state["transcript"] = "what do you see"   # no wake word

    called = {"chat": False}

    async def chat(**kw):
        called["chat"] = True
        return {"message": {"role": "assistant", "content": "should not run"}}
    state["set_chat"](chat)

    # ?wake=1 forces the gate on for this request (harness default is off).
    data = client.post("/converse?wake=1", content=_wav_blob()).json()
    assert data["invoked"] is False
    assert data["reply"] == "" and data["audio_b64"] is None
    assert data["transcript"] == "what do you see"   # still echoed for the UI
    assert called["chat"] is False                   # LLM never spent


def test_converse_wake_gate_answers_when_addressed(harness):
    client, state = harness
    # Sidhu is the default persona (AppConfig voice_gender defaults to 'male').
    state["transcript"] = "hey Sidhu, what do you see"

    async def chat(*, messages, tools=None, temperature=0.4, max_tokens=256, **kw):
        # The wake phrase must be stripped before the model sees the question.
        user = [m for m in messages if m.get("role") == "user"][-1]["content"].lower()
        assert "sidhu" not in user and user.strip() == "what do you see"
        return {"message": {"role": "assistant", "content": "I see the front door."}}
    state["set_chat"](chat)

    data = client.post("/converse?wake=1", content=_wav_blob()).json()
    assert data["invoked"] is True
    assert "front door" in data["reply"]


def test_converse_bare_wake_word_acks_without_llm(harness):
    client, state = harness
    state["transcript"] = "Hey Sidhu"   # just the name, no question

    called = {"chat": False}

    async def chat(**kw):
        called["chat"] = True
        return {"message": {"role": "assistant", "content": "x"}}
    state["set_chat"](chat)

    data = client.post("/converse?wake=1", content=_wav_blob()).json()
    assert data["invoked"] is True
    assert data["reply"]                 # a spoken acknowledgement
    assert called["chat"] is False       # but no LLM spend

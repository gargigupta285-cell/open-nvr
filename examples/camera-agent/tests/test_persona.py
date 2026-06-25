# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the agent persona (Sidhu / Shailaja), the spoken intro, and the
background task system."""
from __future__ import annotations

import asyncio
import base64

from fastapi.testclient import TestClient

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec


def _runtime() -> CameraAgentRuntime:
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="be concise",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front door")],
    )
    return CameraAgentRuntime(cfg)


# ── persona ────────────────────────────────────────────────────────────


def test_system_prompt_names_agent_and_describes_tasks():
    prompt = _runtime().build_system_prompt()
    assert "Sidhu" in prompt  # default voice_gender=male
    assert "create_background_task" in prompt


def test_agent_name_follows_voice_gender():
    assert ca.agent_name_for("female") == "Shailaja"
    assert ca.agent_name_for("male") == "Sidhu"
    assert ca.agent_name_for(None) == "Sidhu"  # default voice_gender=male
    assert "Shailaja" in ca.greeting_for("Shailaja")


def test_male_voice_names_sidhu():
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    voice_gender="male",
                    cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="r")])
    rt = CameraAgentRuntime(cfg)
    assert rt.agent_name == "Sidhu"
    assert "Sidhu" in rt.build_system_prompt()


# ── tool wiring: create_background_task is foreground-only ─────────────


def test_create_task_tool_foreground_only():
    rt = _runtime()
    fg = [t["function"]["name"] for t in rt.tool_definitions]
    bg = [t["function"]["name"] for t in rt.background_tool_definitions]
    assert "create_background_task" in fg
    assert "create_background_task" not in bg  # a task must not spawn tasks


# ── task manager lifecycle ─────────────────────────────────────────────


def test_task_runs_to_completion(monkeypatch):
    rt = _runtime()

    async def fake_turn(runtime, history, query, *, tool_definitions=None, **kw):
        # background turns must NOT be offered the create_background_task tool
        names = [t["function"]["name"] for t in (tool_definitions or [])]
        assert "create_background_task" not in names
        return f"answer for: {query}"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)

    async def go():
        task = rt.tasks.create("person in red shirt at 3am two days ago")
        assert task.status in ("queued", "running")
        for _ in range(100):
            if rt.tasks.get(task.id).status in ("done", "error"):
                break
            await asyncio.sleep(0.02)
        t = rt.tasks.get(task.id)
        assert t.status == "done"
        assert t.result == "answer for: person in red shirt at 3am two days ago"

    asyncio.run(go())


def test_create_task_handler_acks_with_id(monkeypatch):
    rt = _runtime()

    async def fake_turn(*a, **k):
        return "x"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)

    async def go():
        msg = await rt._handle_create_task({"query": "check the back door yesterday"})
        assert "background task #" in msg
        assert len(rt.tasks.list()) == 1

    asyncio.run(go())


def test_create_task_handler_rejects_empty():
    rt = _runtime()
    msg = asyncio.run(rt._handle_create_task({"query": ""}))
    assert "more detail" in msg
    assert rt.tasks.list() == []


# ── endpoints ──────────────────────────────────────────────────────────


def test_intro_endpoint_returns_text_and_audio(monkeypatch):
    rt = _runtime()

    async def fake_synth(_text):
        return b"WAVDATA"

    monkeypatch.setattr(rt.piper, "synthesize", fake_synth)
    client = TestClient(build_app(rt))
    data = client.get("/intro").json()
    assert data["name"] == "Sidhu"
    assert "Sidhu" in data["text"]
    assert data["audio_b64"] == base64.b64encode(b"WAVDATA").decode()


def test_intro_endpoint_text_only_when_tts_down(monkeypatch):
    rt = _runtime()

    async def boom(_text):
        raise RuntimeError("piper down")

    monkeypatch.setattr(rt.piper, "synthesize", boom)
    client = TestClient(build_app(rt))
    data = client.get("/intro").json()
    assert data["audio_b64"] is None
    assert "Sidhu" in data["text"]


def test_tasks_endpoints(monkeypatch):
    rt = _runtime()

    async def fake_turn(*a, **k):
        return "result"

    monkeypatch.setattr(ca, "_run_conversation_turn", fake_turn)
    client = TestClient(build_app(rt))

    r = client.post("/tasks", json={"query": "find the red truck earlier"})
    assert r.status_code == 202
    tid = r.json()["id"]

    listed = client.get("/tasks").json()["tasks"]
    assert any(t["id"] == tid for t in listed)

    assert client.post("/tasks", json={}).status_code == 400


def test_say_endpoint_synthesizes_text(monkeypatch):
    rt = _runtime()
    seen = {}

    async def fake_synth(text):
        seen["text"] = text
        return b"SPOKEN"

    monkeypatch.setattr(rt.piper, "synthesize", fake_synth)
    client = TestClient(build_app(rt))

    r = client.post("/say", json={"text": "Done with task #1: found a red truck."})
    assert r.status_code == 200
    assert r.json()["audio_b64"] == base64.b64encode(b"SPOKEN").decode()
    assert "red truck" in seen["text"]

    assert client.post("/say", json={"text": ""}).status_code == 400


def test_say_endpoint_text_only_when_tts_down(monkeypatch):
    rt = _runtime()

    async def boom(_text):
        raise RuntimeError("piper down")

    monkeypatch.setattr(rt.piper, "synthesize", boom)
    client = TestClient(build_app(rt))
    r = client.post("/say", json={"text": "hello"})
    assert r.status_code == 200 and r.json()["audio_b64"] is None

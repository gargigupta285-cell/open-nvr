# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Testing-team bug: the agent ALWAYS replied "Sorry, I'm having trouble…"
because the model returned empty content on the compose turn AFTER a tool ran.
The turn loop must surface the tool result instead of apologising. Also checks
the OllamaClient sends Ollama's native think:false to disable Qwen3 thinking."""
from __future__ import annotations

import asyncio

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


def _demo_runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t", text_mode=True,
        synthetic_detection=True,
        cameras=[CameraSpec(camera_id="front_door", frame_url="synth:people=2", role="door")],
    )
    return CameraAgentRuntime(cfg)


class _FakeOllama:
    """iter0 → calls detect_objects; iter1 → empty content (the bug)."""
    def __init__(self):
        self._responses = [
            {"message": {"content": "", "tool_calls": [
                {"id": "1", "type": "function",
                 "function": {"name": "detect_objects",
                              "arguments": {"camera_id": "front_door"}}}]}},
            {"message": {"content": "", "tool_calls": []}},
        ]
        self.calls = 0

    async def chat(self, **kw):
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return r

    async def aclose(self):
        return None


def test_empty_reply_after_tool_surfaces_tool_result():
    rt = _demo_runtime()
    rt.ollama = _FakeOllama()
    reply = asyncio.run(ca._run_conversation_turn(
        rt, [], "how many people at the front door?", preferred_camera="front_door"))
    # The model returned empty content, but a tool ran — the user must get the
    # real detection, NOT an apology.
    assert "Sorry" not in reply
    assert "people" in reply.lower()      # "front_door: 2 people"


def test_ollama_client_sends_think_false():
    from adapter_clients import OllamaClient
    c = OllamaClient(url="http://x:11434", token="", model="qwen3:1.7b", think=False)
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    class _C:
        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _Resp()
    c._client = lambda: _C()
    asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    assert captured["body"]["think"] is False     # native Ollama thinking off


def test_humanize_for_speech_uses_locations_and_drops_punctuation():
    from context import CameraSpec
    cams = [CameraSpec(camera_id="front_door", frame_url="x", role="the front door")]
    out = ca._humanize_for_speech("On front_door: 2 people.", cams)
    assert "front_door" not in out and ":" not in out
    assert "the front door" in out and "2 people" in out
    # no role → underscores/colon still cleaned for speech
    assert ca._humanize_for_speech("On cam_1: a car.") == "On cam 1, a car."


def test_clean_for_speech_strips_markdown():
    assert ca._clean_for_speech("**2 people** at the `front door` #now") == "2 people at the front door now"


def test_ollama_client_omits_think_when_none():
    from adapter_clients import OllamaClient
    c = OllamaClient(url="http://x:11434", token="", model="qwen2.5:1.5b")  # think=None
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    class _C:
        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _Resp()
    c._client = lambda: _C()
    asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    assert "think" not in captured["body"]        # non-thinking models unaffected

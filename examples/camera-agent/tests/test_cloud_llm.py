# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cloud/hybrid LLM brain: the OpenAI-compatible client and runtime selection
via llm_provider (issue #82 — stronger tool-calling without a local LLM)."""
from __future__ import annotations

import asyncio

from adapter_clients import OpenAILLMClient, OllamaClient
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


class _FakeResp:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data


def test_url_normalisation():
    assert OpenAILLMClient(base_url="https://api.x.com/v1", api_key="k", model="m")._url \
        == "https://api.x.com/v1/chat/completions"
    assert OpenAILLMClient(base_url="https://api.x.com", api_key="k", model="m")._url \
        == "https://api.x.com/v1/chat/completions"
    assert OpenAILLMClient(base_url="http://host/v1/chat/completions", api_key="k", model="m")._url \
        == "http://host/v1/chat/completions"


def test_chat_sends_openai_shape_and_normalises_response():
    c = OpenAILLMClient(base_url="https://api.x.com/v1", api_key="secret", model="gpt-x")
    captured = {}

    class _Client:
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp({"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "detect_objects",
                                             "arguments": '{"camera_id":"cam1"}'}}]}}]})
    c._client = lambda: _Client()

    tools = [{"type": "function", "function": {"name": "detect_objects", "parameters": {}}}]
    out = asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}],
                             tools=tools, temperature=0.3, max_tokens=128))
    # request: OpenAI body + bearer auth
    assert captured["url"] == "https://api.x.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-x"
    assert captured["json"]["tool_choice"] == "auto"
    assert captured["json"]["max_tokens"] == 128
    # response: normalised to {"message": {...}} the turn loop expects
    msg = out["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "detect_objects"
    assert msg["content"] == ""


def test_chat_handles_plain_text_and_missing_content():
    c = OpenAILLMClient(base_url="https://api.x.com/v1", api_key=None, model="m")

    class _Client:
        async def post(self, url, json=None, headers=None):
            # no Authorization header when api_key is None
            assert "Authorization" not in (headers or {})
            return _FakeResp({"choices": [{"message": {"role": "assistant", "content": "hello"}}]})
    c._client = lambda: _Client()
    out = asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    assert out["message"]["content"] == "hello"


# ── runtime selects the right client ───────────────────────────────────


def _cfg(**extra):
    base = dict(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="r")])
    base.update(extra)
    return AppConfig(**base)


def test_runtime_uses_ollama_by_default():
    rt = CameraAgentRuntime(_cfg())
    assert isinstance(rt.ollama, OllamaClient)


def test_runtime_uses_openai_when_configured():
    rt = CameraAgentRuntime(_cfg(
        llm_provider="openai", llm_base_url="https://api.x.com/v1",
        llm_api_key="k", llm_model="gpt-x"))
    assert isinstance(rt.ollama, OpenAILLMClient)
    assert rt.ollama._url == "https://api.x.com/v1/chat/completions"

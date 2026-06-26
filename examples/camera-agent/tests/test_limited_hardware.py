# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Limited-hardware knobs: cap CPU cores (num_thread) and context size (num_ctx)
on the local Ollama brain so a weak box isn't oversubscribed."""
from __future__ import annotations

import asyncio

from adapter_clients import OllamaClient
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


class _Resp:
    def raise_for_status(self):
        pass
    def json(self):
        return {"message": {"content": "ok"}}


def _capture_body(client):
    captured = {}

    class _C:
        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _Resp()
    client._client = lambda: _C()
    return captured


def test_num_thread_and_num_ctx_passed_to_ollama():
    c = OllamaClient(url="http://x:11434", token="", model="qwen3:0.6b",
                     num_thread=2, num_ctx=2048)
    cap = _capture_body(c)
    asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    opts = cap["body"]["options"]
    assert opts["num_thread"] == 2
    assert opts["num_ctx"] == 2048


def test_defaults_omit_num_thread():
    c = OllamaClient(url="http://x:11434", token="", model="qwen3:1.7b")
    cap = _capture_body(c)
    asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    opts = cap["body"]["options"]
    assert "num_thread" not in opts          # None → all cores
    assert opts["num_ctx"] == 4096


def test_runtime_threads_config_reaches_client():
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    llm_num_threads=2, llm_num_ctx=2048,
                    cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")])
    rt = CameraAgentRuntime(cfg)
    assert rt.ollama._num_thread == 2
    assert rt.ollama._num_ctx == 2048

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Startup-resilience tests: the camera-agent must bridge the ai-adapter's
cold-start window (model still loading / weights still downloading) instead
of reporting the camera offline until someone restarts the adapter.

* KaicAdapterClient.infer retries transient errors with backoff.
* The vision pre-warm fires a tiny frame and retries until the detector
  answers, so the first real question finds the model loaded.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from adapter_clients import KaicAdapterClient
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


# ── KaicAdapterClient retry ────────────────────────────────────────────


def test_infer_retries_then_succeeds():
    client = KaicAdapterClient(kaic_url="http://k", api_key="x",
                               adapter_name="default", retries=2, retry_backoff_s=0.0)
    calls = {"n": 0}

    class FakeClient:
        async def post(self, url, json=None, headers=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("adapter warming up")
            return _FakeResp({"result": {"detections": []}})

    client._client = lambda: FakeClient()
    out = asyncio.run(client.infer(frame_jpeg=b"x"))
    assert out == {"result": {"detections": []}}
    assert calls["n"] == 3  # 2 failures + 1 success


def test_infer_raises_after_exhausting_retries():
    client = KaicAdapterClient(kaic_url="http://k", api_key="x",
                               adapter_name="default", retries=1, retry_backoff_s=0.0)

    class FakeClient:
        async def post(self, *a, **k):
            raise httpx.ConnectError("adapter down")

    client._client = lambda: FakeClient()
    with pytest.raises(httpx.HTTPError):
        asyncio.run(client.infer(frame_jpeg=b"x"))


# ── Vision pre-warm ────────────────────────────────────────────────────


def _runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="r")],
    )
    return CameraAgentRuntime(cfg)


def test_warmup_jpeg_is_a_valid_jpeg():
    assert CameraAgentRuntime._WARMUP_JPEG[:3] == b"\xff\xd8\xff"


def test_prewarm_vision_retries_until_detector_ready():
    rt = _runtime()
    calls = {"n": 0, "frames": []}

    async def fake_infer(*, frame_jpeg, **kw):
        calls["n"] += 1
        calls["frames"].append(frame_jpeg)
        if calls["n"] < 3:
            raise RuntimeError("model not loaded yet")
        return {"result": {"detections": []}}

    rt.detection_client.infer = fake_infer
    asyncio.run(rt._prewarm_vision(attempts=5, backoff_s=0.0))
    assert calls["n"] == 3
    # warmed with the synthetic JPEG, not a real camera frame
    assert calls["frames"][0][:3] == b"\xff\xd8\xff"


def test_prewarm_vision_gives_up_quietly_after_attempts():
    rt = _runtime()
    calls = {"n": 0}

    async def always_fail(*, frame_jpeg, **kw):
        calls["n"] += 1
        raise RuntimeError("never ready")

    rt.detection_client.infer = always_fail
    # Should not raise even when the detector never comes up.
    asyncio.run(rt._prewarm_vision(attempts=3, backoff_s=0.0))
    assert calls["n"] == 3

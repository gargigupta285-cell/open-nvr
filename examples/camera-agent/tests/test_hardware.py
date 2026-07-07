# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hardware guidance: tasks.yml hints → per-skill rows → the /hardware
aggregation the demo UI's Hardware panel renders. Advisory only —
nothing here gates a skill."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import (
    _SKILL_HARDWARE,
    AppConfig,
    CameraAgentRuntime,
    build_app,
)
from context import CameraSpec


def _runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front")],
    )
    return CameraAgentRuntime(cfg)


# ── deriver: tasks.yml hardware blocks → per-skill rows ────────────────


def test_skill_hardware_derived_for_vision_skills():
    assert {r["task"] for r in _SKILL_HARDWARE["see"]} == {"image_captioning", "vqa"}
    assert {r["task"] for r in _SKILL_HARDWARE["count"]} == {"object_detection"}
    row = _SKILL_HARDWARE["count"][0]
    assert row["min"] and row["recommended"] and row["note"]
    assert "yolov8" in row["adapters"]


def test_watch_inherits_counts_hardware():
    assert _SKILL_HARDWARE["watch"] == _SKILL_HARDWARE["count"]


# ── aggregation ────────────────────────────────────────────────────────


def test_recommendation_covers_enabled_skills_and_llm():
    rt = _runtime()
    hw = rt.hardware_recommendation()
    tasks = {r["task"] for r in hw["rows"]}
    # all defaults on → vision tasks present, deduped (watch inherits count)
    assert {"object_detection", "image_captioning", "vqa", "llm"} <= tasks
    assert sum(1 for r in hw["rows"] if r["task"] == "object_detection") == 1
    assert hw["gpu_recommended"] is True
    assert hw["running_on"] == "unknown"      # KAI-C never fetched
    assert hw["tips"]


def test_recommendation_shrinks_when_gpu_skills_disabled():
    rt = _runtime()
    for sid in ("see", "count", "watch", "faces"):
        rt.set_skill_enabled(sid, False)
    hw = rt.hardware_recommendation()
    tasks = {r["task"] for r in hw["rows"]}
    assert "object_detection" not in tasks and "image_captioning" not in tasks
    assert tasks == {"llm"}                    # only the always-on LLM row
    assert hw["gpu_recommended"] is False


def test_running_on_reflects_live_gpu_flags():
    rt = _runtime()
    rt.kaic_capabilities._gpu = {"yolov8": False, "blip": True}
    hw = rt.hardware_recommendation()
    assert hw["running_on"] == "gpu" and hw["gpu_adapters"] == ["blip"]
    rt.kaic_capabilities._gpu = {"yolov8": False}
    assert rt.hardware_recommendation()["running_on"] == "cpu"


# ── endpoint ───────────────────────────────────────────────────────────


def test_hardware_endpoint_shape():
    rt = _runtime()
    client = TestClient(build_app(rt))
    hw = client.get("/hardware").json()
    for key in ("running_on", "gpu_recommended", "summary", "rows", "tips"):
        assert key in hw
    assert isinstance(hw["rows"], list) and hw["rows"]


# ── capabilities client: gpu permission parse ──────────────────────────


def test_capabilities_client_parses_gpu_permissions():
    from adapter_clients import KaicCapabilitiesClient

    client = KaicCapabilitiesClient(kaic_url="http://k")

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"adapters": {
                "yolov8": {"tasks_advertised": ["object_detection"],
                           "permissions": {"gpu": False}},
                "blip": {"tasks_advertised": ["image_captioning"],
                         "permissions": {"gpu": True}},
            }}

    class _Http:
        async def get(self, url, headers=None): return _Resp()

    client._client = lambda: _Http()
    asyncio.run(client.refresh())
    assert client.gpu_adapters == {"yolov8": False, "blip": True}
    assert client.tasks_advertised == {"object_detection", "image_captioning"}

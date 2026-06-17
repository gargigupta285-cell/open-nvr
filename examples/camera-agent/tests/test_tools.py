# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the LLM tool handlers — describe_camera, detect_objects,
recognize_faces, recent_events. The KAI-C adapter clients are mocked
so no HTTP fires; each test pins one tool's response-shape handling.

Tool result strings are designed to flow into the LLM as plain prose,
so the tests assert on substrings rather than exact equality —
phrasing is allowed to evolve."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from context import CameraContext, CameraSpec, EventRecord
from tools import CameraTools, build_tool_definitions


class _StubFrameSource:
    camera_id = "front-porch"

    def __init__(self, frame: bytes = b"\xff\xd8jpeg") -> None:
        self._frame = frame
        self.calls = 0

    def fetch(self) -> bytes:
        self.calls += 1
        return self._frame


def _ctx_with_camera() -> CameraContext:
    spec = CameraSpec(
        camera_id="front-porch",
        frame_url="http://x",
        role="entrance",
    )
    ctx = CameraContext(cameras=[spec], frame_cache_ttl_seconds=5.0)
    ctx.register_frame_source("front-porch", _StubFrameSource())
    return ctx


def _build_tools(ctx: CameraContext, *,
                 caption_response=None,
                 detection_response=None,
                 recognition_response=None) -> CameraTools:
    caption = AsyncMock()
    caption.infer.return_value = caption_response or {
        "result": {"caption": "a box on a doormat"}
    }
    detect = AsyncMock()
    detect.infer.return_value = detection_response or {
        "result": {"detections": [{"label": "person"}]}
    }
    recognise = AsyncMock()
    recognise.infer.return_value = recognition_response or {
        "result": {"recognized": False, "face_bbox": [10, 10, 50, 50]}
    }
    return CameraTools(
        context=ctx,
        caption_client=caption,
        detection_client=detect,
        recognition_client=recognise,
    )


# ── describe_camera ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_camera_returns_caption():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    result = await tools.describe_camera({"camera_id": "front-porch"})
    assert "box on a doormat" in result
    assert "front-porch" in result


@pytest.mark.asyncio
async def test_describe_camera_unknown_id_returns_error_string():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    result = await tools.describe_camera({"camera_id": "kitchen"})
    assert result.startswith("ERROR:")
    assert "kitchen" in result


@pytest.mark.asyncio
async def test_describe_camera_empty_caption_uses_fallback():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, caption_response={"result": {"caption": ""}})
    result = await tools.describe_camera({"camera_id": "front-porch"})
    # No caption available -> fall back to object detection so the user
    # still gets a grounded answer rather than an error or a made-up scene.
    assert "person" in result
    assert "front-porch" in result


@pytest.mark.asyncio
async def test_describe_camera_caption_exception_uses_detection_fallback():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    tools._caption.infer.side_effect = RuntimeError("502 from BLIP")
    result = await tools.describe_camera({"camera_id": "front-porch"})
    # Caption adapter erroring -> grounded detection fallback, not a raw error.
    assert "person" in result
    assert "front-porch" in result


# ── detect_objects ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_objects_groups_duplicate_labels():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, detection_response={
        "result": {"detections": [
            {"label": "person"},
            {"label": "person"},
            {"label": "car"},
        ]},
    })
    result = await tools.detect_objects({"camera_id": "front-porch"})
    # Counts are pluralised naturally for speech ("2 people", not "2x person").
    assert "2 people" in result
    assert "car" in result


@pytest.mark.asyncio
async def test_detect_objects_no_detections():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, detection_response={"result": {"detections": []}})
    result = await tools.detect_objects({"camera_id": "front-porch"})
    assert "No objects detected" in result


@pytest.mark.asyncio
async def test_detect_objects_caps_to_eight_labels():
    """A scene with many unique labels must not flood the LLM context.
    Cap to 8 labels in the summary."""
    ctx = _ctx_with_camera()
    detections = [{"label": f"thing{i}"} for i in range(20)]
    tools = _build_tools(ctx, detection_response={"result": {"detections": detections}})
    result = await tools.detect_objects({"camera_id": "front-porch"})
    # Count comma-separated entries; should be ≤ 8.
    body = result.split(":", 1)[1] if ":" in result else result
    parts = [p.strip() for p in body.rstrip(".").split(",")]
    assert len(parts) <= 8


# ── recognize_faces ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recognize_faces_known_person():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, recognition_response={
        "result": {
            "recognized": True,
            "name": "Alice",
            "category": "family",
            "similarity": 0.88,
        },
    })
    result = await tools.recognize_faces({"camera_id": "front-porch"})
    assert "Alice" in result
    assert "family" in result
    assert "0.88" in result


@pytest.mark.asyncio
async def test_recognize_faces_unknown_face():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, recognition_response={
        "result": {"recognized": False, "face_bbox": [1, 2, 3, 4]},
    })
    result = await tools.recognize_faces({"camera_id": "front-porch"})
    assert "not registered" in result


@pytest.mark.asyncio
async def test_recognize_faces_no_face():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx, recognition_response={
        "result": {"recognized": False, "face_bbox": None},
    })
    result = await tools.recognize_faces({"camera_id": "front-porch"})
    assert "no face" in result.lower()


# ── recent_events ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_events_no_events():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    result = await tools.recent_events({
        "camera_id": "front-porch", "window_seconds": 60,
    })
    assert "No events" in result


@pytest.mark.asyncio
async def test_recent_events_returns_summary_lines():
    ctx = _ctx_with_camera()
    ctx.record_event(EventRecord(
        received_at=time.time() - 5,
        camera_id="front-porch",
        adapter="yolov8",
        summary="person detected",
    ))
    tools = _build_tools(ctx)
    result = await tools.recent_events({
        "camera_id": "front-porch", "window_seconds": 60,
    })
    assert "person detected" in result
    assert "5s ago" in result


@pytest.mark.asyncio
async def test_recent_events_any_camera_wildcard():
    ctx = _ctx_with_camera()
    ctx.record_event(EventRecord(
        received_at=time.time(),
        camera_id="front-porch",
        adapter="x",
        summary="alpha",
    ))
    tools = _build_tools(ctx)
    result = await tools.recent_events({
        "camera_id": "__any__", "window_seconds": 60,
    })
    assert "alpha" in result


@pytest.mark.asyncio
async def test_recent_events_rejects_negative_window():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    result = await tools.recent_events({
        "camera_id": "front-porch", "window_seconds": -5,
    })
    assert result.startswith("ERROR")


@pytest.mark.asyncio
async def test_recent_events_rejects_unknown_camera():
    ctx = _ctx_with_camera()
    tools = _build_tools(ctx)
    result = await tools.recent_events({
        "camera_id": "kitchen", "window_seconds": 60,
    })
    assert result.startswith("ERROR")


# ── Tool definitions ───────────────────────────────────────────────


def test_tool_definitions_bake_camera_ids_into_enum():
    """The LLM should not be able to invent camera names — the enum
    constrains it to configured cameras at the protocol level."""
    defs = build_tool_definitions(["front-porch", "back-door"])
    describe = next(d for d in defs if d["function"]["name"] == "describe_camera")
    enum = describe["function"]["parameters"]["properties"]["camera_id"]["enum"]
    assert set(enum) == {"front-porch", "back-door"}


def test_tool_definitions_recent_events_offers_any_wildcard():
    defs = build_tool_definitions(["front-porch"])
    recent = next(d for d in defs if d["function"]["name"] == "recent_events")
    enum = recent["function"]["parameters"]["properties"]["camera_id"]["enum"]
    assert "__any__" in enum
    assert "front-porch" in enum


def test_tool_definitions_with_no_cameras_has_sentinel():
    """An empty camera list shouldn't crash schema generation —
    insert a sentinel value so the LLM sees a usable enum and the
    handler can return ERROR cleanly."""
    defs = build_tool_definitions([])
    describe = next(d for d in defs if d["function"]["name"] == "describe_camera")
    enum = describe["function"]["parameters"]["properties"]["camera_id"]["enum"]
    assert enum  # non-empty

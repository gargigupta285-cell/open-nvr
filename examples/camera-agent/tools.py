# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tool implementations the camera-agent exposes to the LLM.

Each tool is a coroutine ``handler(args: dict) -> str`` that takes the
arguments the LLM emitted and returns a short text string the LLM
will read back as a ``role: "tool"`` message. The string should be
plain prose, not JSON — the LLM consumes it as natural language and
then phrases the answer to the user.

Four tools:

* ``describe_camera`` — BLIP scene caption on a live frame.
* ``detect_objects`` — YOLOv8 object detection on a live frame.
* ``recognize_faces`` — InsightFace recognition on a live frame.
* ``recent_events`` — recent inference events from the NATS ring
  buffer (no live inference — answers "what happened earlier?").

All four use ``CameraContext`` for shared frame caching + camera
metadata + the event ring.
"""
from __future__ import annotations

import logging
from typing import Any

from adapter_clients import KaicAdapterClient
from context import CameraContext
from frame_sources import FrameSourceError

logger = logging.getLogger(__name__)


# ── Tool definitions in OpenAI / Pipecat function-calling shape ────


def build_tool_definitions(cameras: list[str]) -> list[dict[str, Any]]:
    """Build the OpenAI-style ``tools`` list. Camera IDs are baked
    into the enum so the model can't invent unknown camera names."""
    camera_enum = list(cameras) or ["__no_cameras_configured__"]
    return [
        {
            "type": "function",
            "function": {
                "name": "describe_camera",
                "description": (
                    "Get a one-sentence natural-language description of "
                    "what is currently visible on the named camera. Use "
                    "this when the user asks 'what's on the porch?' or "
                    "'what do you see?' — anything that wants a scene "
                    "description rather than a specific object count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_id": {
                            "type": "string",
                            "enum": camera_enum,
                            "description": "Which camera to look at.",
                        },
                    },
                    "required": ["camera_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "detect_objects",
                "description": (
                    "Run object detection on the named camera and "
                    "return a list of what was detected (people, cars, "
                    "packages, animals, etc.) with confidence scores. "
                    "Use when the user asks 'is there a package?', "
                    "'how many cars?', or wants a specific object count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_id": {
                            "type": "string",
                            "enum": camera_enum,
                        },
                    },
                    "required": ["camera_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recognize_faces",
                "description": (
                    "Run face recognition on the named camera. If the "
                    "person is registered (family / friend / known) "
                    "returns their name; otherwise reports 'unknown'. "
                    "Use when the user asks 'who's at the door?' or "
                    "'is that Alice?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_id": {
                            "type": "string",
                            "enum": camera_enum,
                        },
                    },
                    "required": ["camera_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recent_events",
                "description": (
                    "Look back at recent inference events on the "
                    "cameras. Use when the user asks about the past "
                    "('did anyone come earlier?', 'when did the "
                    "package arrive?'). Returns a list of recent "
                    "events newest-first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_id": {
                            "type": "string",
                            "enum": camera_enum + ["__any__"],
                            "description": (
                                "Filter to one camera, or '__any__' for "
                                "events across all cameras."
                            ),
                        },
                        "window_seconds": {
                            "type": "number",
                            "description": (
                                "How far back to look, in SECONDS. "
                                "60 = last minute, 600 = last 10 "
                                "minutes, 3600 = last hour. Always "
                                "express the window in seconds even "
                                "if the user phrased it differently."
                            ),
                        },
                    },
                    "required": ["camera_id", "window_seconds"],
                },
            },
        },
    ]


# ── Tool handlers ──────────────────────────────────────────────────


class CameraTools:
    """Holds references to the context + KAI-C clients and exposes
    one coroutine per tool. Pipecat's LLM service calls these via
    ``register_function``."""

    def __init__(
        self,
        *,
        context: CameraContext,
        caption_client: KaicAdapterClient,
        detection_client: KaicAdapterClient,
        recognition_client: KaicAdapterClient,
    ) -> None:
        self._ctx = context
        self._caption = caption_client
        self._detect = detection_client
        self._recognise = recognition_client

    # ── describe_camera ────────────────────────────────────────────

    async def describe_camera(self, args: dict[str, Any]) -> str:
        camera_id = self._require_camera(args)
        if isinstance(camera_id, str) and camera_id.startswith("ERROR:"):
            return camera_id
        try:
            frame = await self._ctx.get_frame(camera_id)
        except (LookupError, FrameSourceError) as exc:
            return f"Cannot fetch camera {camera_id!r}: {exc}"
        try:
            response = await self._caption.infer(frame_jpeg=frame)
        except Exception:
            logger.exception("describe_camera: caption call failed")
            return "Caption adapter failed; cannot describe the scene right now."
        caption = ((response.get("result") or {}).get("caption") or "").strip()
        if not caption:
            return f"The {camera_id} camera is online but the captioner returned nothing."
        return f"On {camera_id}: {caption}"

    # ── detect_objects ─────────────────────────────────────────────

    async def detect_objects(self, args: dict[str, Any]) -> str:
        camera_id = self._require_camera(args)
        if isinstance(camera_id, str) and camera_id.startswith("ERROR:"):
            return camera_id
        try:
            frame = await self._ctx.get_frame(camera_id)
        except (LookupError, FrameSourceError) as exc:
            return f"Cannot fetch camera {camera_id!r}: {exc}"
        try:
            response = await self._detect.infer(frame_jpeg=frame)
        except Exception:
            logger.exception("detect_objects: detector call failed")
            return "Object detector failed."
        detections = ((response.get("result") or {}).get("detections")) or []
        if not detections:
            return f"No objects detected on {camera_id}."
        # Group identical labels for a readable summary; cap at 8 to
        # keep the tool-result message short.
        counts: dict[str, int] = {}
        for det in detections[:32]:
            label = str(det.get("label") or det.get("class") or "?").strip()
            if label:
                counts[label] = counts.get(label, 0) + 1
        parts = [
            f"{count}× {label}" if count > 1 else label
            for label, count in sorted(counts.items())
        ][:8]
        return f"On {camera_id}: " + ", ".join(parts) + "."

    # ── recognize_faces ────────────────────────────────────────────

    async def recognize_faces(self, args: dict[str, Any]) -> str:
        camera_id = self._require_camera(args)
        if isinstance(camera_id, str) and camera_id.startswith("ERROR:"):
            return camera_id
        try:
            frame = await self._ctx.get_frame(camera_id)
        except (LookupError, FrameSourceError) as exc:
            return f"Cannot fetch camera {camera_id!r}: {exc}"
        try:
            response = await self._recognise.infer(
                frame_jpeg=frame,
                extra={"task": "face_recognition"},
            )
        except Exception:
            logger.exception("recognize_faces: recognition call failed")
            return "Face recogniser failed."
        result = response.get("result") or {}
        if result.get("recognized"):
            name = result.get("name") or result.get("person_id") or "someone"
            category = result.get("category") or "unknown category"
            similarity = result.get("similarity")
            sim_phrase = f", similarity {similarity:.2f}" if isinstance(
                similarity, (int, float)
            ) else ""
            return (
                f"On {camera_id}: recognised {name} "
                f"({category}{sim_phrase})."
            )
        if "face_bbox" in result and result.get("face_bbox"):
            return f"On {camera_id}: a face is visible but it's not registered."
        return f"On {camera_id}: no face detected."

    # ── recent_events ──────────────────────────────────────────────

    async def recent_events(self, args: dict[str, Any]) -> str:
        camera_arg = args.get("camera_id")
        try:
            window = float(args.get("window_seconds", 0))
        except (TypeError, ValueError):
            return "ERROR: window_seconds must be a number."
        if window <= 0:
            return "ERROR: window_seconds must be positive."

        camera_id: str | None
        if camera_arg in (None, "", "__any__"):
            camera_id = None
        elif isinstance(camera_arg, str) and self._ctx.known_camera(camera_arg):
            camera_id = camera_arg
        else:
            return (
                f"ERROR: unknown camera_id {camera_arg!r}. Use one of "
                f"{sorted(c.camera_id for c in self._ctx.cameras)} "
                f"or '__any__'."
            )

        events = self._ctx.recent_events(
            camera_id=camera_id, window_seconds=window
        )
        if not events:
            scope = camera_id or "any camera"
            mins = int(window / 60) or 1
            return f"No events on {scope} in the last {mins} minute(s)."
        # Wall-clock deltas for human readability; CameraContext
        # stamps received_at with time.time(). Cap at 6 entries so
        # the tool message stays short.
        import time as _time
        now = _time.time()
        lines = [
            f"{int(now - e.received_at)}s ago — {e.camera_id}: {e.summary}"
            for e in events[:6]
        ]
        return "Recent events:\n" + "\n".join(lines)

    # ── Helpers ────────────────────────────────────────────────────

    def _require_camera(self, args: dict[str, Any]) -> str:
        camera_id = args.get("camera_id")
        if not isinstance(camera_id, str) or not camera_id:
            return "ERROR: camera_id is required."
        if not self._ctx.known_camera(camera_id):
            return (
                f"ERROR: camera_id {camera_id!r} is not configured. "
                f"Available: "
                f"{sorted(c.camera_id for c in self._ctx.cameras)}."
            )
        return camera_id

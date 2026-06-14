# Copyright (c) 2026 OpenNVR
# This file is part of OpenNVR.
#
# OpenNVR is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenNVR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.

"""
Bridge between the OpenNVR backend and the AI Adapter Contract v1.

The backend's inference loop historically spoke a *legacy* dialect on
both sides: it sent frames as an ``opennvr://…/latest.jpg`` file URI
(``{"input": {"frame": {"uri": …}}}``) and read flat ``confidence`` /
``bbox`` / ``count`` fields back. SDK-based adapters (yolov8, blip, vlm,
…) speak the v1 contract instead: a frame as multipart bytes or a JSON
``frame_b64``, and a structured ``{"result": {...}}`` response.

These two pure helpers translate between the two so the backend can drive
SDK adapters without a shared volume:

* ``build_infer_payload`` — backend → adapter request body (contract v1).
* ``flatten_infer_response`` — adapter response → the flat shape the
  backend's ``inference_manager`` already persists.

Kept dependency-free and pure so they are fully unit-testable without a
running adapter.
"""
from __future__ import annotations

import base64
from typing import Any

# Keys the contract reserves; params may not collide with them.
_RESERVED_PARAM_KEYS = {"frame_b64", "audio_b64", "file_b64", "__file__"}


def build_infer_payload(
    *,
    task: str,
    jpeg_bytes: bytes,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a contract-v1 JSON request body for an IMAGE-shape adapter.

    Shape: ``{"frame_b64": "<base64 jpeg>", "task": task, **params}``.
    The SDK's body parser pulls ``frame_b64`` out as the frame bytes and
    treats every other top-level key (``task`` and any params) as
    inference parameters.
    """
    if not isinstance(jpeg_bytes, (bytes, bytearray)) or not jpeg_bytes:
        raise ValueError("build_infer_payload: jpeg_bytes must be non-empty bytes")
    body: dict[str, Any] = {}
    if params:
        for k, v in params.items():
            if k in _RESERVED_PARAM_KEYS:
                raise ValueError(
                    f"build_infer_payload: param key {k!r} is reserved by the contract"
                )
            body[k] = v
    body["task"] = task
    body["frame_b64"] = base64.b64encode(bytes(jpeg_bytes)).decode("ascii")
    return body


def flatten_infer_response(adapter_json: dict[str, Any]) -> dict[str, Any]:
    """Translate a contract-v1 adapter response into the flat dict the
    backend's ``inference_manager`` persists.

    Accepts the SDK ``InferResponse`` shape::

        {"model_name", "model_version", "inference_ms",
         "result": {"detections": [...]} | {"caption": "..."} | {...}}

    and also tolerates a response that is *already* flat (legacy adapter)
    so a mixed deployment doesn't break.

    Output keys (all optional): ``label``, ``confidence``, ``bbox``
    ([x, y, w, h] normalized), ``count``, ``caption``, ``latency_ms``,
    plus ``detections`` (full list, passed through) and ``model_name`` /
    ``model_version`` / ``model_fingerprint`` when present.
    """
    if not isinstance(adapter_json, dict):
        return {"confidence": 0.0}

    result = adapter_json.get("result")
    # Legacy/flat response: no nested "result" → assume it's already flat.
    if not isinstance(result, dict):
        return dict(adapter_json)

    flat: dict[str, Any] = {}
    # Carry model provenance through for the event bus / audit.
    for key in ("model_name", "model_version", "model_fingerprint", "inference_ms"):
        if key in adapter_json and adapter_json[key] is not None:
            flat[key] = adapter_json[key]
    if "inference_ms" in adapter_json and adapter_json["inference_ms"] is not None:
        flat["latency_ms"] = adapter_json["inference_ms"]

    detections = result.get("detections")
    if isinstance(detections, list):
        flat["detections"] = detections
        flat["count"] = len(detections)
        top = _top_detection(detections)
        if top is not None:
            label = top.get("label") or top.get("class")
            if label is not None:
                flat["label"] = str(label)
            conf = top.get("confidence", top.get("score"))
            flat["confidence"] = float(conf) if _is_number(conf) else 0.0
            bbox = _normalize_bbox(top.get("bbox"))
            if bbox is not None:
                flat["bbox"] = bbox
        else:
            # Detector ran but found nothing → zero-confidence heartbeat,
            # which the backend uses as a "no detection" placeholder.
            flat["confidence"] = 0.0
        return flat

    # Captioning / OCR / generic single-value results.
    caption = result.get("caption") or result.get("description") or result.get("text")
    if caption is not None:
        flat["caption"] = str(caption)
        flat["confidence"] = float(result.get("confidence", 1.0)) if _is_number(
            result.get("confidence")
        ) else 1.0
        return flat

    # Unknown result shape — pass it through under "result" and emit a
    # zero-confidence heartbeat so the loop doesn't crash on .get().
    flat["result"] = result
    flat.setdefault("confidence", 0.0)
    return flat


def _top_detection(detections: list[Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1.0
    for det in detections:
        if not isinstance(det, dict):
            continue
        score = det.get("confidence", det.get("score"))
        score = float(score) if _is_number(score) else 0.0
        if score > best_score:
            best_score = score
            best = det
    return best


def _normalize_bbox(bbox: Any) -> list[float] | None:
    """Return [x, y, w, h] from either a dict {x,y,w,h} or a 4-list."""
    if isinstance(bbox, dict):
        try:
            return [
                float(bbox.get("x", 0.0)), float(bbox.get("y", 0.0)),
                float(bbox.get("w", 0.0)), float(bbox.get("h", 0.0)),
            ]
        except (TypeError, ValueError):
            return None
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        except (TypeError, ValueError):
            return None
    return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)

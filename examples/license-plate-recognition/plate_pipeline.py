# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two-stage detect → OCR pipeline for license-plate recognition.

This module is deliberately separate from ``license_plate_recognition.py``
so the chain logic is testable in isolation (no event loop, no config,
no NATS). The detector + OCR clients are passed in.

Stage 1 — Vehicle detection
    POST the full camera frame to the YOLOv8 adapter via KAI-C.
    Filter the returned detections to vehicle classes (car / truck /
    bus / motorcycle).

Stage 2 — Plate crop
    For each vehicle, crop the relevant region of the frame. Two
    crop strategies are supported:

    * ``vehicle`` — crop the entire vehicle bbox and feed to the OCR
      adapter. Robust default; works on any YOLOv8 model.
    * ``lower_third`` — crop the bottom third of the vehicle bbox,
      where the plate usually sits. Faster and more accurate for
      most front-/rear-camera angles but is a heuristic.

    Both strategies use Pillow for cropping — JPEG bytes in, JPEG
    bytes out.

Stage 3 — OCR
    POST the crop to the fast-plate-ocr adapter via KAI-C. If
    the response carries ``accepted=true`` and the confidence floor
    is met, the pipeline yields a ``PlateRead`` record. Caller turns
    that into an alert.

The pipeline is intentionally HTTP-only (no WebSocket streaming).
LPR is event-driven — one inference per detected vehicle, not per
frame — so the polling latency dominates anyway, and the simpler
HTTP path is easier to reason about under failure.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Protocol

logger = logging.getLogger(__name__)


DEFAULT_VEHICLE_LABELS: tuple[str, ...] = ("car", "truck", "bus", "motorcycle")
DEFAULT_DETECTION_CONFIDENCE: float = 0.40
DEFAULT_OCR_CONFIDENCE: float = 0.50
DEFAULT_CROP_STRATEGY: str = "lower_third"


# ── Wire shapes ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class VehicleDetection:
    """A single vehicle detection from the upstream YOLOv8 call."""
    label: str
    confidence: float
    # Pixel-coordinate bounding box: (x1, y1, x2, y2). The YOLOv8
    # adapter returns either normalised or pixel coords depending on
    # config; the pipeline normalises to pixels before calling
    # ``crop_for_plate`` so cropping math is straightforward.
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class PlateRead:
    """A plate-text read produced by the OCR stage."""
    plate_text: str
    ocr_confidence: float
    vehicle_label: str
    vehicle_confidence: float
    vehicle_bbox: tuple[int, int, int, int]
    model_id: str | None = None
    correlation_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ── Client protocols ────────────────────────────────────────────────
#
# Defined here as Protocols so the pipeline can be unit-tested with
# fake clients (see tests/) without standing up actual KAI-C HTTP.


class DetectorClient(Protocol):
    def detect(self, frame_jpeg: bytes, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Run object detection on a frame. Returns the raw §5.1
        ``InferResponse`` body — pipeline parses out detections from
        ``result.detections``."""


class OcrClient(Protocol):
    def read(self, plate_jpeg: bytes, *, min_confidence: float | None = None,
             correlation_id: str | None = None) -> dict[str, Any]:
        """Run OCR on a plate crop. Returns the raw §5 ``InferResponse``
        body — pipeline reads ``result.plate_text``, ``result.confidence``,
        ``result.accepted``."""


# ── Pipeline ────────────────────────────────────────────────────────


@dataclass
class PlatePipelineConfig:
    """Per-camera tuning knobs the orchestrator passes in."""
    vehicle_labels: tuple[str, ...] = DEFAULT_VEHICLE_LABELS
    detection_confidence: float = DEFAULT_DETECTION_CONFIDENCE
    ocr_confidence: float = DEFAULT_OCR_CONFIDENCE
    crop_strategy: str = DEFAULT_CROP_STRATEGY

    def __post_init__(self) -> None:
        if self.crop_strategy not in ("vehicle", "lower_third"):
            raise ValueError(
                f"crop_strategy must be 'vehicle' or 'lower_third'; "
                f"got {self.crop_strategy!r}"
            )
        if not 0.0 <= self.detection_confidence <= 1.0:
            raise ValueError(
                f"detection_confidence must be in [0,1]; got {self.detection_confidence}"
            )
        if not 0.0 <= self.ocr_confidence <= 1.0:
            raise ValueError(
                f"ocr_confidence must be in [0,1]; got {self.ocr_confidence}"
            )
        if not self.vehicle_labels:
            raise ValueError("vehicle_labels must be non-empty")


class PlatePipeline:
    """Drives the detect → crop → OCR chain for one frame."""

    def __init__(
        self,
        detector: DetectorClient,
        ocr: OcrClient,
        config: PlatePipelineConfig | None = None,
    ) -> None:
        self.detector = detector
        self.ocr = ocr
        self.config = config or PlatePipelineConfig()

    def process_frame(
        self,
        frame_jpeg: bytes,
        *,
        correlation_id: str | None = None,
    ) -> Iterator[PlateRead]:
        """Run one full pass over a frame. Yields zero or more
        ``PlateRead`` records (one per accepted plate)."""

        try:
            det_response = self.detector.detect(
                frame_jpeg, correlation_id=correlation_id
            )
        except Exception:
            logger.exception("vehicle detection failed; dropping frame")
            return

        vehicles = list(_parse_vehicle_detections(
            det_response,
            allowed_labels=set(self.config.vehicle_labels),
            min_confidence=self.config.detection_confidence,
            frame_jpeg=frame_jpeg,
        ))
        if not vehicles:
            return

        for vehicle in vehicles:
            try:
                crop = crop_for_plate(
                    frame_jpeg, vehicle.bbox, self.config.crop_strategy
                )
            except Exception:
                logger.exception(
                    "failed to crop vehicle bbox; skipping (bbox=%s)",
                    vehicle.bbox,
                )
                continue

            try:
                ocr_response = self.ocr.read(
                    crop,
                    min_confidence=self.config.ocr_confidence,
                    correlation_id=correlation_id,
                )
            except Exception:
                logger.exception(
                    "ocr failed on vehicle crop; skipping (bbox=%s)",
                    vehicle.bbox,
                )
                continue

            read = _parse_plate_read(
                ocr_response,
                vehicle=vehicle,
                correlation_id=correlation_id,
            )
            if read is None:
                continue
            yield read


# ── Parsing helpers ─────────────────────────────────────────────────


def _parse_vehicle_detections(
    response: dict[str, Any],
    *,
    allowed_labels: set[str],
    min_confidence: float,
    frame_jpeg: bytes,
) -> Iterable[VehicleDetection]:
    """Walk a §5.1 InferResponse + filter to vehicle detections above
    the confidence floor, converting any normalised bboxes to
    pixels using the frame's actual dimensions."""

    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return
    detections = result.get("detections") or result.get("objects") or []
    if not isinstance(detections, list):
        return

    width, height = _frame_dimensions(frame_jpeg)

    for det in detections:
        if not isinstance(det, dict):
            continue
        label = str(det.get("label") or det.get("class") or "").lower()
        if label not in allowed_labels:
            continue
        try:
            confidence = float(det.get("confidence") or det.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
        if confidence < min_confidence:
            continue

        bbox_raw = det.get("bbox") or det.get("box")
        try:
            bbox = _normalise_bbox_to_pixels(bbox_raw, width, height)
        except (TypeError, ValueError):
            continue
        if bbox is None:
            continue

        yield VehicleDetection(label=label, confidence=confidence, bbox=bbox)


def _normalise_bbox_to_pixels(
    bbox_raw: Any,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Coerce a bbox into pixel-coordinate (x1, y1, x2, y2).

    The contract's canonical shape (§5.1 ``NormalizedBBox``) is a
    **dict** ``{"x", "y", "w", "h"}`` with values in ``[0, 1]`` — the
    real YOLOv8 reference adapter emits this and any §5.1-compliant
    detector should too. We also accept a few list shapes for
    pragmatic compatibility with third-party detectors that don't
    follow the contract exactly:

    * Dict ``{"x", "y", "w", "h"}`` normalised (canonical, §5.1)
    * Dict ``{"x1", "y1", "x2", "y2"}`` either normalised or pixel
    * List ``[x1, y1, x2, y2]`` normalised (all ≤ 1.0)
    * List ``[x1, y1, x2, y2]`` pixel
    * List ``[x, y, w, h]`` pixel (xywh)

    Note: distinguishing list-xyxy from list-xywh in pixel space is a
    heuristic and can misclassify legitimate xyxy bboxes whose
    coordinates happen to fit the xywh-arithmetic constraints. Stick
    with the canonical dict shape if you're authoring a detector.
    """
    # ── Dict shape (canonical §5.1) ──────────────────────────────
    if isinstance(bbox_raw, dict):
        if {"x", "y", "w", "h"} <= bbox_raw.keys():
            x_n = float(bbox_raw["x"])
            y_n = float(bbox_raw["y"])
            w_n = float(bbox_raw["w"])
            h_n = float(bbox_raw["h"])
            if all(0.0 <= v <= 1.0 for v in (x_n, y_n, w_n, h_n)):
                x1 = int(x_n * width)
                y1 = int(y_n * height)
                x2 = int((x_n + w_n) * width)
                y2 = int((y_n + h_n) * height)
            else:
                # Pixel xywh dict.
                x1 = int(x_n)
                y1 = int(y_n)
                x2 = int(x_n + w_n)
                y2 = int(y_n + h_n)
        elif {"x1", "y1", "x2", "y2"} <= bbox_raw.keys():
            vals = [
                float(bbox_raw["x1"]), float(bbox_raw["y1"]),
                float(bbox_raw["x2"]), float(bbox_raw["y2"]),
            ]
            if all(0.0 <= v <= 1.0 for v in vals):
                x1 = int(vals[0] * width)
                y1 = int(vals[1] * height)
                x2 = int(vals[2] * width)
                y2 = int(vals[3] * height)
            else:
                x1, y1, x2, y2 = (int(v) for v in vals)
        else:
            return None
    # ── List / tuple shape ───────────────────────────────────────
    elif isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
        values = [float(v) for v in bbox_raw]
        if all(0.0 <= v <= 1.0 for v in values):
            # Normalised list — treat as xyxy.
            x1 = int(values[0] * width)
            y1 = int(values[1] * height)
            x2 = int(values[2] * width)
            y2 = int(values[3] * height)
        else:
            # Pixel — could be xyxy or xywh. Heuristic only.
            a, b, c, d = values
            if 0 < c <= width and 0 < d <= height and a + c <= width + 1 and b + d <= height + 1:
                x1, y1 = int(a), int(b)
                x2, y2 = int(a + c), int(b + d)
            else:
                x1, y1, x2, y2 = int(a), int(b), int(c), int(d)
    else:
        return None

    # Clip + reject degenerate.
    x1 = max(0, min(x1, width))
    x2 = max(0, min(x2, width))
    y1 = max(0, min(y1, height))
    y2 = max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _parse_plate_read(
    response: dict[str, Any],
    *,
    vehicle: VehicleDetection,
    correlation_id: str | None,
) -> PlateRead | None:
    """Translate a fast-plate-ocr response into a ``PlateRead`` or
    return None when the OCR didn't accept the read."""
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    plate_text = str(result.get("plate_text") or "").strip()
    if not plate_text:
        return None
    try:
        ocr_confidence = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    accepted = bool(result.get("accepted", True))
    if not accepted:
        return None
    return PlateRead(
        plate_text=plate_text,
        ocr_confidence=ocr_confidence,
        vehicle_label=vehicle.label,
        vehicle_confidence=vehicle.confidence,
        vehicle_bbox=vehicle.bbox,
        model_id=result.get("model_id"),
        correlation_id=correlation_id,
        raw=result,
    )


# ── Cropping ────────────────────────────────────────────────────────


def crop_for_plate(
    frame_jpeg: bytes,
    bbox: tuple[int, int, int, int],
    strategy: str,
) -> bytes:
    """Crop the frame for the OCR stage. Returns JPEG bytes."""
    # Late import so the pipeline module imports cleanly even when
    # Pillow isn't installed yet (e.g. ``pytest --collect-only``
    # during a doc-only PR).
    from PIL import Image

    x1, y1, x2, y2 = bbox
    if strategy == "lower_third":
        # Lower third of the vehicle bbox — where plates usually sit
        # on rear-facing camera angles. Cheap heuristic; documented
        # in the README as such.
        h = y2 - y1
        y1 = y1 + (2 * h) // 3

    # ``with`` so the underlying file handle (BytesIO) is closed
    # promptly — important when this runs inside a long polling loop.
    with Image.open(io.BytesIO(frame_jpeg)) as src:
        crop = src.crop((x1, y1, x2, y2))

    buf = io.BytesIO()
    # Drop alpha (P-mode / RGBA → RGB) so the JPEG encoder doesn't
    # complain about palette / alpha channels coming out of crop().
    if crop.mode not in ("RGB", "L"):
        crop = crop.convert("RGB")
    crop.save(buf, format="JPEG", quality=92)
    crop.close()
    return buf.getvalue()


def _frame_dimensions(frame_jpeg: bytes) -> tuple[int, int]:
    """Return (width, height) from a JPEG byte string."""
    from PIL import Image

    with Image.open(io.BytesIO(frame_jpeg)) as img:
        return img.size  # (width, height)

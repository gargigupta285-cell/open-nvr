# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for plate_pipeline.PlatePipeline + its parsing/cropping helpers.

These tests construct fake DetectorClient / OcrClient implementations
so the chain logic is exercised without real KAI-C HTTP.
"""
from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from plate_pipeline import (
    DEFAULT_CROP_STRATEGY,
    PlatePipeline,
    PlatePipelineConfig,
    PlateRead,
    VehicleDetection,
    _normalise_bbox_to_pixels,
    _parse_plate_read,
    _parse_vehicle_detections,
    crop_for_plate,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _solid_jpeg(width: int = 320, height: int = 240, color: tuple[int, int, int] = (180, 180, 180)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class _FakeDetector:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[bytes] = []

    def detect(self, frame_jpeg: bytes, *, correlation_id: str | None = None) -> dict[str, Any]:
        self.calls.append(frame_jpeg)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeOcr:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[bytes] = []

    def read(self, plate_jpeg: bytes, *, min_confidence: float | None = None,
             correlation_id: str | None = None) -> dict[str, Any]:
        self.calls.append(plate_jpeg)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _det_response(detections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "model_name": "yolov8",
        "model_version": "fake",
        "inference_ms": 12,
        "result": {"detections": detections},
    }


def _ocr_response(plate: str, conf: float, accepted: bool = True) -> dict[str, Any]:
    return {
        "status": "ok",
        "model_name": "fast-plate-ocr",
        "model_version": "fake",
        "inference_ms": 14,
        "result": {
            "plate_text": plate,
            "confidence": conf,
            "characters": [{"char": c, "confidence": conf} for c in plate],
            "accepted": accepted,
            "min_confidence_applied": 0.5,
            "model_id": "fake-model",
        },
    }


# ── Config validation ──────────────────────────────────────────────


def test_pipeline_config_rejects_unknown_crop_strategy():
    with pytest.raises(ValueError):
        PlatePipelineConfig(crop_strategy="middle")


def test_pipeline_config_rejects_out_of_range_thresholds():
    with pytest.raises(ValueError):
        PlatePipelineConfig(detection_confidence=1.5)
    with pytest.raises(ValueError):
        PlatePipelineConfig(ocr_confidence=-0.1)


def test_pipeline_config_rejects_empty_vehicle_labels():
    with pytest.raises(ValueError):
        PlatePipelineConfig(vehicle_labels=())


# ── Bbox normalisation ──────────────────────────────────────────────


def test_normalise_bbox_canonical_dict_xywh_normalised():
    """The wire shape YOLOv8 actually emits — §5.1 NormalizedBBox
    as a dict {x, y, w, h} with values in [0, 1]. This is the path
    that must work end-to-end."""
    bbox = _normalise_bbox_to_pixels(
        {"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.6}, 320, 240
    )
    # x1 = 0.1 * 320 = 32; y1 = 0.2 * 240 = 48
    # x2 = 0.5 * 320 = 160; y2 = 0.8 * 240 = 192
    assert bbox == (32, 48, 160, 192)


def test_normalise_bbox_dict_xyxy_normalised():
    bbox = _normalise_bbox_to_pixels(
        {"x1": 0.1, "y1": 0.2, "x2": 0.5, "y2": 0.8}, 320, 240
    )
    assert bbox == (32, 48, 160, 192)


def test_normalise_bbox_dict_xywh_pixel():
    bbox = _normalise_bbox_to_pixels(
        {"x": 50, "y": 40, "w": 100, "h": 80}, 320, 240
    )
    assert bbox == (50, 40, 150, 120)


def test_normalise_bbox_dict_missing_keys_returns_none():
    # Doesn't have a full {x,y,w,h} OR {x1,y1,x2,y2} set.
    assert _normalise_bbox_to_pixels({"x": 0.1, "y": 0.2}, 320, 240) is None


def test_normalise_bbox_treats_all_under_one_as_normalised():
    bbox = _normalise_bbox_to_pixels([0.1, 0.2, 0.5, 0.8], 320, 240)
    assert bbox == (32, 48, 160, 192)


def test_normalise_bbox_xyxy_pixel():
    bbox = _normalise_bbox_to_pixels([50, 40, 200, 220], 320, 240)
    assert bbox == (50, 40, 200, 220)


def test_normalise_bbox_xywh_pixel_detected_by_heuristic():
    # x=10, y=20, w=100, h=150 → x2=110, y2=170, fits the frame.
    bbox = _normalise_bbox_to_pixels([10, 20, 100, 150], 320, 240)
    assert bbox == (10, 20, 110, 170)


def test_normalise_bbox_clips_out_of_bounds():
    bbox = _normalise_bbox_to_pixels([10, 10, 500, 500], 320, 240)
    assert bbox == (10, 10, 320, 240)


def test_normalise_bbox_returns_none_for_degenerate():
    # xywh with zero width/height: falls through to xyxy → x2<=x1.
    assert _normalise_bbox_to_pixels([100, 100, 0, 0], 320, 240) is None
    # xyxy where x2<=x1 (xywh-detection heuristic also fails because
    # y+h would overflow): genuinely degenerate.
    assert _normalise_bbox_to_pixels([200, 200, 100, 100], 320, 240) is None


# ── Parse vehicle detections ────────────────────────────────────────


def test_parse_vehicle_detections_handles_real_yolov8_dict_bbox_shape():
    """The reference YOLOv8 adapter emits bbox as a §5.1
    NormalizedBBox dict, not a list. The pipeline must accept that
    shape end-to-end — otherwise every detection is silently dropped
    against a real adapter."""
    frame = _solid_jpeg(640, 480)
    response = _det_response([
        {
            "label": "car",
            "confidence": 0.91,
            "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
        },
    ])
    out = list(_parse_vehicle_detections(
        response,
        allowed_labels={"car"},
        min_confidence=0.4,
        frame_jpeg=frame,
    ))
    assert len(out) == 1
    assert out[0].label == "car"
    # 0.1*640=64, 0.2*480=96, (0.1+0.3)*640=256, (0.2+0.4)*480=288
    assert out[0].bbox == (64, 96, 256, 288)


def test_parse_vehicle_detections_filters_by_label_and_confidence():
    frame = _solid_jpeg(320, 240)
    response = _det_response([
        {"label": "car", "confidence": 0.92, "bbox": [10, 10, 200, 200]},
        {"label": "person", "confidence": 0.99, "bbox": [10, 10, 100, 100]},  # not a vehicle
        {"label": "truck", "confidence": 0.20, "bbox": [10, 10, 100, 100]},   # below floor
    ])
    out = list(_parse_vehicle_detections(
        response,
        allowed_labels={"car", "truck", "bus", "motorcycle"},
        min_confidence=0.4,
        frame_jpeg=frame,
    ))
    assert len(out) == 1
    assert out[0].label == "car"


def test_parse_vehicle_detections_tolerates_garbage_entries():
    frame = _solid_jpeg(320, 240)
    response = _det_response([
        {"label": "car"},  # no bbox
        "not-a-dict",
        {"label": "car", "confidence": "nope", "bbox": [10, 10, 50, 50]},  # bad conf
        {"label": "car", "confidence": 0.9, "bbox": [10, 10]},  # short bbox
        {"label": "car", "confidence": 0.9, "bbox": [10, 10, 50, 50]},  # ok
    ])
    out = list(_parse_vehicle_detections(
        response,
        allowed_labels={"car"},
        min_confidence=0.4,
        frame_jpeg=frame,
    ))
    assert len(out) == 1


# ── Parse plate read ───────────────────────────────────────────────


def test_parse_plate_read_extracts_canonical_fields():
    vehicle = VehicleDetection(label="car", confidence=0.9, bbox=(10, 10, 100, 100))
    response = _ocr_response("ABC1234", 0.93)
    read = _parse_plate_read(response, vehicle=vehicle, correlation_id="cid-1")
    assert isinstance(read, PlateRead)
    assert read.plate_text == "ABC1234"
    assert read.ocr_confidence == pytest.approx(0.93)
    assert read.correlation_id == "cid-1"
    assert read.vehicle_label == "car"


def test_parse_plate_read_drops_unaccepted_reads():
    vehicle = VehicleDetection(label="car", confidence=0.9, bbox=(10, 10, 100, 100))
    response = _ocr_response("XY99", 0.30, accepted=False)
    assert _parse_plate_read(response, vehicle=vehicle, correlation_id=None) is None


def test_parse_plate_read_drops_empty_text():
    vehicle = VehicleDetection(label="car", confidence=0.9, bbox=(10, 10, 100, 100))
    response = _ocr_response("", 0.99)
    assert _parse_plate_read(response, vehicle=vehicle, correlation_id=None) is None


# ── Cropping ────────────────────────────────────────────────────────


def test_crop_for_plate_vehicle_strategy_returns_full_bbox_size():
    frame = _solid_jpeg(320, 240)
    out = crop_for_plate(frame, (50, 60, 200, 180), strategy="vehicle")
    img = Image.open(io.BytesIO(out))
    assert img.size == (150, 120)


def test_crop_for_plate_lower_third_strategy_takes_bottom_band():
    frame = _solid_jpeg(320, 240)
    out = crop_for_plate(frame, (50, 60, 200, 180), strategy="lower_third")
    img = Image.open(io.BytesIO(out))
    # bbox height = 120, lower third = bottom 40 pixels.
    assert img.size == (150, 40)


# ── End-to-end pipeline ─────────────────────────────────────────────


def test_process_frame_yields_one_read_per_accepted_vehicle():
    frame = _solid_jpeg(320, 240)
    detector = _FakeDetector(_det_response([
        {"label": "car", "confidence": 0.91, "bbox": [10, 10, 200, 200]},
        {"label": "truck", "confidence": 0.88, "bbox": [30, 30, 280, 200]},
    ]))
    ocr = _FakeOcr(_ocr_response("ABC1234", 0.93))
    pipeline = PlatePipeline(detector, ocr, PlatePipelineConfig())

    reads = list(pipeline.process_frame(frame, correlation_id="cid-1"))
    assert len(reads) == 2
    assert all(r.plate_text == "ABC1234" for r in reads)
    assert {r.vehicle_label for r in reads} == {"car", "truck"}
    # Each vehicle should have driven one OCR call.
    assert len(ocr.calls) == 2


def test_process_frame_handles_no_vehicles_gracefully():
    frame = _solid_jpeg(320, 240)
    detector = _FakeDetector(_det_response([
        {"label": "person", "confidence": 0.99, "bbox": [10, 10, 100, 100]},
    ]))
    ocr = _FakeOcr(_ocr_response("UNUSED", 0.99))
    pipeline = PlatePipeline(detector, ocr, PlatePipelineConfig())

    reads = list(pipeline.process_frame(frame))
    assert reads == []
    # OCR must NOT be called when nothing was detected as a vehicle.
    assert ocr.calls == []


def test_process_frame_swallows_detector_failure():
    frame = _solid_jpeg(320, 240)
    detector = _FakeDetector(RuntimeError("upstream YOLOv8 timeout"))
    ocr = _FakeOcr(_ocr_response("X", 0.9))
    pipeline = PlatePipeline(detector, ocr, PlatePipelineConfig())
    # Drops the frame, returns no reads, doesn't propagate.
    assert list(pipeline.process_frame(frame)) == []
    assert ocr.calls == []


def test_process_frame_skips_single_vehicle_on_ocr_failure():
    frame = _solid_jpeg(320, 240)
    detector = _FakeDetector(_det_response([
        {"label": "car", "confidence": 0.91, "bbox": [10, 10, 200, 200]},
    ]))
    ocr = _FakeOcr(RuntimeError("OCR adapter died"))
    pipeline = PlatePipeline(detector, ocr, PlatePipelineConfig())
    # OCR failure is logged but doesn't kill the loop.
    assert list(pipeline.process_frame(frame)) == []

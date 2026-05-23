# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for FaceRecognitionPipeline + its parser.

The pipeline calls a RecognitionClient (HTTP to KAI-C in production)
and parses an InferResponse-shaped dict. Tests use a fake client so
no network is required.
"""
from __future__ import annotations

from typing import Any

import pytest

from face_recognition_pipeline import (
    DEFAULT_RECOGNITION_THRESHOLD,
    FaceRead,
    FaceRecognitionPipeline,
    FaceRecognitionPipelineConfig,
    _parse_bbox,
    _parse_recognition_response,
)


class _FakeClient:
    def __init__(self, response: dict[str, Any] | Exception):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def recognize(self, frame_jpeg: bytes, *, threshold: float,
                  correlation_id: str | None = None) -> dict[str, Any]:
        self.calls.append(
            {"threshold": threshold, "correlation_id": correlation_id}
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _wrap(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "model_name": "insightface",
        "model_version": "fake",
        "inference_ms": 12,
        "result": result,
    }


# ── Config validation ──────────────────────────────────────────────


@pytest.mark.parametrize("bad", [-0.01, 0.0, 1.01, 2.0])
def test_pipeline_config_rejects_out_of_range_threshold(bad):
    with pytest.raises(ValueError):
        FaceRecognitionPipelineConfig(recognition_threshold=bad)


def test_pipeline_config_accepts_default():
    cfg = FaceRecognitionPipelineConfig()
    assert cfg.recognition_threshold == DEFAULT_RECOGNITION_THRESHOLD


# ── Parser ─────────────────────────────────────────────────────────


def test_parse_recognised_face():
    read = _parse_recognition_response(
        _wrap({
            "task": "face_recognition",
            "recognized": True,
            "person_id": "alice",
            "name": "Alice Smith",
            "category": "family",
            "similarity": 0.92,
            "face_bbox": [100, 80, 240, 240],
            "threshold": 0.5,
        }),
        correlation_id="cid-1",
    )
    assert isinstance(read, FaceRead)
    assert read.recognized is True
    assert read.face_detected is True
    assert read.person_id == "alice"
    assert read.name == "Alice Smith"
    assert read.category == "family"
    assert read.similarity == pytest.approx(0.92, rel=1e-3)
    assert read.face_bbox == (100, 80, 240, 240)
    assert read.threshold == pytest.approx(0.5)
    assert read.correlation_id == "cid-1"


def test_parse_unknown_face_with_bbox():
    """Face seen but no DB match → ``face_detected=True, recognized=False``."""
    read = _parse_recognition_response(
        _wrap({
            "task": "face_recognition",
            "recognized": False,
            "face_bbox": [50, 50, 150, 200],
            "message": "no match above threshold 0.5",
            "threshold": 0.5,
        }),
        correlation_id="cid-2",
    )
    assert read is not None
    assert read.face_detected is True
    assert read.recognized is False
    assert read.face_bbox == (50, 50, 150, 200)


def test_parse_no_face_detected():
    """No bbox in the response → face_detected=False."""
    read = _parse_recognition_response(
        _wrap({
            "task": "face_recognition",
            "recognized": False,
            "face_bbox": None,
            "message": "no face detected",
        }),
        correlation_id=None,
    )
    assert read is not None
    assert read.face_detected is False
    assert read.recognized is False


def test_parse_returns_none_for_garbage_response():
    assert _parse_recognition_response("not-a-dict", correlation_id=None) is None
    assert _parse_recognition_response({}, correlation_id=None) is None
    assert _parse_recognition_response({"result": "not-a-dict"}, correlation_id=None) is None


def test_parse_tolerates_missing_similarity():
    """The contract says similarity is required when recognized=true,
    but the parser is lenient — better a typed read with None than
    a dropped frame."""
    read = _parse_recognition_response(
        _wrap({
            "task": "face_recognition",
            "recognized": True,
            "person_id": "bob",
            "name": "Bob Jones",
            "category": "friend",
            # similarity intentionally missing
            "face_bbox": [10, 10, 100, 100],
            "threshold": 0.5,
        }),
        correlation_id=None,
    )
    assert read.recognized is True
    assert read.similarity is None


# ── bbox helper ────────────────────────────────────────────────────


def test_parse_bbox_rejects_degenerate():
    assert _parse_bbox(None) is None
    assert _parse_bbox([10, 10, 10, 10]) is None
    assert _parse_bbox([10, 10, 5, 5]) is None
    assert _parse_bbox([1, 2, 3]) is None  # length 3
    assert _parse_bbox("not a list") is None


def test_parse_bbox_handles_floats_via_int_coercion():
    assert _parse_bbox([10.0, 20.5, 100.9, 200.0]) == (10, 20, 100, 200)


# ── Pipeline.process_frame ─────────────────────────────────────────


def test_process_frame_returns_recognised_face():
    client = _FakeClient(_wrap({
        "task": "face_recognition",
        "recognized": True,
        "person_id": "alice",
        "name": "Alice Smith",
        "category": "family",
        "similarity": 0.88,
        "face_bbox": [10, 20, 100, 200],
        "threshold": 0.5,
    }))
    pipeline = FaceRecognitionPipeline(client)

    read = pipeline.process_frame(b"\xff\xd8jpeg", correlation_id="cid-x")
    assert read.recognized is True
    assert read.person_id == "alice"
    # Pipeline must thread the configured threshold AND the per-call
    # correlation_id down to the client.
    assert client.calls == [
        {"threshold": DEFAULT_RECOGNITION_THRESHOLD, "correlation_id": "cid-x"}
    ]


def test_process_frame_swallows_client_failure():
    """Recognition call dies → return None, log, don't crash the loop."""
    client = _FakeClient(RuntimeError("upstream KAI-C 503"))
    pipeline = FaceRecognitionPipeline(client)
    assert pipeline.process_frame(b"\xff\xd8jpeg") is None


def test_process_frame_uses_configured_threshold():
    client = _FakeClient(_wrap({
        "task": "face_recognition",
        "recognized": False,
        "face_bbox": None,
        "threshold": 0.75,
    }))
    pipeline = FaceRecognitionPipeline(
        client, FaceRecognitionPipelineConfig(recognition_threshold=0.75)
    )
    pipeline.process_frame(b"\xff\xd8jpeg")
    assert client.calls[0]["threshold"] == pytest.approx(0.75)

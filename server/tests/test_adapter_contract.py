# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the contract-v1 request/response bridge and the persistent
capture pool — no OpenCV, no RTSP, no running adapter."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

# Make server/services importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))

import adapter_contract as ac  # noqa: E402
from frame_capture import PersistentCapturePool  # noqa: E402


# ── build_infer_payload ────────────────────────────────────────────


def test_build_payload_is_contract_shaped():
    body = ac.build_infer_payload(task="object_detection", jpeg_bytes=b"\xff\xd8jpeg")
    assert body["task"] == "object_detection"
    assert "frame_b64" in body
    assert base64.b64decode(body["frame_b64"]) == b"\xff\xd8jpeg"


def test_build_payload_includes_params_at_top_level():
    body = ac.build_infer_payload(
        task="open_vocab_detection", jpeg_bytes=b"x",
        params={"queries": ["red truck"], "threshold": 0.2},
    )
    assert body["queries"] == ["red truck"]
    assert body["threshold"] == 0.2


def test_build_payload_threads_camera_id_top_level():
    # In governed mode the server adds camera_id as a param; KAI-C's v1
    # route reads it at the top level for the NATS subject + audit.
    body = ac.build_infer_payload(
        task="object_detection", jpeg_bytes=b"x",
        params={"camera_id": "cam-dock", "confidence_threshold": 0.4},
    )
    assert body["camera_id"] == "cam-dock"
    assert body["confidence_threshold"] == 0.4


def test_build_payload_rejects_reserved_param():
    with pytest.raises(ValueError):
        ac.build_infer_payload(task="t", jpeg_bytes=b"x", params={"frame_b64": "nope"})


def test_build_payload_rejects_empty_frame():
    with pytest.raises(ValueError):
        ac.build_infer_payload(task="t", jpeg_bytes=b"")


# ── flatten_infer_response ─────────────────────────────────────────


def test_flatten_detection_response():
    resp = {
        "model_name": "yolov8", "inference_ms": 42,
        "result": {"detections": [
            {"label": "person", "confidence": 0.6, "bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.3}},
            {"label": "car", "confidence": 0.9, "bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}},
        ]},
    }
    flat = ac.flatten_infer_response(resp)
    assert flat["count"] == 2
    assert flat["label"] == "car"          # highest-confidence detection
    assert flat["confidence"] == 0.9
    assert flat["bbox"] == [0.5, 0.5, 0.1, 0.1]
    assert flat["latency_ms"] == 42
    assert flat["model_name"] == "yolov8"


def test_flatten_empty_detections_is_zero_confidence():
    flat = ac.flatten_infer_response({"result": {"detections": []}})
    assert flat["confidence"] == 0.0
    assert flat["count"] == 0


def test_flatten_caption_response():
    flat = ac.flatten_infer_response({"result": {"caption": "a red truck at the dock"}})
    assert flat["caption"] == "a red truck at the dock"


def test_flatten_tolerates_legacy_flat_response():
    legacy = {"label": "person", "confidence": 0.7, "bbox": [1, 2, 3, 4]}
    flat = ac.flatten_infer_response(legacy)
    assert flat["label"] == "person" and flat["confidence"] == 0.7


def test_flatten_bbox_from_list():
    resp = {"result": {"detections": [
        {"label": "x", "confidence": 0.5, "bbox": [0.1, 0.2, 0.3, 0.4]}]}}
    assert ac.flatten_infer_response(resp)["bbox"] == [0.1, 0.2, 0.3, 0.4]


# ── PersistentCapturePool ──────────────────────────────────────────


class _FakeCapture:
    """A fake cv2 capture that yields a fixed number of frames then fails."""

    instances: list["_FakeCapture"] = []

    def __init__(self, url, frames=3, opened=True):
        self.url = url
        self._frames = frames
        self._opened = opened
        self.released = False
        _FakeCapture.instances.append(self)

    def isOpened(self):
        return self._opened

    def read(self):
        if self._frames <= 0:
            return False, None
        self._frames -= 1
        return True, f"frame-from-{self.url}"

    def release(self):
        self.released = True


def _pool(frames=3, opened=True):
    _FakeCapture.instances = []
    return PersistentCapturePool(
        capture_factory=lambda url: _FakeCapture(url, frames=frames, opened=opened),
        encode_jpeg=lambda frame, q: f"jpeg:{frame}".encode(),
    )


def test_pool_reuses_one_capture_across_frames():
    pool = _pool(frames=5)
    a = pool.get_jpeg(1, "rtsp://cam1")
    b = pool.get_jpeg(1, "rtsp://cam1")
    assert a == b"jpeg:frame-from-rtsp://cam1"
    assert b is not None
    # Only ONE capture opened for two grabs — no per-frame reconnect.
    assert len(_FakeCapture.instances) == 1


def test_pool_reopens_on_read_failure():
    pool = _pool(frames=1)            # first capture yields 1 frame then fails
    assert pool.get_jpeg(1, "rtsp://cam1") is not None     # frame 1
    # Second call: first read fails → transparent reopen → new capture's frame
    assert pool.get_jpeg(1, "rtsp://cam1") is not None
    assert len(_FakeCapture.instances) == 2                # reopened once


def test_pool_reopens_when_url_changes():
    pool = _pool(frames=5)
    pool.get_jpeg(1, "rtsp://cam1?jwt=A")
    pool.get_jpeg(1, "rtsp://cam1?jwt=B")   # rotated token → reopen
    assert len(_FakeCapture.instances) == 2
    assert _FakeCapture.instances[0].released is True


def test_pool_unopened_capture_returns_none():
    pool = _pool(opened=False)
    assert pool.get_jpeg(1, "rtsp://cam1") is None


def test_pool_release_frees_capture():
    pool = _pool(frames=5)
    pool.get_jpeg(1, "rtsp://cam1")
    pool.release(1)
    assert _FakeCapture.instances[0].released is True

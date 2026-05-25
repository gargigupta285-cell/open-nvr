# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pipeline tests — config validation, response parsing, ROI filtering,
IoU tracker behaviour. Decoupled from the orchestrator so any of these
can fail without dragging the whole test suite down."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from package_pipeline import (
    DEFAULT_DETECTION_CONFIDENCE,
    DEFAULT_IOU_THRESHOLD,
    Detection,
    IouTracker,
    PackagePipeline,
    PackagePipelineConfig,
    Roi,
    _iou,
    _normalise_bbox_to_pixels,
)


def _make_jpeg(width: int = 320, height: int = 240, colour=(180, 180, 180)) -> bytes:
    """Generate a real decodable JPEG via Pillow. The pipeline needs
    Pillow to read frame dimensions, so the bytes must be valid."""
    img = Image.new("RGB", (width, height), colour)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── Pipeline config ────────────────────────────────────────────────


def test_config_rejects_empty_package_labels():
    with pytest.raises(ValueError, match="package_labels"):
        PackagePipelineConfig(package_labels=())


def test_config_rejects_detection_confidence_out_of_range():
    with pytest.raises(ValueError, match="detection_confidence"):
        PackagePipelineConfig(detection_confidence=1.5)


def test_config_allows_empty_person_labels():
    """Empty person_labels intentionally disables the porch-pirate
    heuristic — the orchestrator falls back to a single severity."""
    cfg = PackagePipelineConfig(person_labels=())
    assert cfg.person_labels == ()


# ── ROI ────────────────────────────────────────────────────────────


def test_roi_parse_returns_none_for_unset():
    assert Roi.parse(None) is None
    assert Roi.parse("") is None
    assert Roi.parse([]) is None


def test_roi_parse_aabb_shortcut():
    roi = Roi.parse([0.1, 0.2, 0.8, 0.9])
    assert roi is not None
    assert len(roi.polygon) == 4


def test_roi_parse_polygon_requires_3_points():
    with pytest.raises(ValueError, match="3 points"):
        Roi.parse([[0.0, 0.0], [1.0, 1.0]])


def test_roi_parse_rejects_invalid_aabb():
    with pytest.raises(ValueError, match="x1 < x2"):
        Roi.parse([0.5, 0.5, 0.4, 0.9])


def test_roi_contains_centroid_inside():
    roi = Roi.parse([0.0, 0.0, 0.5, 0.5])
    assert roi is not None
    # bbox centered at (80, 60) in a 320x240 frame → centroid (0.25, 0.25) → inside
    det = Detection(label="suitcase", confidence=0.9, bbox=(60, 40, 100, 80))
    assert roi.contains_centroid(det, (320, 240)) is True


def test_roi_contains_centroid_outside():
    roi = Roi.parse([0.0, 0.0, 0.4, 0.4])
    assert roi is not None
    # bbox centered at (240, 180) → centroid (0.75, 0.75) → outside
    det = Detection(label="suitcase", confidence=0.9, bbox=(220, 160, 260, 200))
    assert roi.contains_centroid(det, (320, 240)) is False


# ── Bbox normaliser ─────────────────────────────────────────────────


def test_bbox_canonical_normalised_dict():
    bbox = _normalise_bbox_to_pixels(
        {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, 100, 100
    )
    assert bbox == (10, 20, 40, 60)


def test_bbox_xyxy_dict_normalised():
    bbox = _normalise_bbox_to_pixels(
        {"x1": 0.1, "y1": 0.2, "x2": 0.4, "y2": 0.6}, 100, 100
    )
    assert bbox == (10, 20, 40, 60)


def test_bbox_xyxy_list_pixel():
    """A pixel list that COULDN'T be xywh (x2 + w would exceed frame)
    forces the heuristic into xyxy. Tests with values that satisfy
    BOTH interpretations end up testing the heuristic's choice rather
    than the parser's correctness — use forcing values."""
    # [10, 20, 150, 180] in a 100x100 frame: c=150 > width so the
    # 'is this xywh?' branch falls through to xyxy. After bounds
    # clipping, (10, 20, 100, 100).
    bbox = _normalise_bbox_to_pixels([10, 20, 150, 180], 100, 100)
    assert bbox == (10, 20, 100, 100)


def test_bbox_degenerate_returns_none():
    """[0, 0, 0, 0] is truly degenerate under either interpretation:
    xywh = zero-size box; xyxy = x2<=x1 reject. Use this rather than
    [50, 50, 50, 50] which is ambiguous (valid 50x50 box under xywh,
    degenerate under xyxy)."""
    assert _normalise_bbox_to_pixels([0, 0, 0, 0], 100, 100) is None


def test_bbox_clips_to_frame_bounds():
    bbox = _normalise_bbox_to_pixels([-10, -10, 200, 200], 100, 100)
    assert bbox == (0, 0, 100, 100)


# ── Pipeline integration ───────────────────────────────────────────


class _FakeDetector:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def detect(self, frame_jpeg, *, correlation_id=None):
        self.calls.append({"len": len(frame_jpeg), "correlation_id": correlation_id})
        return self.response


def test_pipeline_filters_to_package_labels():
    response = {
        "result": {
            "detections": [
                {"label": "suitcase", "confidence": 0.9,
                 "bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
                {"label": "car", "confidence": 0.95,
                 "bbox": {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}},
                {"label": "person", "confidence": 0.85,
                 "bbox": {"x": 0.6, "y": 0.1, "w": 0.1, "h": 0.3}},
            ]
        }
    }
    pipeline = PackagePipeline(_FakeDetector(response))
    reads = pipeline.process_frame(_make_jpeg(), correlation_id="cid-1")
    assert reads is not None
    assert len(reads.packages) == 1
    assert reads.packages[0].label == "suitcase"
    assert len(reads.persons) == 1
    assert reads.persons[0].label == "person"


def test_pipeline_drops_sub_confidence_detections():
    response = {
        "result": {
            "detections": [
                {"label": "suitcase", "confidence": 0.10,
                 "bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
            ]
        }
    }
    cfg = PackagePipelineConfig(detection_confidence=0.5)
    pipeline = PackagePipeline(_FakeDetector(response), config=cfg)
    reads = pipeline.process_frame(_make_jpeg())
    assert reads is not None
    assert reads.packages == ()


def test_pipeline_applies_roi_filter():
    # Two suitcases — one inside ROI [0, 0, 0.5, 0.5], one outside.
    response = {
        "result": {
            "detections": [
                {"label": "suitcase", "confidence": 0.9,
                 "bbox": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}},  # inside
                {"label": "suitcase", "confidence": 0.9,
                 "bbox": {"x": 0.7, "y": 0.7, "w": 0.1, "h": 0.1}},  # outside
            ]
        }
    }
    roi = Roi.parse([0.0, 0.0, 0.5, 0.5])
    pipeline = PackagePipeline(_FakeDetector(response))
    reads = pipeline.process_frame(_make_jpeg(), roi=roi)
    assert reads is not None
    assert len(reads.packages) == 1


def test_pipeline_detector_error_returns_none():
    class _Boom:
        def detect(self, frame_jpeg, *, correlation_id=None):
            raise RuntimeError("simulated KAI-C 503")

    pipeline = PackagePipeline(_Boom())
    assert pipeline.process_frame(_make_jpeg()) is None


def test_pipeline_garbage_response_yields_empty():
    pipeline = PackagePipeline(_FakeDetector({"unexpected": "shape"}))
    reads = pipeline.process_frame(_make_jpeg())
    assert reads is not None
    assert reads.packages == ()
    assert reads.persons == ()


# ── IoU helper + tracker ───────────────────────────────────────────


def test_iou_disjoint_bboxes():
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_identical_bboxes():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_partial_overlap():
    a = (0, 0, 10, 10)
    b = (5, 5, 15, 15)
    # Intersection 5x5=25; union 100+100-25 = 175. IoU = 25/175 ≈ 0.142857
    assert _iou(a, b) == pytest.approx(25.0 / 175.0, rel=1e-4)


def test_tracker_new_detection_creates_track():
    tracker = IouTracker(iou_threshold=0.3)
    det = Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))
    matched, missed = tracker.update([det], now=1.0)
    assert len(matched) == 1
    assert missed == []
    track = tracker.tracks[matched[0]]
    assert track.hits == 1


def test_tracker_matches_overlapping_detection_next_frame():
    tracker = IouTracker(iou_threshold=0.3)
    det1 = Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))
    matched1, _ = tracker.update([det1], now=1.0)

    # Frame 2: same suitcase, drifted slightly.
    det2 = Detection(label="suitcase", confidence=0.9, bbox=(12, 11, 52, 51))
    matched2, missed2 = tracker.update([det2], now=2.0)
    assert matched2 == matched1  # same track id
    assert missed2 == []
    assert tracker.tracks[matched2[0]].hits == 2


def test_tracker_unmatched_detections_become_new_tracks():
    tracker = IouTracker(iou_threshold=0.3)
    tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=1.0,
    )
    # Disjoint bbox → new track.
    matched, _ = tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(200, 200, 250, 250))],
        now=2.0,
    )
    assert len(tracker.tracks) == 2


def test_tracker_label_mismatch_does_not_match():
    """A 'person' detection should never match a 'suitcase' track even
    with full bbox overlap — labels are independent state machines."""
    tracker = IouTracker(iou_threshold=0.3)
    tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=1.0,
    )
    matched, missed = tracker.update(
        [Detection(label="person", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=2.0,
    )
    assert len(tracker.tracks) == 2
    assert len(missed) == 1  # suitcase track missed


def test_tracker_bumps_misses_for_unmatched_track():
    tracker = IouTracker(iou_threshold=0.3)
    tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=1.0,
    )
    matched, missed = tracker.update([], now=2.0)
    assert matched == []
    assert len(missed) == 1
    assert tracker.tracks[missed[0]].misses == 1


def test_tracker_drop_removes_track():
    tracker = IouTracker(iou_threshold=0.3)
    matched, _ = tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=1.0,
    )
    tracker.drop(matched[0])
    assert tracker.tracks == {}


def test_tracker_assigns_best_iou_pair_regardless_of_detection_order():
    """Greedy matching by descending IoU — the detection with the
    higher overlap wins the existing track, even when it appears
    second in the detection list. Order-dependent matching is the
    failure mode this test pins."""
    tracker = IouTracker(iou_threshold=0.3)
    tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(10, 10, 50, 50))],
        now=1.0,
    )
    original_id = next(iter(tracker.tracks))

    # Two new detections, both overlapping the original track AND
    # both above the 0.3 IoU threshold. The FIRST one (mid-overlap
    # ≈0.39) should NOT win; the SECOND one (near-identical ≈0.92)
    # should win. Order-dependent matching would assign the first
    # detection it visits, which is the bug we're pinning.
    d_mid = Detection(label="suitcase", confidence=0.9, bbox=(20, 20, 60, 60))
    d_close = Detection(label="suitcase", confidence=0.9, bbox=(11, 11, 51, 51))
    matched, missed = tracker.update([d_mid, d_close], now=2.0)
    assert len(tracker.tracks) == 2  # one rematch + one new
    # The original track keeps its id and now has the near-identical
    # bbox, not the mid-overlap one.
    survivor = tracker.tracks[original_id]
    assert survivor.bbox == (11, 11, 51, 51)
    assert survivor.hits == 2


def test_tracker_threshold_boundary_inclusive():
    """A pair with IoU exactly equal to the threshold counts as a
    match (>=), matching the README's threshold language.

    Two identical boxes have IoU = 1.0; with threshold = 1.0 the
    boundary case is trivially constructible.
    """
    tracker = IouTracker(iou_threshold=1.0)
    tracker.update(
        [Detection(label="suitcase", confidence=0.9, bbox=(0, 0, 10, 10))],
        now=1.0,
    )
    det = Detection(label="suitcase", confidence=0.9, bbox=(0, 0, 10, 10))
    matched, missed = tracker.update([det], now=2.0)
    assert len(tracker.tracks) == 1  # rematched, no new track
    assert missed == []


def test_tracker_invalid_threshold_rejected():
    with pytest.raises(ValueError, match="iou_threshold"):
        IouTracker(iou_threshold=0.0)
    with pytest.raises(ValueError, match="iou_threshold"):
        IouTracker(iou_threshold=1.5)

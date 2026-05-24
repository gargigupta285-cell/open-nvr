# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The detect → ROI-filter → track pipeline for the package-delivery
example.

Stage 1 — Detection
    POST the full porch frame to YOLOv8 via KAI-C. The adapter returns
    detections in §5.1 ``InferResponse`` shape. We filter to:

    * the configured ``package_labels`` (default: COCO ``suitcase`` /
      ``backpack`` / ``handbag``), and
    * the configured ``person_labels`` (default: COCO ``person``) —
      kept separate because they drive different downstream behaviour.

Stage 2 — ROI filter
    If the camera has a porch ROI configured, drop detections whose
    centroid falls outside the polygon (or AABB). The ROI is the
    operator's "this is my porch" hint that keeps a delivery truck
    rolling past from registering as an arrival.

Stage 3 — Tracking
    Thread detections across consecutive frames with a greedy IoU
    matcher. Each track gets a stable id; the orchestrator's state
    machine reads tracks frame-to-frame to decide when a package has
    *arrived* (seen N frames in a row) and when it's *gone* (missing
    M frames in a row).

The pipeline is HTTP-only (no WebSocket streaming). Package arrival
isn't latency-critical (the cardboard box isn't going anywhere in
under a second), so the simpler HTTP-poll shape is the right
default — matches LPR and Smart Doorbell.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)


DEFAULT_PACKAGE_LABELS: tuple[str, ...] = ("suitcase", "backpack", "handbag")
DEFAULT_PERSON_LABELS: tuple[str, ...] = ("person",)
DEFAULT_DETECTION_CONFIDENCE: float = 0.35
DEFAULT_IOU_THRESHOLD: float = 0.30


# ── Wire shapes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Detection:
    """A single detection from the upstream YOLOv8 call, already
    normalised to pixel coordinates."""
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) in pixels

    @property
    def centroid(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass(frozen=True)
class FrameReads:
    """All detections in one frame, split by category. The orchestrator
    consumes one ``FrameReads`` per camera per poll cycle."""
    packages: tuple[Detection, ...]
    persons: tuple[Detection, ...]
    frame_size: tuple[int, int]  # (width, height) in pixels
    correlation_id: str | None = None


# ── Client protocol ────────────────────────────────────────────────


class DetectorClient(Protocol):
    def detect(
        self, frame_jpeg: bytes, *, correlation_id: str | None = None
    ) -> dict[str, Any]:
        """Run object detection on a frame. Returns the raw §5.1
        ``InferResponse`` body — the pipeline parses out detections
        from ``result.detections``."""


# ── ROI ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Roi:
    """A porch ROI in normalised coordinates [0..1].

    Accepts either a polygon (3+ points) or an axis-aligned rectangle
    given as ``[x1, y1, x2, y2]``. Internally both shapes resolve to
    a polygon so the point-in-polygon test handles them uniformly.
    """
    polygon: tuple[tuple[float, float], ...]

    @classmethod
    def parse(cls, raw: Any) -> "Roi | None":
        """Parse a config value into an ``Roi`` or return ``None`` for
        empty / unset. Raises ``ValueError`` on a malformed shape."""
        if raw is None or raw == "":
            return None
        if not isinstance(raw, list):
            raise ValueError("roi must be a list of [x, y] points or a [x1, y1, x2, y2] rect")
        if not raw:
            return None

        # AABB shortcut — four numbers in a flat list.
        if len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw):
            x1, y1, x2, y2 = (float(v) for v in raw)
            if x2 <= x1 or y2 <= y1:
                raise ValueError("roi rect must satisfy x1 < x2 and y1 < y2")
            poly = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
            return cls(polygon=poly)

        # Polygon — list of [x, y] points.
        points: list[tuple[float, float]] = []
        for entry in raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError("roi polygon points must be [x, y] pairs")
            x, y = float(entry[0]), float(entry[1])
            points.append((x, y))
        if len(points) < 3:
            raise ValueError("roi polygon needs at least 3 points")
        return cls(polygon=tuple(points))

    def contains_centroid(self, det: Detection, frame_size: tuple[int, int]) -> bool:
        """Return True if the detection's centroid falls inside the ROI.
        ``frame_size`` is (width, height) in pixels — needed to map the
        pixel-coordinate centroid into the normalised ROI space."""
        width, height = frame_size
        if width <= 0 or height <= 0:
            return True  # degenerate; don't reject
        cx, cy = det.centroid
        nx, ny = cx / float(width), cy / float(height)
        return _point_in_polygon(nx, ny, self.polygon)


def _point_in_polygon(x: float, y: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray-cast point-in-polygon. Edge cases (point on edge) fall in
    one direction consistently — fine for an ROI hint."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


# ── Pipeline config + driver ───────────────────────────────────────


@dataclass
class PackagePipelineConfig:
    """Per-camera tuning knobs the orchestrator passes in."""
    package_labels: tuple[str, ...] = DEFAULT_PACKAGE_LABELS
    person_labels: tuple[str, ...] = DEFAULT_PERSON_LABELS
    detection_confidence: float = DEFAULT_DETECTION_CONFIDENCE

    def __post_init__(self) -> None:
        if not 0.0 <= self.detection_confidence <= 1.0:
            raise ValueError(
                f"detection_confidence must be in [0,1]; got {self.detection_confidence}"
            )
        if not self.package_labels:
            raise ValueError("package_labels must be non-empty")
        # person_labels MAY be empty — disables the porch-pirate heuristic.


class PackagePipeline:
    """Wraps the detector client + parses + applies the ROI filter for
    one frame at a time."""

    def __init__(
        self,
        detector: DetectorClient,
        config: PackagePipelineConfig | None = None,
    ) -> None:
        self.detector = detector
        self.config = config or PackagePipelineConfig()

    def process_frame(
        self,
        frame_jpeg: bytes,
        *,
        roi: Roi | None = None,
        correlation_id: str | None = None,
    ) -> FrameReads | None:
        """Run one detection pass. Returns ``None`` only when the
        detector raises (network / KAI-C error). An empty-but-valid
        frame returns ``FrameReads`` with empty tuples — the
        orchestrator state machine treats that as a no-detection tick."""
        try:
            response = self.detector.detect(
                frame_jpeg, correlation_id=correlation_id
            )
        except Exception:
            logger.exception("package detection: detector call failed")
            return None

        try:
            frame_size = _frame_dimensions(frame_jpeg)
        except Exception:
            logger.exception("package detection: could not read frame dimensions")
            return None

        package_set = {lbl.lower() for lbl in self.config.package_labels}
        person_set = {lbl.lower() for lbl in self.config.person_labels}

        packages: list[Detection] = []
        persons: list[Detection] = []
        for det in _parse_detections(
            response,
            min_confidence=self.config.detection_confidence,
            frame_size=frame_size,
        ):
            label = det.label.lower()
            if label in package_set:
                if roi is None or roi.contains_centroid(det, frame_size):
                    packages.append(det)
            elif label in person_set:
                # Persons are only used for the porch-pirate / pickup
                # heuristic, so we ALSO filter them by ROI — a person
                # walking past on the sidewalk isn't a pickup.
                if roi is None or roi.contains_centroid(det, frame_size):
                    persons.append(det)

        return FrameReads(
            packages=tuple(packages),
            persons=tuple(persons),
            frame_size=frame_size,
            correlation_id=correlation_id,
        )


# ── Tracking ───────────────────────────────────────────────────────


@dataclass
class _TrackState:
    """Mutable bookkeeping for one tracked detection across frames."""
    track_id: str
    label: str
    bbox: tuple[int, int, int, int]
    confidence: float
    hits: int = 1
    misses: int = 0
    first_seen_at: float = 0.0
    last_seen_at: float = 0.0
    # Custom flags the orchestrator's state machine can set / read.
    state: str = "new"           # new → arrived → lingering → gone
    arrived_at: float | None = None
    linger_alert_fired: bool = False


class IouTracker:
    """Greedy IoU tracker for one camera. Stateful across frames.

    Per ``update()`` call:
    1. For each new detection, find the existing track with the
       highest IoU above ``iou_threshold``. Greedy match — once a
       track is taken, it's off the table for the rest of this call.
    2. Unmatched detections become new tracks.
    3. Unmatched tracks have their ``misses`` counter bumped.

    Track ids are short uuids — stable for the lifetime of the track.
    """

    def __init__(self, *, iou_threshold: float = DEFAULT_IOU_THRESHOLD) -> None:
        if not 0.0 < iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be in (0, 1]")
        self._iou_threshold = iou_threshold
        self._tracks: dict[str, _TrackState] = {}
        self._next_index = 0

    @property
    def tracks(self) -> dict[str, _TrackState]:
        return self._tracks

    def update(
        self,
        detections: Iterable[Detection],
        *,
        now: float,
    ) -> tuple[list[str], list[str]]:
        """Match detections to tracks. Returns
        ``(matched_track_ids, missed_track_ids)``.

        Matching strategy: compute IoU for every (detection, track)
        pair with matching labels, then greedily assign the highest-
        IoU pair first. This avoids the detection-order failure mode
        where two boxes both overlap one track and the order they
        appear in the response decides which one "wins" — the better
        IoU should always win regardless of order.
        """
        det_list = list(detections)
        unmatched_tracks = set(self._tracks.keys())
        unmatched_det_indices = set(range(len(det_list)))
        matched_ids: list[str] = []

        # Build (iou, det_idx, track_id) tuples for label-compatible
        # pairs above the threshold. Sort by descending IoU and assign
        # greedily — each detection and track can match at most once.
        candidates: list[tuple[float, int, str]] = []
        for det_idx, det in enumerate(det_list):
            for tid, track in self._tracks.items():
                if track.label != det.label:
                    continue
                iou = _iou(track.bbox, det.bbox)
                if iou >= self._iou_threshold:
                    candidates.append((iou, det_idx, tid))
        candidates.sort(key=lambda c: c[0], reverse=True)

        for iou, det_idx, tid in candidates:
            if det_idx not in unmatched_det_indices:
                continue
            if tid not in unmatched_tracks:
                continue
            det = det_list[det_idx]
            track = self._tracks[tid]
            track.bbox = det.bbox
            track.confidence = det.confidence
            track.hits += 1
            track.misses = 0
            track.last_seen_at = now
            unmatched_tracks.discard(tid)
            unmatched_det_indices.discard(det_idx)
            matched_ids.append(tid)

        # Unmatched detections → new tracks.
        for det in [det_list[i] for i in sorted(unmatched_det_indices)]:
            self._next_index += 1
            tid = f"trk_{self._next_index:06d}"
            self._tracks[tid] = _TrackState(
                track_id=tid,
                label=det.label,
                bbox=det.bbox,
                confidence=det.confidence,
                first_seen_at=now,
                last_seen_at=now,
            )
            matched_ids.append(tid)

        # Bump misses for tracks that didn't match this frame.
        missed_ids = list(unmatched_tracks)
        for tid in missed_ids:
            self._tracks[tid].misses += 1

        return matched_ids, missed_ids

    def drop(self, track_id: str) -> None:
        self._tracks.pop(track_id, None)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union for two axis-aligned bboxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / float(union)


# ── Parsing helpers ────────────────────────────────────────────────


def _parse_detections(
    response: dict[str, Any],
    *,
    min_confidence: float,
    frame_size: tuple[int, int],
) -> Iterable[Detection]:
    """Walk a §5.1 InferResponse + filter detections above the
    confidence floor, converting bboxes to pixels."""
    if not isinstance(response, dict):
        return
    result = response.get("result")
    if not isinstance(result, dict):
        return
    detections = result.get("detections") or result.get("objects") or []
    if not isinstance(detections, list):
        return

    width, height = frame_size

    for det in detections:
        if not isinstance(det, dict):
            continue
        label = str(det.get("label") or det.get("class") or "").strip().lower()
        if not label:
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

        yield Detection(label=label, confidence=confidence, bbox=bbox)


def _normalise_bbox_to_pixels(
    bbox_raw: Any,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Coerce a bbox into pixel-coordinate (x1, y1, x2, y2).

    Mirrors the license-plate-recognition adapter's helper — the
    canonical contract shape is the dict ``{"x", "y", "w", "h"}``
    normalised, but we accept the few list shapes third-party
    detectors emit too.
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
    elif isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
        values = [float(v) for v in bbox_raw]
        if all(0.0 <= v <= 1.0 for v in values):
            x1 = int(values[0] * width)
            y1 = int(values[1] * height)
            x2 = int(values[2] * width)
            y2 = int(values[3] * height)
        else:
            a, b, c, d = values
            # Heuristic xywh-vs-xyxy in pixel space (same heuristic
            # as LPR's helper — documented as such).
            if 0 < c <= width and 0 < d <= height and a + c <= width + 1 and b + d <= height + 1:
                x1, y1 = int(a), int(b)
                x2, y2 = int(a + c), int(b + d)
            else:
                x1, y1, x2, y2 = int(a), int(b), int(c), int(d)
    else:
        return None

    x1 = max(0, min(x1, width))
    x2 = max(0, min(x2, width))
    y1 = max(0, min(y1, height))
    y2 = max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _frame_dimensions(frame_jpeg: bytes) -> tuple[int, int]:
    """Return (width, height) from a JPEG byte string."""
    # Late import keeps the module importable in test environments
    # that don't have Pillow yet (e.g. pytest --collect-only).
    from PIL import Image

    with Image.open(io.BytesIO(frame_jpeg)) as img:
        return img.size  # (width, height)

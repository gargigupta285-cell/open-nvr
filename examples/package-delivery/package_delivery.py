# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
package-delivery — watch a porch ROI for package arrivals and
pick-ups, fire alerts when something changes.

Pipeline mirrors smart-doorbell / license-plate-recognition: HTTP-poll
each camera, drive YOLOv8 via KAI-C, run a per-camera state machine,
dispatch alerts. The interesting bit specific to package-delivery is
the **arrive → present → linger → gone** transition diagram and the
"porch-pirate vs owner" severity heuristic at the gone transition.

Run:
    python package_delivery.py --config config.yml

Once-through (for tests / one-shot evaluations):
    python package_delivery.py --config config.yml --once
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from alerts import (
    Alert,
    AlertDispatcher,
    AlertSource,
    DEFAULT_ALERT_SUBJECT_PREFIX,
    build_dispatcher,
)
from frame_sources import FrameSource, FrameSourceError, build_frame_source
from package_pipeline import (
    DEFAULT_DETECTION_CONFIDENCE,
    DEFAULT_IOU_THRESHOLD,
    DEFAULT_PACKAGE_LABELS,
    DEFAULT_PERSON_LABELS,
    Detection,
    FrameReads,
    IouTracker,
    PackagePipeline,
    PackagePipelineConfig,
    Roi,
    _TrackState,
)

logger = logging.getLogger("package-delivery")

CORRELATION_ID_HEADER = "X-Correlation-Id"

# Pre-base64 cap on the embedded snapshot. Default keeps post-base64
# envelope under NATS's 1 MB default max_payload. Mirror smart-doorbell.
_DEFAULT_SNAPSHOT_MAX_BYTES: int = 700 * 1024

# Alert event kinds — used in the alert envelope's evidence so a
# subscriber can route on event_kind without parsing the title.
EVENT_ARRIVED = "package_arrived"
EVENT_LINGERING = "package_lingering"
EVENT_GONE_OWNER = "package_picked_up"
EVENT_GONE_STRANGER = "package_taken"


# ── Config ──────────────────────────────────────────────────────────


@dataclass
class CameraConfig:
    camera_id: str
    frame_url: str
    roi: Roi | None = None


@dataclass
class AppConfig:
    """Operator-tunable settings. Validated in ``load_config``."""

    # KAI-C is used for the detection call (auditable).
    kaic_url: str
    kaic_api_key: str
    detector_adapter: str = "yolov8"

    cameras: list[CameraConfig] = field(default_factory=list)
    poll_interval_seconds: float = 3.0
    request_timeout_seconds: float = 30.0

    # Pipeline / state-machine knobs.
    package_labels: tuple[str, ...] = DEFAULT_PACKAGE_LABELS
    person_labels: tuple[str, ...] = DEFAULT_PERSON_LABELS
    detection_confidence: float = DEFAULT_DETECTION_CONFIDENCE
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    arrive_consecutive_hits: int = 2
    gone_consecutive_misses: int = 3
    linger_alert_after_seconds: float = 0.0
    pickup_person_lookback_seconds: float = 8.0

    # Dedup window per (camera, track_id, event_kind).
    dedup_window_seconds: float = 30.0

    # Snapshot attachment.
    attach_snapshot_on_alerts: bool = True
    snapshot_max_bytes: int = _DEFAULT_SNAPSHOT_MAX_BYTES

    # Alert delivery channels.
    webhook_url: str | None = None
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = DEFAULT_ALERT_SUBJECT_PREFIX


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"config file {path} did not parse to a dict")

    kaic_url = raw.get("kaic_url")
    kaic_api_key = raw.get("kaic_api_key")
    if not kaic_url:
        raise SystemExit("config: kaic_url is required")
    if not kaic_api_key:
        raise SystemExit("config: kaic_api_key is required")

    cameras_raw = raw.get("cameras") or []
    cameras: list[CameraConfig] = []
    for entry in cameras_raw:
        if not isinstance(entry, dict):
            raise SystemExit("config: each camera must be a mapping")
        cam_id = entry.get("camera_id")
        url = entry.get("frame_url")
        if not cam_id or not url:
            raise SystemExit("config: camera entries need camera_id + frame_url")
        try:
            roi = Roi.parse(entry.get("roi"))
        except ValueError as exc:
            raise SystemExit(f"config: camera {cam_id!r} roi invalid: {exc}")
        cameras.append(CameraConfig(camera_id=cam_id, frame_url=url, roi=roi))

    def _str_tuple(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        val = raw.get(key, list(default))
        if not isinstance(val, list):
            raise SystemExit(f"config: {key} must be a list of strings")
        return tuple(str(v).strip().lower() for v in val if str(v).strip())

    def _float(key: str, default: float) -> float:
        try:
            return float(raw.get(key, default))
        except (TypeError, ValueError):
            raise SystemExit(
                f"config: {key} must be a number; got {raw.get(key)!r}"
            )

    def _positive_int(key: str, default: int, *, minimum: int = 1) -> int:
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError):
            raise SystemExit(
                f"config: {key} must be an integer; got {raw.get(key)!r}"
            )
        if value < minimum:
            raise SystemExit(
                f"config: {key} must be >= {minimum}; got {value}. A value "
                f"below {minimum} would defeat the anti-flicker intent of "
                f"the state machine."
            )
        return value

    subject_prefix = str(
        raw.get("nats_alerts_subject_prefix", DEFAULT_ALERT_SUBJECT_PREFIX)
    ).strip() or DEFAULT_ALERT_SUBJECT_PREFIX

    return AppConfig(
        kaic_url=str(kaic_url),
        kaic_api_key=str(kaic_api_key),
        detector_adapter=str(raw.get("detector_adapter", "yolov8")),
        cameras=cameras,
        poll_interval_seconds=_float("poll_interval_seconds", 3.0),
        request_timeout_seconds=_float("request_timeout_seconds", 30.0),
        package_labels=_str_tuple("package_labels", DEFAULT_PACKAGE_LABELS),
        person_labels=_str_tuple("person_labels", DEFAULT_PERSON_LABELS),
        detection_confidence=_float(
            "detection_confidence", DEFAULT_DETECTION_CONFIDENCE
        ),
        iou_threshold=_float("iou_threshold", DEFAULT_IOU_THRESHOLD),
        arrive_consecutive_hits=_positive_int("arrive_consecutive_hits", 2),
        gone_consecutive_misses=_positive_int("gone_consecutive_misses", 3),
        linger_alert_after_seconds=_float("linger_alert_after_seconds", 0.0),
        pickup_person_lookback_seconds=_float(
            "pickup_person_lookback_seconds", 8.0
        ),
        dedup_window_seconds=_float("dedup_window_seconds", 30.0),
        attach_snapshot_on_alerts=bool(
            raw.get("attach_snapshot_on_alerts", True)
        ),
        snapshot_max_bytes=int(
            raw.get("snapshot_max_bytes", _DEFAULT_SNAPSHOT_MAX_BYTES)
        ),
        webhook_url=raw.get("webhook_url"),
        nats_alerts_url=raw.get("nats_alerts_url"),
        nats_alerts_token=raw.get("nats_alerts_token"),
        nats_alerts_subject_prefix=subject_prefix,
    )


# ── KAI-C detector client ──────────────────────────────────────────


class KaicDetectorClient:
    """JSON+base64 HTTP client for the YOLOv8 detector via KAI-C.

    KAI-C's ``/api/v1/infer/{adapter_name}`` proxy only accepts
    application/json (multipart proxying is a planned follow-up), so
    the frame ships base64-encoded inside the JSON body. The SDK's
    body parser unwraps ``frame_b64`` into the binary payload before
    the adapter's service sees it.
    """

    def __init__(
        self,
        kaic_url: str,
        api_key: str,
        adapter_name: str,
        timeout_seconds: float,
    ) -> None:
        self._url = f"{kaic_url.rstrip('/')}/api/v1/infer/{adapter_name}"
        self._api_key = api_key
        self._timeout = timeout_seconds

    def detect(
        self, frame_jpeg: bytes, *, correlation_id: str | None = None
    ) -> dict[str, Any]:
        headers = {
            "X-Internal-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        body = {"frame_b64": base64.b64encode(frame_jpeg).decode("ascii")}
        resp = httpx.post(
            self._url, json=body, headers=headers, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json()


# ── Per-camera bookkeeping ─────────────────────────────────────────


@dataclass
class _PersonSighting:
    """One person detection — kept on a short ring buffer per camera
    so the "gone" handler can answer 'was a person here recently?'"""
    seen_at: float
    bbox: tuple[int, int, int, int]


# ── The orchestrator ───────────────────────────────────────────────


class PackageDelivery:
    """Polls all configured cameras, runs detection + tracking,
    dispatches alerts based on per-track state transitions."""

    def __init__(
        self,
        config: AppConfig,
        pipeline: PackagePipeline,
        dispatcher: AlertDispatcher,
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.dispatcher = dispatcher

        # One IoU tracker per camera — tracks are per-camera, not
        # global, so the same suitcase moved between cameras starts a
        # fresh track. That's the right call for a porch app.
        self._trackers: dict[str, IouTracker] = {
            cam.camera_id: IouTracker(iou_threshold=config.iou_threshold)
            for cam in config.cameras
        }
        # Short ring buffer of person sightings, per camera, used by
        # the "gone" handler to decide owner-vs-stranger.
        self._person_sightings: dict[str, list[_PersonSighting]] = {
            cam.camera_id: [] for cam in config.cameras
        }
        # Dedup key: (camera_id, track_id, event_kind) → monotonic ts.
        self._last_fired: dict[tuple[str, str, str], float] = {}

        # Last frame per camera — needed for snapshot attachment on the
        # "gone" event (the package isn't in the current frame anymore).
        self._last_frame_jpeg: dict[str, bytes] = {}

        self._stop = False
        self._frame_sources: dict[str, FrameSource] = {}
        for cam in config.cameras:
            self._frame_sources[cam.camera_id] = build_frame_source(
                camera_id=cam.camera_id, url=cam.frame_url,
            )

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        if not self.config.cameras:
            raise SystemExit("config: at least one camera is required for the daemon")
        logger.info(
            "started: %d cameras, poll=%.1fs, package_labels=%s",
            len(self.config.cameras),
            self.config.poll_interval_seconds,
            list(self.config.package_labels),
        )
        try:
            while not self._stop:
                cycle_started = time.monotonic()
                for cam in self.config.cameras:
                    if self._stop:
                        break
                    try:
                        self._process_camera(cam)
                    except Exception:
                        logger.exception(
                            "camera=%s: unexpected error in cycle",
                            cam.camera_id,
                        )
                if self._stop:
                    break
                elapsed = time.monotonic() - cycle_started
                time.sleep(max(0.0, self.config.poll_interval_seconds - elapsed))
        finally:
            try:
                self.dispatcher.close()
            except Exception:
                logger.exception("dispatcher.close() failed")

    def step(self) -> None:
        """Single pass over every camera. Used by --once and tests."""
        for cam in self.config.cameras:
            self._process_camera(cam)

    # ── Per-camera tick ────────────────────────────────────────────

    def _process_camera(self, cam: CameraConfig) -> None:
        correlation_id = uuid.uuid4().hex
        try:
            frame = self._frame_sources[cam.camera_id].fetch()
        except FrameSourceError as exc:
            logger.warning(
                "camera=%s: frame fetch failed: %s correlation_id=%s",
                cam.camera_id, exc, correlation_id,
            )
            return

        reads = self.pipeline.process_frame(
            frame, roi=cam.roi, correlation_id=correlation_id,
        )
        if reads is None:
            return  # detector error already logged

        now = time.monotonic()
        self._record_persons(cam.camera_id, reads.persons, now)
        self._last_frame_jpeg[cam.camera_id] = frame

        tracker = self._trackers[cam.camera_id]
        matched_ids, missed_ids = tracker.update(reads.packages, now=now)

        # ── State-machine transitions ──
        for tid in matched_ids:
            track = tracker.tracks[tid]
            self._on_track_hit(cam, track, frame, correlation_id, now)
        for tid in list(missed_ids):
            track = tracker.tracks.get(tid)
            if track is None:
                continue
            if self._on_track_miss(cam, track, correlation_id, now):
                tracker.drop(tid)

    # ── Transition handlers ─────────────────────────────────────────

    def _on_track_hit(
        self,
        cam: CameraConfig,
        track: _TrackState,
        frame_jpeg: bytes,
        correlation_id: str,
        now: float,
    ) -> None:
        """A track was matched this frame. Apply the
        new → arrived → lingering transitions where relevant."""
        if track.state == "new":
            if track.hits >= self.config.arrive_consecutive_hits:
                dispatched = self._fire(
                    cam=cam,
                    track=track,
                    event_kind=EVENT_ARRIVED,
                    severity="info",
                    title=f"Package arrived on {cam.camera_id}",
                    description=(
                        f"YOLOv8 detected {track.label!r} on {cam.camera_id} "
                        f"for {track.hits} consecutive frames "
                        f"(confidence {track.confidence:.2f})."
                    ),
                    frame_jpeg=frame_jpeg,
                    correlation_id=correlation_id,
                    now=now,
                )
                if dispatched:
                    track.state = "arrived"
                    track.arrived_at = now
            return

        if track.state == "arrived" and self.config.linger_alert_after_seconds > 0:
            assert track.arrived_at is not None
            elapsed = now - track.arrived_at
            if (
                not track.linger_alert_fired
                and elapsed >= self.config.linger_alert_after_seconds
            ):
                hours = elapsed / 3600.0
                dispatched = self._fire(
                    cam=cam,
                    track=track,
                    event_kind=EVENT_LINGERING,
                    severity="low",
                    title=f"Package still on {cam.camera_id} after {hours:.1f}h",
                    description=(
                        f"{track.label!r} has been on {cam.camera_id} for "
                        f"{hours:.1f} hours without being picked up. Consider "
                        f"bringing it inside."
                    ),
                    frame_jpeg=frame_jpeg,
                    correlation_id=correlation_id,
                    now=now,
                )
                if dispatched:
                    track.state = "lingering"
                    track.linger_alert_fired = True

    def _on_track_miss(
        self,
        cam: CameraConfig,
        track: _TrackState,
        correlation_id: str,
        now: float,
    ) -> bool:
        """A track wasn't matched this frame. Returns True iff the
        caller should drop the track from the tracker.

        Two drop paths:
          * Ghost: a track that never crossed the arrival threshold
            and is now past the gone threshold. Silent drop, no alert.
          * Gone-event dispatched: the disappearance event made it out
            of the dedup window and into the dispatcher.

        A track whose gone-event was dedup-suppressed is intentionally
        NOT dropped — keeping it around lets the next miss tick re-try
        the dispatch, or a sudden reappearance rematch instead of
        spawning a new track id.
        """
        if track.misses < self.config.gone_consecutive_misses:
            return False
        if track.state == "new":
            # Was a flicker — never crossed the arrival threshold. Drop
            # silently; no alert fires for ghost detections.
            return True

        # Owner-vs-stranger heuristic. When ``lookback`` is 0 the
        # operator has opted out of the heuristic entirely — fire as
        # an owner pickup (info severity). The config docs explicitly
        # describe this opt-out behaviour so the daemon doesn't
        # surprise homelab users with high-severity porch-pirate
        # alerts for every package they pick up themselves.
        lookback = self.config.pickup_person_lookback_seconds
        if lookback <= 0.0:
            event_kind = EVENT_GONE_OWNER
            severity = "info"
            title = f"Package gone from {cam.camera_id}"
            description = (
                f"{track.label!r} on {cam.camera_id} is no longer visible. "
                f"Owner-vs-stranger heuristic is disabled "
                f"(pickup_person_lookback_seconds=0)."
            )
        elif self._person_seen_recently(cam.camera_id, now, lookback=lookback):
            event_kind = EVENT_GONE_OWNER
            severity = "info"
            title = f"Package picked up at {cam.camera_id}"
            description = (
                f"{track.label!r} on {cam.camera_id} is no longer visible. "
                f"A person was seen in the porch ROI within the last "
                f"{lookback:.0f}s — likely an owner pickup."
            )
        else:
            event_kind = EVENT_GONE_STRANGER
            severity = "high"
            title = f"Package gone from {cam.camera_id} (no person seen)"
            description = (
                f"{track.label!r} on {cam.camera_id} is no longer visible "
                f"and NO person was seen in the porch ROI within the last "
                f"{lookback:.0f}s. Possible porch-pirate scenario — or "
                f"wind/rain knocked the box. Review the snapshot."
            )

        # Snapshot is the last frame we have for the camera — the
        # package itself isn't visible there (that's why it fired
        # 'gone'), but the surrounding scene gives the operator
        # something to look at.
        frame = self._last_frame_jpeg.get(cam.camera_id)
        dispatched = self._fire(
            cam=cam,
            track=track,
            event_kind=event_kind,
            severity=severity,
            title=title,
            description=description,
            frame_jpeg=frame,
            correlation_id=correlation_id,
            now=now,
        )
        if dispatched:
            track.state = "gone"
        return dispatched

    # ── Helpers ────────────────────────────────────────────────────

    def _record_persons(
        self, camera_id: str, persons: tuple[Detection, ...], now: float
    ) -> None:
        if not persons:
            self._prune_persons(camera_id, now)
            return
        sightings = self._person_sightings[camera_id]
        for det in persons:
            sightings.append(_PersonSighting(seen_at=now, bbox=det.bbox))
        self._prune_persons(camera_id, now)

    def _prune_persons(self, camera_id: str, now: float) -> None:
        # Keep two windows' worth of sightings so the lookback always
        # has a buffer. Bounded growth is the goal — a noisy camera
        # producing lots of person detections shouldn't balloon memory.
        cutoff = now - max(
            self.config.pickup_person_lookback_seconds * 2.0, 30.0
        )
        sightings = self._person_sightings[camera_id]
        self._person_sightings[camera_id] = [
            s for s in sightings if s.seen_at >= cutoff
        ]

    def _person_seen_recently(
        self, camera_id: str, now: float, *, lookback: float
    ) -> bool:
        if lookback <= 0.0:
            return False
        cutoff = now - lookback
        return any(
            s.seen_at >= cutoff for s in self._person_sightings.get(camera_id, [])
        )

    def _fire(
        self,
        *,
        cam: CameraConfig,
        track: _TrackState,
        event_kind: str,
        severity: str,
        title: str,
        description: str,
        frame_jpeg: bytes | None,
        correlation_id: str,
        now: float,
    ) -> bool:
        """Dispatch an alert through the dispatcher. Returns False if
        the event was suppressed by the per-(camera, track, event_kind)
        dedup window — callers use the return value to decide whether
        to advance the state machine."""
        # Dedup per (camera, track, event_kind). Most state transitions
        # are one-shot by design, but the dedup window catches state-
        # machine flicker (track briefly missed → rematched → missed
        # again) that would otherwise re-fire the gone event.
        key = (cam.camera_id, track.track_id, event_kind)
        if self.config.dedup_window_seconds > 0:
            last = self._last_fired.get(key)
            if last is not None and (now - last) < self.config.dedup_window_seconds:
                return False
            self._last_fired[key] = now

        snapshot_b64: str | None = None
        if self.config.attach_snapshot_on_alerts and frame_jpeg is not None:
            cap = max(0, int(self.config.snapshot_max_bytes))
            if cap == 0 or len(frame_jpeg) <= cap:
                snapshot_b64 = base64.b64encode(frame_jpeg).decode("ascii")
            else:
                logger.warning(
                    "camera=%s: snapshot %d bytes exceeds snapshot_max_bytes=%d; "
                    "dropping from alert envelope correlation_id=%s",
                    cam.camera_id, len(frame_jpeg), cap, correlation_id,
                )

        evidence: dict[str, Any] = {
            "event_kind": event_kind,
            "track_id": track.track_id,
            "label": track.label,
            "confidence": round(track.confidence, 4),
            "bbox": list(track.bbox),
            "hits": track.hits,
            "misses": track.misses,
            "first_seen_monotonic": round(track.first_seen_at, 3),
            "last_seen_monotonic": round(track.last_seen_at, 3),
        }
        if snapshot_b64:
            evidence["snapshot_b64"] = snapshot_b64
            evidence["snapshot_mime"] = "image/jpeg"

        self.dispatcher.dispatch(
            Alert(
                severity=severity,
                title=title,
                description=description,
                camera_id=cam.camera_id,
                source=AlertSource(),
                correlation_id=correlation_id,
                evidence=evidence,
                tags=[event_kind],
            )
        )
        return True


# ── CLI ────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenNVR package-delivery example app — watch a "
                    "porch for packages arriving and being picked up."
    )
    parser.add_argument(
        "--config", required=True, help="Path to config.yml (see config.example.yml)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single poll cycle across all cameras and exit. "
             "Useful for tests and ad-hoc debugging.",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python log level (default: INFO)",
    )
    return parser.parse_args(argv)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def _build_runtime(cfg: AppConfig) -> PackageDelivery:
    detector = KaicDetectorClient(
        kaic_url=cfg.kaic_url,
        api_key=cfg.kaic_api_key,
        adapter_name=cfg.detector_adapter,
        timeout_seconds=cfg.request_timeout_seconds,
    )
    pipeline = PackagePipeline(
        detector=detector,
        config=PackagePipelineConfig(
            package_labels=cfg.package_labels,
            person_labels=cfg.person_labels,
            detection_confidence=cfg.detection_confidence,
        ),
    )
    dispatcher = build_dispatcher(
        webhook_url=cfg.webhook_url,
        nats_alerts_url=cfg.nats_alerts_url,
        nats_alerts_token=cfg.nats_alerts_token,
        nats_alerts_subject_prefix=cfg.nats_alerts_subject_prefix,
    )
    return PackageDelivery(cfg, pipeline, dispatcher)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.log_level)
    cfg = load_config(args.config)
    runtime = _build_runtime(cfg)

    if args.once:
        runtime.step()
        try:
            runtime.dispatcher.close()
        except Exception:
            logger.exception("dispatcher.close() failed")
        return 0

    # SIGINT / SIGTERM trigger a clean exit.
    def _sig(signum: int, frame: Any) -> None:
        logger.info("received signal %d; stopping", signum)
        runtime.request_stop()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    runtime.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

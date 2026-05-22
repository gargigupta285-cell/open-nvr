# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
license-plate-recognition — drive YOLOv8 (vehicle detection) → crop →
fast-plate-ocr (OCR) on a polled set of cameras, fire alerts per
recognised plate.

Pipeline shape mirrors intrusion-detection: HTTP-poll each configured
camera at ``poll_interval_seconds``, run inference via KAI-C, dispatch
alerts. The interesting bit specific to LPR is the **two-stage
inference chain** — one frame becomes one POST to YOLOv8 *and* N POSTs
to fast-plate-ocr (one per detected vehicle), under a single
correlation_id so the audit trail joins cleanly.

Run:
    python license_plate_recognition.py --config config.yml

Daemonises on the foreground; SIGINT/SIGTERM stops cleanly after
finishing the in-flight cycle.
"""
from __future__ import annotations

import argparse
import asyncio
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
from plate_pipeline import (
    PlatePipeline,
    PlatePipelineConfig,
    PlateRead,
)

logger = logging.getLogger("license-plate-recognition")


CORRELATION_ID_HEADER = "X-Correlation-Id"


# ── Config ──────────────────────────────────────────────────────────


@dataclass
class CameraConfig:
    camera_id: str
    frame_url: str


@dataclass
class AppConfig:
    """Operator-tunable settings. Validated in ``load_config``."""

    kaic_url: str
    kaic_api_key: str
    detector_adapter: str = "yolov8"
    ocr_adapter: str = "fast_plate_ocr"

    cameras: list[CameraConfig] = field(default_factory=list)
    poll_interval_seconds: float = 2.0
    request_timeout_seconds: float = 30.0

    # Pipeline tuning — passed straight to PlatePipelineConfig.
    vehicle_labels: tuple[str, ...] = ("car", "truck", "bus", "motorcycle")
    detection_confidence: float = 0.40
    ocr_confidence: float = 0.50
    crop_strategy: str = "lower_third"

    # Plate-level dedup: don't re-fire the same plate within this
    # window (per camera). Set to 0 to fire on every read.
    dedup_window_seconds: float = 60.0

    # Optional plate watchlists. ``allowlist`` reads on these plates
    # fire a low-severity "expected vehicle" alert. ``denylist`` reads
    # fire a high-severity "watchlist plate" alert. Plates not in
    # either list fire info-severity reads.
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)

    # Alert delivery channels (see alerts.py).
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
        cameras.append(CameraConfig(camera_id=cam_id, frame_url=url))
    if not cameras:
        raise SystemExit("config: at least one camera is required")

    subject_prefix = str(
        raw.get("nats_alerts_subject_prefix", DEFAULT_ALERT_SUBJECT_PREFIX)
    ).strip() or DEFAULT_ALERT_SUBJECT_PREFIX

    return AppConfig(
        kaic_url=str(kaic_url),
        kaic_api_key=str(kaic_api_key),
        detector_adapter=str(raw.get("detector_adapter", "yolov8")),
        ocr_adapter=str(raw.get("ocr_adapter", "fast_plate_ocr")),
        cameras=cameras,
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 2.0)),
        request_timeout_seconds=float(raw.get("request_timeout_seconds", 30.0)),
        vehicle_labels=tuple(raw.get("vehicle_labels", ("car", "truck", "bus", "motorcycle"))),
        detection_confidence=float(raw.get("detection_confidence", 0.40)),
        ocr_confidence=float(raw.get("ocr_confidence", 0.50)),
        crop_strategy=str(raw.get("crop_strategy", "lower_third")),
        dedup_window_seconds=float(raw.get("dedup_window_seconds", 60.0)),
        allowlist=[str(p).upper().strip() for p in (raw.get("allowlist") or [])],
        denylist=[str(p).upper().strip() for p in (raw.get("denylist") or [])],
        webhook_url=raw.get("webhook_url"),
        nats_alerts_url=raw.get("nats_alerts_url"),
        nats_alerts_token=raw.get("nats_alerts_token"),
        nats_alerts_subject_prefix=subject_prefix,
    )


# ── KAI-C HTTP clients ─────────────────────────────────────────────


class KaicDetectorClient:
    """HTTP client for the detector adapter (YOLOv8) via KAI-C.

    Uses multipart/form-data with a ``frame`` file field — the wire
    shape the SDK's body parser advertises in /capabilities
    (``application/octet-stream`` is NOT a supported content type,
    even though it would feel natural for raw frame bytes).
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
        headers = {"X-Internal-Api-Key": self._api_key}
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        files = {"frame": ("frame.jpg", frame_jpeg, "image/jpeg")}
        resp = httpx.post(
            self._url,
            files=files,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


class KaicOcrClient:
    """HTTP client for the OCR adapter (fast-plate-ocr) via KAI-C."""

    def __init__(
        self,
        kaic_url: str,
        api_key: str,
        adapter_name: str,
        timeout_seconds: float,
    ) -> None:
        self._url = f"{kaic_url.rstrip('/')}/api/v1/infer/{adapter_name}"
        self._timeout = timeout_seconds
        self._api_key = api_key

    def read(
        self,
        plate_jpeg: bytes,
        *,
        min_confidence: float | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        # Multipart upload — the fast-plate-ocr adapter declares
        # BodyShape.IMAGE, so KAI-C / SDK route picks the multipart
        # path when it sees a 'frame' file part.
        params_blob: dict[str, Any] = {}
        if min_confidence is not None:
            params_blob["min_confidence"] = min_confidence
        files = {"frame": ("plate.jpg", plate_jpeg, "image/jpeg")}
        data = {"params": json.dumps(params_blob)} if params_blob else None

        headers = {"X-Internal-Api-Key": self._api_key}
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id

        resp = httpx.post(
            self._url,
            files=files,
            data=data,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ── Application loop ───────────────────────────────────────────────


class LicensePlateRecognizer:
    """The polling driver. One instance per process; loops over all
    configured cameras at ``poll_interval_seconds``."""

    def __init__(
        self,
        config: AppConfig,
        pipeline: PlatePipeline,
        dispatcher: AlertDispatcher,
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.dispatcher = dispatcher
        # Per-(camera_id, plate) timestamp for dedup.
        self._last_fired: dict[tuple[str, str], float] = {}
        self._allowlist = {p for p in config.allowlist if p}
        self._denylist = {p for p in config.denylist if p}
        self._stop = False
        # One FrameSource per camera, built once at startup. Mirrors
        # intrusion-detection's pattern.
        self._frame_sources: dict[str, FrameSource] = {}
        for cam in config.cameras:
            self._frame_sources[cam.camera_id] = build_frame_source(
                camera_id=cam.camera_id, url=cam.frame_url,
            )

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Block forever (or until ``request_stop()``) polling cameras."""
        logger.info(
            "license-plate-recognition started: %d cameras, poll=%ss, "
            "labels=%s, detector=%s, ocr=%s",
            len(self.config.cameras),
            self.config.poll_interval_seconds,
            list(self.config.vehicle_labels),
            self.config.detector_adapter,
            self.config.ocr_adapter,
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
                sleep = max(0.0, self.config.poll_interval_seconds - elapsed)
                time.sleep(sleep)
        finally:
            try:
                self.dispatcher.close()
            except Exception:
                logger.exception("dispatcher.close() failed")

    def step(self) -> None:
        """Single pass over every camera. Useful for ``--once`` and tests."""
        for cam in self.config.cameras:
            self._process_camera(cam)

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

        reads = list(
            self.pipeline.process_frame(frame, correlation_id=correlation_id)
        )
        if not reads:
            return

        now = time.monotonic()
        for read in reads:
            plate_key = (cam.camera_id, read.plate_text.upper())
            if self.config.dedup_window_seconds > 0:
                last = self._last_fired.get(plate_key)
                if last is not None and (now - last) < self.config.dedup_window_seconds:
                    continue
                self._last_fired[plate_key] = now

            alert = self._build_alert(cam, read)
            self.dispatcher.dispatch(alert)

    def _build_alert(self, cam: CameraConfig, read: PlateRead) -> Alert:
        plate_upper = read.plate_text.upper()
        if plate_upper in self._denylist:
            severity = "high"
            title = f"Watchlist plate {plate_upper} seen"
        elif plate_upper in self._allowlist:
            severity = "low"
            title = f"Expected plate {plate_upper} seen"
        else:
            severity = "info"
            title = f"Plate {plate_upper} read"

        return Alert(
            severity=severity,
            title=title,
            description=(
                f"License plate '{plate_upper}' read on camera "
                f"{cam.camera_id} ({read.vehicle_label}, "
                f"ocr_confidence={read.ocr_confidence:.2f})."
            ),
            camera_id=cam.camera_id,
            source=AlertSource(),
            correlation_id=read.correlation_id,
            evidence={
                "plate_text": plate_upper,
                "ocr_confidence": round(read.ocr_confidence, 4),
                "vehicle_label": read.vehicle_label,
                "vehicle_confidence": round(read.vehicle_confidence, 4),
                "vehicle_bbox": list(read.vehicle_bbox),
                "model_id": read.model_id,
                "in_allowlist": plate_upper in self._allowlist,
                "in_denylist": plate_upper in self._denylist,
            },
        )


# ── CLI ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OpenNVR license-plate-recognition example",
    )
    parser.add_argument("--config", required=True, help="path to config.yml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one pass over every camera then exit (useful for testing)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="DEBUG / INFO / WARNING / ERROR (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    pipeline = PlatePipeline(
        detector=KaicDetectorClient(
            config.kaic_url, config.kaic_api_key,
            config.detector_adapter, config.request_timeout_seconds,
        ),
        ocr=KaicOcrClient(
            config.kaic_url, config.kaic_api_key,
            config.ocr_adapter, config.request_timeout_seconds,
        ),
        config=PlatePipelineConfig(
            vehicle_labels=tuple(config.vehicle_labels),
            detection_confidence=config.detection_confidence,
            ocr_confidence=config.ocr_confidence,
            crop_strategy=config.crop_strategy,
        ),
    )
    dispatcher = build_dispatcher(
        webhook_url=config.webhook_url,
        webhook_timeout_seconds=config.request_timeout_seconds,
        nats_alerts_url=config.nats_alerts_url,
        nats_alerts_token=config.nats_alerts_token,
        nats_alerts_subject_prefix=config.nats_alerts_subject_prefix,
    )

    recognizer = LicensePlateRecognizer(config, pipeline, dispatcher)

    if args.once:
        try:
            recognizer.step()
        finally:
            dispatcher.close()
        return 0

    def _handle_signal(signum, _frame):
        logger.info("received signal %s; stopping", signum)
        recognizer.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    recognizer.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

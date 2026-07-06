# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
license-plate-recognition — drive YOLOv8 (vehicle detection) → crop →
fast-plate-ocr (OCR) on a polled set of cameras, fire alerts per
recognised plate.

Now built on the ``opennvr-app-sdk``. The SDK's
:class:`~opennvr_app_sdk.FrameApp` base owns the poll loop, per-camera
fetch/rule failure isolation, and the §03 contract endpoints. The
frame sources and the §11.5 alert stack moved into the SDK (thin shims
remain at ``frame_sources.py`` / ``alerts.py`` for import
compatibility).

What stays here — deliberately — is the domain pipeline: the
**two-stage inference chain** (``plate_pipeline.py``: one frame becomes
one POST to YOLOv8 *and* N POSTs to fast-plate-ocr, one per detected
vehicle, under a single correlation_id so the audit trail joins
cleanly), the watchlist severity routing, and the per-(camera, plate)
dedup ledger. Two SDK primitives were evaluated and NOT forced onto it:

* the SDK ``KaiCClient`` — this app's detector/OCR calls predate the
  contract-v1 ``task``/``camera_id`` body fields; ``KaicDetectorClient``
  / ``KaicOcrClient`` keep the historical wire shape (a bare
  ``{"frame_b64", ...}``) so deployed adapters see no change.
* ``keyed_state`` — the dedup ledger must stay a plain
  ``{(camera, plate): monotonic_ts}`` dict: it reads "last
  actually-fired", never refreshes on suppression, and its exact shape
  is part of this app's pinned test surface.

Run:
    python license_plate_recognition.py --config config.yml

Daemonises on the foreground; SIGINT/SIGTERM stops cleanly after
finishing the in-flight cycle.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

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
from opennvr_app_sdk import AlertType, AppManifest, FrameApp, Param, StateView
from opennvr_app_sdk.frame_sources import DictFrameSource
from plate_pipeline import (
    PlatePipeline,
    PlatePipelineConfig,
    PlateRead,
)

logger = logging.getLogger("license-plate-recognition")


CORRELATION_ID_HEADER = "X-Correlation-Id"

# The SDK FrameApp rejects a non-positive poll interval (its sleep is
# the shutdown-interruptible kind). This app historically accepted 0
# ("poll as fast as the cameras answer"); map that to a near-zero
# interval so old configs — and the test fixtures — keep working.
_MIN_POLL_INTERVAL_SECONDS = 0.001


MANIFEST = AppManifest(
    id="license-plate-recognition",
    name="License Plate Recognition",
    version="1.0.0",
    category="vehicle",
    summary=(
        "Reads license plates via a YOLOv8 → crop → fast-plate-ocr chain "
        "and routes severity through allow/deny watchlists."
    ),
    requires_tasks=["object_detection", "license_plate_recognition"],  # canonical per server/config/tasks.yml
    subscribes=None,  # FrameApp: drives inference itself via KAI-C
    params=[
        Param("vehicle_labels", list, default=["car", "truck", "bus", "motorcycle"]),
        Param("poll_interval_seconds", float, default=2.0),
        Param("detection_confidence", float, default=0.40),
        Param("ocr_confidence", float, default=0.50),
        Param("crop_strategy", str, default="lower_third",
              description="Which part of the vehicle bbox goes to OCR."),
        Param("dedup_window_seconds", float, default=60.0,
              description="Per-(camera, plate) re-fire suppression; 0 fires every read."),
        Param("allowlist", list, default=[],
              description="Plates that fire a low-severity 'expected vehicle' alert."),
        Param("denylist", list, default=[],
              description="Plates that fire a high-severity 'watchlist plate' alert."),
    ],
    emits=[
        AlertType("plate_read", severity="low",
                  description="Info-severity read for unlisted plates."),
        AlertType("plate_expected", severity="low"),
        AlertType("plate_watchlist", severity="high"),
    ],
    # Declarative live-state views over state_snapshot (GET /state) —
    # rendered generically by the catalog. Sizes update live as the
    # registry watchlists apply through on_config_update.
    state_schema=[
        StateView(name="allowlist_size", label="Allowlist",
                  kind="metric", path="allowlist_size"),
        StateView(name="denylist_size", label="Denylist",
                  kind="metric", path="denylist_size"),
        StateView(name="deduped", label="Plates deduped",
                  kind="metric", path="deduped_plates_tracked",
                  description="Distinct (camera, plate) pairs in the dedup window."),
    ],
)


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

    # App contract (spec §03) — all optional; see the SDK's contract
    # module. ``contract_port`` serves /health /manifest /state;
    # ``opennvr_url`` triggers registry self-registration on boot.
    contract_port: int | None = None
    contract_bind_host: str | None = None
    contract_host: str | None = None
    opennvr_url: str | None = None
    opennvr_token: str | None = None


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
        contract_port=(
            int(raw["contract_port"]) if raw.get("contract_port") is not None else None
        ),
        contract_bind_host=raw.get("contract_bind_host"),
        contract_host=raw.get("contract_host"),
        opennvr_url=raw.get("opennvr_url"),
        opennvr_token=raw.get("opennvr_token"),
    )


# ── KAI-C HTTP clients ─────────────────────────────────────────────


class KaicDetectorClient:
    """JSON+base64 HTTP client for the detector adapter (YOLOv8) via KAI-C.

    KAI-C's ``/api/v1/infer/{adapter_name}`` proxy only accepts
    application/json (multipart proxying is a planned follow-up), so
    the frame ships base64-encoded inside the JSON body. The SDK's
    body parser unwraps ``frame_b64`` into the binary payload before
    the adapter's service sees it.

    Kept app-side (rather than swapping to the SDK ``KaiCClient``) to
    preserve this app's historical wire body — a bare
    ``{"frame_b64": ...}`` without the contract-v1 ``task`` /
    ``camera_id`` fields.
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
            self._url,
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


class KaicOcrClient:
    """JSON+base64 HTTP client for the OCR adapter (fast-plate-ocr) via KAI-C.

    See ``KaicDetectorClient`` for the reasoning — KAI-C is JSON-only,
    and the historical wire body is preserved.
    """

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
        headers = {
            "X-Internal-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        body: dict[str, Any] = {
            "frame_b64": base64.b64encode(plate_jpeg).decode("ascii"),
        }
        if min_confidence is not None:
            body["min_confidence"] = min_confidence

        resp = httpx.post(
            self._url,
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ── Application loop ───────────────────────────────────────────────


class LicensePlateRecognizer(FrameApp):
    """The polling driver (via the SDK FrameApp loop). One instance per
    process; the base loop polls all configured cameras at
    ``poll_interval_seconds`` and :meth:`on_frame` runs the two-stage
    pipeline per fetched frame.

    Alerts are dispatched *inside* the rule rather than returned to the
    base, because the dedup ledger gates them — a suppressed repeat
    read must not reach the dispatcher at all.
    """

    manifest = MANIFEST

    def __init__(
        self,
        config: AppConfig,
        pipeline: PlatePipeline,
        dispatcher: AlertDispatcher,
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.dispatcher = dispatcher
        # One FrameSource per camera, built once at startup. Mirrors
        # intrusion-detection's pattern.
        self._frame_sources: dict[str, FrameSource] = {}
        for cam in config.cameras:
            self._frame_sources[cam.camera_id] = build_frame_source(
                camera_id=cam.camera_id, url=cam.frame_url,
            )

        super().__init__(
            config,
            dispatcher,
            # By-reference bridge: swapping an entry in
            # ``self._frame_sources`` (test stubs, camera reconfig) is
            # picked up on the next tick.
            frame_source=DictFrameSource(self._frame_sources),
            cameras=[cam.camera_id for cam in config.cameras],
            poll_interval_seconds=(
                config.poll_interval_seconds
                if config.poll_interval_seconds > 0
                else _MIN_POLL_INTERVAL_SECONDS
            ),
        )

    def setup(self) -> None:
        config = self.config
        self._cameras_by_id: dict[str, CameraConfig] = {
            cam.camera_id: cam for cam in config.cameras
        }
        # Per-(camera_id, plate) timestamp for dedup. A plain dict on
        # purpose (not the SDK keyed_state): dedup reads "last
        # actually-fired", never refreshes on suppression, and its
        # shape is pinned by this app's test suite.
        self._last_fired: dict[tuple[str, str], float] = {}
        # BOTH watchlists live in ONE tuple attribute so a live config
        # swap is a single rebind: a reader can never observe new-allow
        # with old-deny mid-update (the alert-severity routing reads
        # both sets per plate).
        self._watchlists: tuple[set[str], set[str]] = (
            {p for p in config.allowlist if p},
            {p for p in config.denylist if p},
        )

    def request_stop(self) -> None:
        """Historical name — the SDK base spells it ``stop()``."""
        self.stop()

    def step(self) -> None:
        """Single pass over every camera. Useful for ``--once`` and tests."""
        self.handle_tick()

    # ── The rule (one camera × one fetched frame) ──────────────────

    def on_frame(
        self, camera_id: str, frame_bytes: bytes
    ) -> Iterable[Alert] | None:
        cam = self._cameras_by_id[camera_id]
        correlation_id = uuid.uuid4().hex

        reads = list(
            self.pipeline.process_frame(frame_bytes, correlation_id=correlation_id)
        )
        if not reads:
            return None

        now = time.monotonic()
        fired = 0
        for read in reads:
            plate_key = (cam.camera_id, read.plate_text.upper())
            if self.config.dedup_window_seconds > 0:
                last = self._last_fired.get(plate_key)
                if last is not None and (now - last) < self.config.dedup_window_seconds:
                    continue
                self._last_fired[plate_key] = now

            alert = self._build_alert(cam, read)
            self.dispatcher.dispatch(alert)
            fired += 1
        # Wire the app-dispatched alerts into the SDK contract counters
        # (/health's alerts_fired) — the base loop can't see them
        # because on_frame returns None.
        self._contract_note_alerts(fired)
        return None

    def state_snapshot(self) -> dict[str, Any]:
        """``GET /state`` — the dedup ledger size + watchlist counts."""
        return {
            "cameras": [cam.camera_id for cam in self.config.cameras],
            "deduped_plates_tracked": len(self._last_fired),
            "allowlist_size": len(self._watchlists[0]),
            "denylist_size": len(self._watchlists[1]),
        }

    def on_config_update(self, config: dict[str, Any]) -> None:
        """Live config delivery (SDK registry poll): apply watchlist
        edits from the catalog's config form WITHOUT a restart.

        Called from the SDK's poll thread; idempotent by construction
        (tuple equality short-circuits the no-change case, including the
        first fetch that re-delivers the boot config). The swap is ONE
        rebind of a single ``(allow, deny)`` tuple — a reader in the
        frame loop sees either wholly-old or wholly-new lists, never a
        mixed pair (a plate moving allow→deny can't transiently match
        neither). Plates normalize exactly like ``load_config``
        (upper + strip).

        Only the watchlists apply live: they are pure per-read lookups.
        Camera topology / adapter / interval edits still need a restart
        (they are baked into the running pipeline), which the SDK's
        default log line already tells the operator.
        """
        allow = {
            str(p).upper().strip()
            for p in (config.get("allowlist") or [])
            if str(p).strip()
        }
        deny = {
            str(p).upper().strip()
            for p in (config.get("denylist") or [])
            if str(p).strip()
        }
        if (allow, deny) == self._watchlists:
            return
        self._watchlists = (allow, deny)
        logger.info(
            "watchlists updated live from the registry: "
            "allowlist=%d denylist=%d",
            len(allow),
            len(deny),
        )

    def _build_alert(self, cam: CameraConfig, read: PlateRead) -> Alert:
        plate_upper = read.plate_text.upper()
        # ONE read of the tuple → both membership tests see the same
        # generation of the watchlists even mid-config-swap.
        allowlist, denylist = self._watchlists
        if plate_upper in denylist:
            severity = "high"
            title = f"Watchlist plate {plate_upper} seen"
        elif plate_upper in allowlist:
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
                "in_allowlist": plate_upper in allowlist,
                "in_denylist": plate_upper in denylist,
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

    # The SDK FrameApp loop is async; drive it the same way the SDK
    # AppRunner drives a Detector. SIGINT / SIGTERM trigger a clean exit.
    loop = asyncio.new_event_loop()

    def _handle_signal(signum, _frame):
        logger.info("received signal %s; stopping", signum)
        loop.call_soon_threadsafe(recognizer.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(recognizer.run())
    finally:
        try:
            dispatcher.close()
        except Exception:
            logger.exception("dispatcher.close() failed")
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

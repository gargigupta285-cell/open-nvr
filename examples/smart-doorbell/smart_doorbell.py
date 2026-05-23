# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
smart-doorbell — poll a doorbell camera, recognise faces via the
InsightFace adapter through KAI-C, fire alerts with severity that
depends on whether the face is registered.

Pipeline mirrors intrusion-detection: HTTP-poll each camera, run
inference via KAI-C, dispatch alerts. The interesting bit specific
to Smart Doorbell is the **face-DB enrollment flow** — operators
register family members ahead of time via the ``enroll`` CLI
subcommand below, which talks directly to the InsightFace adapter's
``/faces/register`` route (KAI-C does not proxy that surface).

Run as a daemon:
    python smart_doorbell.py daemon --config config.yml

Enroll Alice via REST (no shared volume needed):
    python smart_doorbell.py enroll \\
        --config config.yml \\
        --person-id alice \\
        --name "Alice Smith" \\
        --image ~/photos/alice.jpg \\
        --category family

List enrolled faces:
    python smart_doorbell.py list-faces --config config.yml
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
from face_recognition_pipeline import (
    DEFAULT_RECOGNITION_THRESHOLD,
    FaceRead,
    FaceRecognitionPipeline,
    FaceRecognitionPipelineConfig,
    RecognitionClient,
)
from frame_sources import FrameSource, FrameSourceError, build_frame_source

logger = logging.getLogger("smart-doorbell")

CORRELATION_ID_HEADER = "X-Correlation-Id"

# Cap on the raw JPEG snapshot we'll embed in an alert envelope.
# Base64 inflates by ~33%, so a 700 KB JPEG becomes ~933 KB on the
# wire — still under the NATS default 1 MB max_payload. Operators
# with NATS configured for larger payloads can override via
# ``snapshot_max_bytes`` in config. A snapshot above the cap is
# dropped from the envelope (the alert still fires) and a WARN log
# line names the camera so the operator can shrink the source.
_DEFAULT_SNAPSHOT_MAX_BYTES: int = 700 * 1024


# ── Config ──────────────────────────────────────────────────────────


@dataclass
class CameraConfig:
    camera_id: str
    frame_url: str


@dataclass
class AppConfig:
    """Operator-tunable settings. Validated in ``load_config``."""

    # KAI-C is used for the recognition call (auditable).
    kaic_url: str
    kaic_api_key: str
    recognition_adapter: str = "insightface"

    # The InsightFace adapter's direct URL for face-DB CRUD. KAI-C
    # does not proxy the /faces/* routes, so the enroll subcommand
    # hits the adapter directly. Bearer-token auth.
    adapter_url: str = "http://127.0.0.1:9005"
    adapter_token: str = ""

    cameras: list[CameraConfig] = field(default_factory=list)
    poll_interval_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    recognition_threshold: float = DEFAULT_RECOGNITION_THRESHOLD

    # Dedup: don't refire the same (camera, person-or-unknown-bucket)
    # alert within this window. Set 0 to fire every read.
    dedup_window_seconds: float = 60.0

    # If True, embed a base64 JPEG snapshot in UNKNOWN-face alert
    # envelopes only. A small downstream relay (see alerts-subscriber/)
    # can then forward the photo to Telegram / ntfy / Discord without
    # a second HTTP round-trip to the NVR. Known-face alerts still
    # ride small (no snapshot) so the alert bus stays low-bandwidth
    # in the common case.
    attach_snapshot_for_unknowns: bool = True

    # Pre-base64 cap on the embedded snapshot. Default keeps the
    # post-base64 envelope under NATS's 1 MB default max_payload.
    # A snapshot larger than this is dropped from the envelope
    # (the alert still fires) with a WARN log line.
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
        cameras.append(CameraConfig(camera_id=cam_id, frame_url=url))
    if not cameras:
        # The enroll / list-faces subcommands DON'T need cameras
        # configured; the daemon does. We accept zero cameras at
        # parse time and check again at daemon-start.
        pass

    subject_prefix = str(
        raw.get("nats_alerts_subject_prefix", DEFAULT_ALERT_SUBJECT_PREFIX)
    ).strip() or DEFAULT_ALERT_SUBJECT_PREFIX

    return AppConfig(
        kaic_url=str(kaic_url),
        kaic_api_key=str(kaic_api_key),
        recognition_adapter=str(raw.get("recognition_adapter", "insightface")),
        adapter_url=str(raw.get("adapter_url", "http://127.0.0.1:9005")),
        adapter_token=str(raw.get("adapter_token", "") or ""),
        cameras=cameras,
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 1.0)),
        request_timeout_seconds=float(raw.get("request_timeout_seconds", 30.0)),
        recognition_threshold=float(
            raw.get("recognition_threshold", DEFAULT_RECOGNITION_THRESHOLD)
        ),
        dedup_window_seconds=float(raw.get("dedup_window_seconds", 60.0)),
        attach_snapshot_for_unknowns=bool(
            raw.get("attach_snapshot_for_unknowns", True)
        ),
        snapshot_max_bytes=int(
            raw.get("snapshot_max_bytes", _DEFAULT_SNAPSHOT_MAX_BYTES)
        ),
        webhook_url=raw.get("webhook_url"),
        nats_alerts_url=raw.get("nats_alerts_url"),
        nats_alerts_token=raw.get("nats_alerts_token"),
        nats_alerts_subject_prefix=subject_prefix,
    )


# ── KAI-C recognition client ───────────────────────────────────────


class KaicRecognitionClient:
    """JSON POST to KAI-C's /api/v1/infer/{adapter} → InsightFace
    /infer. KAI-C audits the call and threads the correlation_id
    through to the adapter.

    KAI-C only proxies application/json (multipart proxying is a
    planned follow-up), so the frame ships base64-encoded inside the
    JSON body. The SDK's body parser unwraps ``frame_b64`` into the
    binary payload and lifts the remaining keys (``task``,
    ``threshold``) into the top-level payload the service sees.
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

    def recognize(
        self,
        frame_jpeg: bytes,
        *,
        threshold: float,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-Internal-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        body: dict[str, Any] = {
            "frame_b64": base64.b64encode(frame_jpeg).decode("ascii"),
            "task": "face_recognition",
            "threshold": threshold,
        }
        resp = httpx.post(
            self._url,
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ── The orchestrator ───────────────────────────────────────────────


class SmartDoorbell:
    """Polls all configured cameras, runs recognition, dispatches."""

    # Sentinel object used to bucket unknown-person dedup keys. We use
    # an object() rather than a string so a hostile / unlikely
    # ``person_id`` value (e.g. someone enrols a face with id
    # ``"__unknown__"``) can never collide with the stranger bucket.
    # Mixed-type tuple keys are fine in dict.
    _UNKNOWN_BUCKET = object()

    def __init__(
        self,
        config: AppConfig,
        pipeline: FaceRecognitionPipeline,
        dispatcher: AlertDispatcher,
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.dispatcher = dispatcher
        # Key is (camera_id, person_id_or_sentinel). When the face is
        # recognised we use the person_id (a str); when it isn't we
        # use the ``_UNKNOWN_BUCKET`` sentinel object so a hostile
        # person_id can't collide with the stranger bucket.
        self._last_fired: dict[tuple[str, Any], float] = {}
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
            raise SystemExit(
                "config: at least one camera is required for the daemon"
            )
        logger.info(
            "smart-doorbell started: %d cameras, poll=%ss, threshold=%.2f",
            len(self.config.cameras),
            self.config.poll_interval_seconds,
            self.config.recognition_threshold,
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

        read = self.pipeline.process_frame(frame, correlation_id=correlation_id)
        if read is None or not read.face_detected:
            # No face → nothing to alert. (We could fire a "movement,
            # no recognisable face" event but that's a different
            # example app — keep this one focused on the doorbell.)
            return

        bucket = read.person_id or self._UNKNOWN_BUCKET
        plate_key = (cam.camera_id, bucket)
        now = time.monotonic()
        if self.config.dedup_window_seconds > 0:
            last = self._last_fired.get(plate_key)
            if last is not None and (now - last) < self.config.dedup_window_seconds:
                return
            self._last_fired[plate_key] = now

        attach_snapshot = (
            self.config.attach_snapshot_for_unknowns and not read.recognized
        )
        snapshot_bytes: bytes | None = None
        if attach_snapshot:
            cap = max(0, int(self.config.snapshot_max_bytes))
            if cap == 0 or len(frame) <= cap:
                snapshot_bytes = frame
            else:
                logger.warning(
                    "camera=%s: snapshot %d bytes exceeds snapshot_max_bytes=%d; "
                    "dropping from alert envelope correlation_id=%s",
                    cam.camera_id, len(frame), cap, read.correlation_id,
                )
        alert = self._build_alert(cam, read, snapshot_bytes)
        self.dispatcher.dispatch(alert)

    def _build_alert(
        self,
        cam: CameraConfig,
        read: FaceRead,
        snapshot: bytes | None,
    ) -> Alert:
        if read.recognized:
            severity = "low" if (read.category or "").lower() == "family" else "info"
            display = read.name or read.person_id or "?"
            title = f"Known visitor at {cam.camera_id}: {display}"
            description = (
                f"Recognised {display!r} (similarity "
                f"{read.similarity:.2f}) on {cam.camera_id}."
            )
        else:
            severity = "high"
            title = f"Unknown visitor at {cam.camera_id}"
            description = (
                f"Unrecognised face on {cam.camera_id}. "
                "Check the snapshot below."
            )

        evidence: dict[str, Any] = {
            "recognized": read.recognized,
            "person_id": read.person_id,
            "name": read.name,
            "category": read.category,
            "similarity": read.similarity,
            "face_bbox": list(read.face_bbox) if read.face_bbox else None,
            "threshold": read.threshold,
        }
        if snapshot is not None:
            evidence["snapshot_b64"] = base64.b64encode(snapshot).decode("ascii")
            evidence["snapshot_mime"] = "image/jpeg"

        return Alert(
            severity=severity,
            title=title,
            description=description,
            camera_id=cam.camera_id,
            source=AlertSource(),
            correlation_id=read.correlation_id,
            evidence=evidence,
        )


# ── enroll / list-faces / get-face / delete-face subcommands ─────


class _FaceAdminClient:
    """Direct HTTP client for the adapter's /faces/* CRUD routes.
    KAI-C does NOT proxy these (they're not part of the contract);
    the enroll flow talks to the adapter directly."""

    def __init__(self, base_url: str, token: str, timeout_seconds: float) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout_seconds

    def register(
        self,
        *,
        image_bytes: bytes,
        person_id: str,
        name: str,
        category: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        files = {"frame": ("face.jpg", image_bytes, "image/jpeg")}
        data = {
            "person_id": person_id,
            "name": name,
            "category": category,
            "metadata": json.dumps(metadata or {}),
        }
        resp = httpx.post(
            f"{self._base}/faces/register",
            files=files, data=data,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def list_faces(self, category: str | None = None) -> dict[str, Any]:
        params = {"category": category} if category else {}
        resp = httpx.get(
            f"{self._base}/faces",
            params=params,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get_face(self, person_id: str) -> dict[str, Any]:
        resp = httpx.get(
            f"{self._base}/faces/{person_id}",
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_face(self, person_id: str) -> dict[str, Any]:
        resp = httpx.delete(
            f"{self._base}/faces/{person_id}",
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()


# Soft cap so we fail client-side before shipping a 50 MB photo over
# the network only to get a 413 back. Matches the adapter-side
# ``MAX_IMAGE_BYTES`` cap.
_ENROLL_MAX_IMAGE_BYTES: int = 8 * 1024 * 1024


def _print_http_error(action: str, exc: httpx.HTTPStatusError) -> None:
    """Translate a 4xx/5xx response into a one-line operator-friendly
    error. Avoids dumping a full httpx traceback for predictable
    failure modes (no face detected, bad token, file too large)."""
    try:
        detail = exc.response.json().get("detail", "")
    except Exception:
        detail = exc.response.text[:200]
    print(
        f"{action} failed (HTTP {exc.response.status_code}): {detail}",
        file=sys.stderr,
    )


def _cmd_enroll(config: AppConfig, args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser()
    if not image_path.is_file():
        print(f"image not found: {image_path}", file=sys.stderr)
        return 2
    size = image_path.stat().st_size
    if size > _ENROLL_MAX_IMAGE_BYTES:
        print(
            f"image {image_path} is {size / 1_000_000:.1f} MB — over the "
            f"{_ENROLL_MAX_IMAGE_BYTES / 1_000_000:.0f} MB upload limit. "
            "Resize / re-encode before enrolling.",
            file=sys.stderr,
        )
        return 2
    image_bytes = image_path.read_bytes()
    client = _FaceAdminClient(
        config.adapter_url, config.adapter_token, config.request_timeout_seconds,
    )
    try:
        out = client.register(
            image_bytes=image_bytes,
            person_id=args.person_id,
            name=args.name,
            category=args.category,
        )
    except httpx.HTTPStatusError as exc:
        _print_http_error("enroll", exc)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def _cmd_list_faces(config: AppConfig, args: argparse.Namespace) -> int:
    client = _FaceAdminClient(
        config.adapter_url, config.adapter_token, config.request_timeout_seconds,
    )
    try:
        out = client.list_faces(category=args.category)
    except httpx.HTTPStatusError as exc:
        _print_http_error("list-faces", exc)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def _cmd_delete_face(config: AppConfig, args: argparse.Namespace) -> int:
    client = _FaceAdminClient(
        config.adapter_url, config.adapter_token, config.request_timeout_seconds,
    )
    try:
        out = client.delete_face(args.person_id)
    except httpx.HTTPStatusError as exc:
        _print_http_error("delete-face", exc)
        return 1
    print(json.dumps(out, indent=2))
    return 0


# ── daemon ─────────────────────────────────────────────────────────


def _cmd_daemon(config: AppConfig, args: argparse.Namespace) -> int:
    pipeline = FaceRecognitionPipeline(
        client=KaicRecognitionClient(
            config.kaic_url, config.kaic_api_key,
            config.recognition_adapter, config.request_timeout_seconds,
        ),
        config=FaceRecognitionPipelineConfig(
            recognition_threshold=config.recognition_threshold,
        ),
    )
    dispatcher = build_dispatcher(
        webhook_url=config.webhook_url,
        webhook_timeout_seconds=config.request_timeout_seconds,
        nats_alerts_url=config.nats_alerts_url,
        nats_alerts_token=config.nats_alerts_token,
        nats_alerts_subject_prefix=config.nats_alerts_subject_prefix,
    )

    doorbell = SmartDoorbell(config, pipeline, dispatcher)

    if args.once:
        try:
            doorbell.step()
        finally:
            dispatcher.close()
        return 0

    def _handle_signal(signum, _frame):
        logger.info("received signal %s; stopping", signum)
        doorbell.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    doorbell.run()
    return 0


# ── CLI ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OpenNVR smart-doorbell example",
    )
    parser.add_argument("--config", required=True, help="path to config.yml")
    parser.add_argument(
        "--log-level", default="INFO",
        help="DEBUG / INFO / WARNING / ERROR (default: INFO)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # daemon
    p_daemon = sub.add_parser("daemon", help="poll cameras, recognise faces, fire alerts")
    p_daemon.add_argument(
        "--once", action="store_true",
        help="run one pass over every camera then exit",
    )
    p_daemon.set_defaults(func=_cmd_daemon)

    # enroll
    p_enroll = sub.add_parser("enroll", help="register a known face")
    p_enroll.add_argument("--person-id", required=True)
    p_enroll.add_argument("--name", required=True)
    p_enroll.add_argument("--image", required=True, help="path to a JPEG/PNG face crop")
    p_enroll.add_argument("--category", default="family")
    p_enroll.set_defaults(func=_cmd_enroll)

    # list-faces
    p_list = sub.add_parser("list-faces", help="list registered faces")
    p_list.add_argument("--category", default=None)
    p_list.set_defaults(func=_cmd_list_faces)

    # delete-face
    p_del = sub.add_parser("delete-face", help="delete a registered face")
    p_del.add_argument("--person-id", required=True)
    p_del.set_defaults(func=_cmd_delete_face)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    return args.func(config, args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

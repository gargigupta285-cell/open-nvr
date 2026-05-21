# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Intrusion-detection example app.

Watches one or more cameras for persons/vehicles entering operator-
defined restricted zones during operator-defined restricted hours. On
detection, fires an alert via stdout (always) and an optional
webhook. Uses KAI-C's contract proxy (``POST /api/v1/infer/{adapter}``)
for inference — so every alert is correlation-id-traceable through
the audit log.

This is the first first-party example app per §12 of the AI Adapter
Contract design. Operators run it as a sidecar to OpenNVR; community
contributors copy it as a template for their own monitoring apps.

Run:
    python intrusion_detection.py --config config.yml          # daemon
    python intrusion_detection.py --config config.yml --once    # one cycle (testing)
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import logging
import signal
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from alerts import Alert, AlertDispatcher, build_dispatcher
from frame_sources import FrameSource, FrameSourceError, build_frame_source
from zone import Point, Zone, bbox_center

logger = logging.getLogger("intrusion-detection")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class CameraWatch:
    """One camera + its zone + its frame source. Multiple cameras
    can share the same KAI-C/adapter target — each gets its own
    detector loop iteration."""

    camera_id: str
    frame_url: str  # file://, http://, https://
    zone: Zone
    # Camera frame dimensions in pixels. The contract emits
    # normalized [0, 1] bboxes; we translate back to pixels to
    # compare against the zone polygon, which is operator-defined
    # in pixels.
    frame_width: int
    frame_height: int


@dataclass
class RestrictedHours:
    """A daily time window during which alerts fire. Supports
    cross-midnight ranges (e.g. ``start=22:00, end=06:00``).

    All comparisons use the LOCAL timezone of the host (or the
    operator-supplied ``timezone`` if pytz/zoneinfo is configured).
    For v1 we use ``datetime.now()`` which picks up the host TZ.
    """

    start: _dt.time
    end: _dt.time

    def contains(self, when: _dt.datetime) -> bool:
        """True if ``when.time()`` is within [start, end). Handles
        cross-midnight ranges by inverting the comparison."""
        t = when.time()
        if self.start <= self.end:
            # Normal range, e.g. 09:00 - 17:00.
            return self.start <= t < self.end
        # Cross-midnight range, e.g. 22:00 - 06:00.
        return t >= self.start or t < self.end


@dataclass
class AppConfig:
    """Top-level config loaded from YAML."""

    kaic_url: str
    kaic_adapter_name: str
    kaic_api_key: str | None
    poll_interval_seconds: float
    watch_labels: list[str]
    restricted_hours: RestrictedHours
    cameras: list[CameraWatch]
    webhook_url: str | None
    # Optional NATS alert fan-out. When ``nats_alerts_url`` is set,
    # every fired alert is also published as JSON onto
    # ``{nats_alerts_subject_prefix}.{source.kind}.{source.name}.{camera_id}``.
    # Wire this up to feed the OpenNVR alerts inbox, a SIEM, or any
    # other bus subscriber without standing up additional webhooks.
    # Default-disabled so single-host deployments without NATS just
    # work.
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = "opennvr.alerts"
    request_timeout_seconds: float = 30.0
    # ``kaic_transport`` selects how this example talks to KAI-C:
    #
    # * ``http`` (default, back-compat) — one POST to
    #   /api/v1/infer/{adapter} per polled frame. Simpler; one
    #   connection per cycle (httpx keeps it alive). Latency floor is
    #   the poll interval (~1s default).
    #
    # * ``ws`` — one persistent WebSocket per camera to KAI-C's
    #   /api/v1/infer/{adapter}/stream proxy (§6). Drops per-frame
    #   latency from ~poll_interval to ~adapter inference time
    #   (~30-50ms for YOLOv8) at the cost of one open connection per
    #   camera. Use when you actually need sub-second response on
    #   alerts; HTTP is fine for typical surveillance.
    kaic_transport: str = "http"


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed AppConfig.

    Raises ``ValueError`` on malformed config — caller's job to
    surface a useful operator message and exit non-zero."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")

    try:
        kaic_url = str(raw["kaic_url"]).rstrip("/")
    except KeyError as exc:
        raise ValueError("config: 'kaic_url' is required") from exc

    poll_interval = float(raw.get("poll_interval_seconds", 5.0))
    if poll_interval <= 0:
        raise ValueError("config: 'poll_interval_seconds' must be > 0")

    rh_raw = raw.get("restricted_hours", {})
    try:
        rh = RestrictedHours(
            start=_dt.time.fromisoformat(str(rh_raw.get("start", "00:00"))),
            end=_dt.time.fromisoformat(str(rh_raw.get("end", "23:59"))),
        )
    except ValueError as exc:
        raise ValueError(f"config: bad restricted_hours value: {exc}") from exc

    cameras_raw = raw.get("cameras") or []
    if not cameras_raw:
        raise ValueError("config: at least one camera entry is required")
    cameras: list[CameraWatch] = []
    for idx, c in enumerate(cameras_raw):
        try:
            zone = Zone.from_config(
                name=str(c.get("zone_name", f"zone-{idx}")),
                vertices=c["zone"],
            )
            cameras.append(
                CameraWatch(
                    camera_id=str(c["camera_id"]),
                    frame_url=str(c["frame_url"]),
                    zone=zone,
                    frame_width=int(c.get("frame_width", 1920)),
                    frame_height=int(c.get("frame_height", 1080)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"config: camera entry {idx} malformed: {exc}"
            ) from exc

    kaic_transport = str(raw.get("kaic_transport", "http")).lower()
    if kaic_transport not in ("http", "ws"):
        raise ValueError(
            f"config: kaic_transport must be 'http' or 'ws', got {kaic_transport!r}"
        )

    nats_alerts_url = str(raw["nats_alerts_url"]).strip() if raw.get("nats_alerts_url") else None
    nats_alerts_token = str(raw["nats_alerts_token"]) if raw.get("nats_alerts_token") else None
    # Refuse an explicitly-empty prefix; absent → use the default.
    if "nats_alerts_subject_prefix" in raw:
        nats_prefix = str(raw["nats_alerts_subject_prefix"]).strip()
        if not nats_prefix:
            raise ValueError(
                "config: 'nats_alerts_subject_prefix' must not be empty "
                "(omit the key to use the default 'opennvr.alerts')"
            )
    else:
        nats_prefix = "opennvr.alerts"

    return AppConfig(
        kaic_url=kaic_url,
        kaic_adapter_name=str(raw.get("kaic_adapter_name", "yolov8")),
        kaic_api_key=str(raw["kaic_api_key"]) if raw.get("kaic_api_key") else None,
        poll_interval_seconds=poll_interval,
        watch_labels=[str(s).lower() for s in raw.get("watch_labels", ["person"])],
        restricted_hours=rh,
        cameras=cameras,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=nats_alerts_url,
        nats_alerts_token=nats_alerts_token,
        nats_alerts_subject_prefix=nats_prefix,
        request_timeout_seconds=float(raw.get("request_timeout_seconds", 30.0)),
        kaic_transport=kaic_transport,
    )


# ── KAI-C client ───────────────────────────────────────────────────


class KaicClient:
    """Tiny client for KAI-C's ``POST /api/v1/infer/{adapter}``.

    We send the frame as base64 JSON (the convenience path) because
    multipart adds boilerplate without benefit at 1-fps polling.
    Threads ``X-Correlation-Id`` so every alert traces back through
    KAI-C's audit log and the adapter's logs alike.
    """

    def __init__(
        self,
        base_url: str,
        adapter_name: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._adapter_name = adapter_name
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds, trust_env=False)

    def close(self) -> None:
        if self._owns_client and hasattr(self._client, "close"):
            self._client.close()

    def infer_frame(
        self,
        *,
        camera_id: str,
        frame_bytes: bytes,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Send a frame to KAI-C; return the raw InferResponse body.

        Raises ``KaicError`` on transport failure or non-200; the
        detector loop catches and decides whether to alert / skip /
        abort."""
        url = f"{self._base_url}/api/v1/infer/{self._adapter_name}"
        headers = {"X-Correlation-Id": correlation_id}
        if self._api_key:
            headers["X-Internal-Api-Key"] = self._api_key
        body = {
            "camera_id": camera_id,
            "frame_b64": base64.b64encode(frame_bytes).decode("ascii"),
        }
        try:
            response = self._client.post(url, json=body, headers=headers)
        except Exception as exc:
            raise KaicError(f"KAI-C unreachable at {url}: {exc}") from exc
        if response.status_code != 200:
            raise KaicError(
                f"KAI-C returned HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.json()


class KaicError(Exception):
    """Raised when KAI-C is unreachable or returns a non-200. The
    detector loop treats this as a transient skip — alerts don't fire
    on a comms failure (the failure itself is visible in KAI-C's
    audit log via the correlation_id we sent)."""


# ── KAI-C streaming client (§6 WebSocket) ──────────────────────────


class KaicStreamClient:
    """Per-camera persistent WebSocket session against KAI-C's
    ``/api/v1/infer/{adapter}/stream`` proxy (added in A2.4b).

    Uses the synchronous ``websockets.sync`` client so the detector's
    thread model stays simple — each camera owns one client, each
    poll cycle does ``send_frame() + recv_result()``. The async
    ``websockets.connect`` API is the more idiomatic choice for
    high-fan-out streaming, but for this example's typical "5-20
    cameras at 1-30 fps" workload, the sync API is plenty and keeps
    the codebase synchronous end-to-end.

    Reconnects lazily on the next ``send_frame`` after a transport
    error. A persistent failure surfaces as alerts not firing for
    the affected camera; KAI-C's audit log shows ``stream.failed``
    so operators can investigate.

    Frame metadata + binary are sent per §6.3:

        send_text({"type": "frame", "seq": <int>, "ts_ms": <int>, "content_type": "image/jpeg"})
        send_bytes(<jpeg bytes>)

    Result comes back as a text frame:

        {"type": "result", "seq": <echoed>, "ts_ms": <...>, "inference_ms": <int>, "result": {...}}
    """

    def __init__(
        self,
        base_url: str,
        adapter_name: str,
        camera_id: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
        websocket_factory: Callable[[str, list[tuple[str, str]]], Any] | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self._camera_id = camera_id
        self._api_key = api_key
        self._timeout = timeout_seconds
        # Translate http(s):// → ws(s):// for the upstream connect URL.
        # KAI-C's WS endpoint is at {kaic_url}/api/v1/infer/{adapter}/stream;
        # preserve any path prefix the operator supplied so KAI-C
        # deployed behind a reverse proxy (e.g.,
        # ``https://nvr.corp/kaic/``) routes correctly. (Peer review M3.)
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(base_url)
        ws_scheme_map = {"http": "ws", "https": "wss"}
        ws_scheme = ws_scheme_map.get(parsed.scheme.lower())
        if ws_scheme is None:
            raise ValueError(
                f"kaic_url must start with http:// or https://, got {base_url!r}"
            )
        # Build the WS URL: keep host + any path prefix, append the
        # KAI-C streaming path. Strips a trailing slash on the prefix
        # so we don't end up with ``//api/v1/...``.
        path_prefix = (parsed.path or "").rstrip("/")
        self._url = urlunparse((
            ws_scheme,
            parsed.netloc,
            f"{path_prefix}/api/v1/infer/{adapter_name}/stream",
            "", "", "",
        ))
        # Allow injection for tests (the production path uses
        # ``websockets.sync.client.connect``; tests can substitute a
        # fake that returns a stub connection).
        self._websocket_factory = websocket_factory
        self._conn: Any = None
        self._seq: int = 0
        # KAI-C audits at SESSION grain (stream.opened/closed/failed),
        # not per-frame, so all frames in this WS session share one
        # correlation_id — set on handshake. We expose it back via
        # ``infer_frame``'s response so the detector's alerts
        # reference the same ID KAI-C will show in its audit log.
        # (Peer review H1.)
        self._session_correlation_id: str | None = None

    def _do_connect(self, headers: list[tuple[str, str]]) -> Any:
        """Open a fresh WS connection. Raises ``KaicError`` on
        connect failure so the caller can decide to skip the cycle."""
        if self._websocket_factory is not None:
            return self._websocket_factory(self._url, headers)
        # Lazy import — websockets is only needed in WS mode, and
        # this keeps the HTTP-mode happy path light.
        from websockets.sync.client import connect as ws_connect

        try:
            return ws_connect(
                self._url,
                additional_headers=headers,
                open_timeout=self._timeout,
                close_timeout=2.0,
                # 32 MiB upper bound on result-message size. Mirrors
                # the SDK's adapter-side ``max_body_bytes`` default;
                # a misbehaving / malicious adapter can't OOM the
                # detector with an unbounded frame. (Peer review L6.)
                max_size=32 * 1024 * 1024,
            )
        except Exception as exc:  # noqa: BLE001
            raise KaicError(f"WS connect to {self._url} failed: {exc}") from exc

    def _ensure_open(self, correlation_id: str) -> None:
        """Open the WS + send the §6.1 handshake on first use. The
        adapter's handshake_ack confirms the negotiated transport
        (downgrades shared_memory → websocket if the adapter doesn't
        support shm; we never offer shm so this is informational)."""
        if self._conn is not None:
            return
        headers = [("X-Correlation-Id", correlation_id)]
        if self._api_key:
            headers.append(("X-Internal-Api-Key", self._api_key))
        conn = self._do_connect(headers)
        try:
            conn.send(_json_dumps({
                "type": "handshake",
                "client_id": "intrusion-detection",
                "camera_id": self._camera_id,
                "frame_transport": "websocket",
            }))
            # Read the handshake_ack before the first frame goes out;
            # rejects (bad camera_id, adapter not registered) close
            # the WS here rather than mid-frame.
            ack_raw = conn.recv(timeout=self._timeout)
            ack = _json_loads(ack_raw)
            if not isinstance(ack, dict) or ack.get("type") != "handshake_ack":
                raise KaicError(f"unexpected handshake response: {ack_raw!r}")
        except KaicError:
            self._safe_close(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._safe_close(conn)
            raise KaicError(f"WS handshake failed: {exc}") from exc
        self._conn = conn
        self._session_correlation_id = correlation_id
        # Fresh session → fresh seq counter. (Peer review H2 — without
        # this, a reconnect mid-stream re-uses the previous session's
        # seq numbers, which §6.3 says must be monotonically increasing
        # *per session*.)
        self._seq = 0

    def infer_frame(
        self,
        *,
        frame_bytes: bytes,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Send one frame to KAI-C, return the parsed result body.

        Raises ``KaicError`` on transport failure or protocol violation.
        The detector loop catches and skips that cycle — same handling
        as the HTTP path. The next call will trigger a reconnect via
        ``_ensure_open``."""
        self._ensure_open(correlation_id)
        assert self._conn is not None
        self._seq += 1
        ts_ms = int(time.monotonic() * 1000)
        try:
            self._conn.send(_json_dumps({
                "type": "frame",
                "seq": self._seq,
                "ts_ms": ts_ms,
                "content_type": "image/jpeg",
            }))
            self._conn.send(frame_bytes)
            raw = self._conn.recv(timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001
            # Tear down so the next call reconnects. The frame is
            # lost; that's the same behaviour as an HTTP 502.
            self._safe_close(self._conn)
            self._conn = None
            raise KaicError(f"WS infer failed: {exc}") from exc

        # Parse the §6.3 result message.
        try:
            payload = _json_loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise KaicError(f"WS recv: non-JSON payload {raw!r}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("type") != "result":
            raise KaicError(f"WS recv: unexpected message {payload!r}")
        # Shape the response so the detector's existing post-parser
        # (which expects ``InferResponse``-like dicts from the HTTP
        # path) can consume it unchanged. We add a private
        # ``__session_correlation_id`` key so the detector knows the
        # effective correlation_id KAI-C will surface in its audit
        # log — in WS mode all frames within a session share one
        # correlation_id, so alerts MUST reference the session's,
        # not the per-step one ``_call_kaic`` was passed. (Peer
        # review H1.)
        return {
            "status": "ok",
            "model_name": "",  # WS protocol doesn't echo model name
            "model_version": "",
            "inference_ms": int(payload.get("inference_ms", 0)),
            "result": payload.get("result") or {},
            "__session_correlation_id": self._session_correlation_id,
        }

    def close(self) -> None:
        if self._conn is not None:
            self._safe_close(self._conn)
            self._conn = None

    @staticmethod
    def _safe_close(conn: Any) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# Json helpers — kept module-local so the lazy ``import json`` only
# happens when WS mode actually runs (and so monkey-patching is easy
# in tests).

def _json_dumps(obj: Any) -> str:
    import json as _json
    return _json.dumps(obj)


def _json_loads(raw: Any) -> Any:
    import json as _json
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return _json.loads(raw)


# ── Detector loop ──────────────────────────────────────────────────


class IntrusionDetector:
    """The main detector. Holds config + KAI-C client + dispatcher.

    ``step(camera)`` runs one cycle for one camera; ``run()`` schedules
    every camera every ``poll_interval_seconds`` until SIGINT/SIGTERM
    or a stop_flag is set.
    """

    def __init__(
        self,
        config: AppConfig,
        kaic_client: KaicClient,
        dispatcher: AlertDispatcher,
        *,
        now: Callable[[], _dt.datetime] = _dt.datetime.now,
        stream_client_factory: Callable[[str], KaicStreamClient] | None = None,
    ) -> None:
        self._config = config
        self._kaic = kaic_client
        self._dispatcher = dispatcher
        self._now = now
        self._stop_flag = False
        # Cache frame sources at init time so config errors surface
        # immediately, not on the first cycle.
        self._frame_sources: dict[str, FrameSource] = {}
        for camera in config.cameras:
            self._frame_sources[camera.camera_id] = build_frame_source(
                camera_id=camera.camera_id,
                url=camera.frame_url,
            )
        # WS mode: one persistent stream client per camera, built
        # lazily on first ``step``. ``stream_client_factory`` is an
        # injection point for tests; production builds the default
        # ``KaicStreamClient`` from config.
        self._stream_clients: dict[str, KaicStreamClient] = {}
        self._stream_client_factory = stream_client_factory or self._default_stream_client_factory

    def _default_stream_client_factory(self, camera_id: str) -> KaicStreamClient:
        return KaicStreamClient(
            self._config.kaic_url,
            self._config.kaic_adapter_name,
            camera_id,
            api_key=self._config.kaic_api_key,
            timeout_seconds=self._config.request_timeout_seconds,
        )

    def stop(self) -> None:
        self._stop_flag = True

    def close(self) -> None:
        """Tear down WS clients (no-op if HTTP mode). Called from the
        CLI's finally block so a clean shutdown returns sockets."""
        for client in self._stream_clients.values():
            client.close()
        self._stream_clients.clear()

    def _call_kaic(
        self,
        camera: CameraWatch,
        frame_bytes: bytes,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Send one frame to KAI-C via whichever transport this
        deployment configured. HTTP is one-shot per call; WS reuses
        a persistent connection per camera. Both raise ``KaicError``
        on transport failure so ``step()``'s catch handles them
        identically — same alert semantics across modes (no alert
        on comms failure; the failure is in KAI-C's audit log via
        the correlation_id we sent)."""
        if self._config.kaic_transport == "ws":
            client = self._stream_clients.get(camera.camera_id)
            if client is None:
                client = self._stream_client_factory(camera.camera_id)
                self._stream_clients[camera.camera_id] = client
            return client.infer_frame(
                frame_bytes=frame_bytes,
                correlation_id=correlation_id,
            )
        # Default: HTTP path (back-compat).
        return self._kaic.infer_frame(
            camera_id=camera.camera_id,
            frame_bytes=frame_bytes,
            correlation_id=correlation_id,
        )

    def step(self, camera: CameraWatch) -> list[Alert]:
        """Run one detection cycle for one camera. Returns the list
        of alerts that were fired (mostly for testing — the dispatcher
        already sent them through every channel)."""
        # Outside restricted hours → no inference, no alert.
        now = self._now()
        if not self._config.restricted_hours.contains(now):
            return []

        try:
            frame_bytes = self._frame_sources[camera.camera_id].fetch()
        except FrameSourceError as exc:
            logger.warning("frame fetch failed for %s: %s", camera.camera_id, exc)
            return []

        correlation_id = uuid.uuid4().hex
        try:
            infer_response = self._call_kaic(camera, frame_bytes, correlation_id)
        except KaicError as exc:
            logger.warning("kaic inference failed for %s: %s", camera.camera_id, exc)
            return []

        # WS mode: KAI-C audits at session grain (one correlation_id
        # per WS session, not per frame). Use whatever the stream
        # client reports as the session's effective correlation_id so
        # alerts join back to the right KAI-C audit row. HTTP mode is
        # per-call so the per-step ID and effective ID always match.
        # (Peer review H1.)
        if isinstance(infer_response, dict):
            effective_correlation_id = (
                infer_response.get("__session_correlation_id") or correlation_id
            )
            correlation_id = effective_correlation_id

        # Detection list lives at ``response.result.detections`` per
        # §5.1. Defensive parsing — adapters might return error
        # envelopes too, or (in pathological cases) non-dict bodies.
        if not isinstance(infer_response, dict):
            logger.warning(
                "kaic returned non-dict body for %s: %r", camera.camera_id, type(infer_response).__name__,
            )
            return []
        result = infer_response.get("result") or {}
        if not isinstance(result, dict) or result.get("status") == "error":
            logger.warning(
                "kaic returned error envelope for %s: %s",
                camera.camera_id,
                result.get("error", {}) if isinstance(result, dict) else result,
            )
            return []
        detections = result.get("detections") or []

        fired: list[Alert] = []
        for det in detections:
            label = str(det.get("label", "")).lower()
            if label not in self._config.watch_labels:
                continue
            bbox = det.get("bbox")
            if not isinstance(bbox, dict):
                continue
            center = bbox_center(bbox, camera.frame_width, camera.frame_height)
            if not camera.zone.contains(center):
                continue
            alert = self._build_alert(camera, det, center, correlation_id)
            self._dispatcher.fire(alert)
            fired.append(alert)
        return fired

    def _build_alert(
        self,
        camera: CameraWatch,
        detection: dict[str, Any],
        center: Point,
        correlation_id: str,
    ) -> Alert:
        label = str(detection.get("label", "object"))
        confidence = float(detection.get("confidence", 0.0))
        return Alert(
            title=f"{label.capitalize()} in restricted zone {camera.zone.name!r}",
            description=(
                f"Detected {label} (confidence={confidence:.2f}) inside zone "
                f"{camera.zone.name!r} on camera {camera.camera_id} at "
                f"({center.x:.0f}, {center.y:.0f})."
            ),
            camera_id=camera.camera_id,
            severity="high",
            correlation_id=correlation_id,
            evidence={
                "detection": detection,
                "bbox_center_px": {"x": center.x, "y": center.y},
                "zone_name": camera.zone.name,
                "kaic_adapter": self._config.kaic_adapter_name,
            },
            tags=["intrusion", "restricted-zone", label],
        )

    def run(self) -> None:
        """Daemon loop. Polls every camera every
        ``poll_interval_seconds``. Returns when ``stop()`` is called
        (e.g. via SIGINT handler) or when the process is killed."""
        logger.info(
            "intrusion-detection started: %d cameras, poll=%.1fs, watch=%s, hours=%s-%s",
            len(self._config.cameras),
            self._config.poll_interval_seconds,
            self._config.watch_labels,
            self._config.restricted_hours.start.isoformat(),
            self._config.restricted_hours.end.isoformat(),
        )
        while not self._stop_flag:
            cycle_started = time.monotonic()
            for camera in self._config.cameras:
                if self._stop_flag:
                    break
                try:
                    self.step(camera)
                except Exception:
                    # No single camera failure should kill the loop.
                    logger.exception("step() raised for camera=%s", camera.camera_id)
            elapsed = time.monotonic() - cycle_started
            sleep_for = max(0.0, self._config.poll_interval_seconds - elapsed)
            # Sleep in short slices so SIGINT is responsive.
            slept = 0.0
            while slept < sleep_for and not self._stop_flag:
                chunk = min(0.25, sleep_for - slept)
                time.sleep(chunk)
                slept += chunk
        logger.info("intrusion-detection stopped")


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intrusion-detection",
        description="Watch cameras for intrusions; alert via KAI-C audit + webhook.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle per configured camera and exit (testing).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    dispatcher = build_dispatcher(
        webhook_url=config.webhook_url,
        nats_alerts_url=config.nats_alerts_url,
        nats_alerts_token=config.nats_alerts_token,
        nats_alerts_subject_prefix=config.nats_alerts_subject_prefix,
    )
    kaic_client = KaicClient(
        config.kaic_url,
        config.kaic_adapter_name,
        api_key=config.kaic_api_key,
        timeout_seconds=config.request_timeout_seconds,
    )
    detector = IntrusionDetector(config, kaic_client, dispatcher)

    # Wire SIGINT / SIGTERM to graceful shutdown.
    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        detector.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        if args.once:
            for camera in config.cameras:
                detector.step(camera)
        else:
            detector.run()
    finally:
        detector.close()   # WS clients (no-op in HTTP mode)
        kaic_client.close()
        # Drain in-flight NATS alert publishes (no-op for stdout +
        # webhook channels). Stays at the end of the finally clause
        # so it runs even if detector/kaic_client close raises.
        dispatcher.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

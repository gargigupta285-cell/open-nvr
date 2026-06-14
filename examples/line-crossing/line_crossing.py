# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Line-crossing (tripwire) example app.

Fires an alert when a *tracked* entity crosses an operator-defined
oriented line in a counted direction — the canonical "perimeter
tripwire" / "directional people-counter" primitive. Use it for
perimeter intrusion (someone crosses the fence line inward), entrance
in/out counts, one-way corridors, or loading-dock gate traffic.

Architecture
------------

Subscribes to KAI-C's NATS inference broadcast surface like
``loitering-detection`` and ``occupancy-counting`` — zero adapter cost
on top of the detection stream already running. The crucial difference:
this app needs **per-object identity** to know that *the same* object
moved from one side of the line to the other. It therefore consumes
detections that carry a ``track_id`` (emit them with the ``bytetrack``
adapter chained after your detector, or a detector that tracks
natively). Detections without a ``track_id`` are ignored with a once-
per-process warning — counting line crossings without identity is not
well-defined.

How a crossing is decided
-------------------------

Per (camera, tripwire, track_id) we remember the previous center point.
When the next center arrives, we test whether the segment
``previous → current`` crosses the tripwire AND flips to the other side
(see ``line.py``). If it does and the direction matches the tripwire's
``count_direction``, we fire once for that crossing. Tracks idle longer
than ``track_ttl_seconds`` are forgotten so memory stays bounded.

Run::

    python line_crossing.py --config config.yml
    python line_crossing.py --config config.yml --once
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from alerts import Alert, AlertDispatcher, build_dispatcher
from line import Point, Tripwire, bbox_center

logger = logging.getLogger("line-crossing")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class CameraWire:
    """One camera + one tripwire + its pixel dimensions."""

    camera_id: str
    wire: Tripwire
    frame_width: int
    frame_height: int


@dataclass
class AppConfig:
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    watch_labels: list[str]
    track_ttl_seconds: float
    cameras: dict[str, CameraWire]  # keyed by camera_id
    webhook_url: str | None
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = "opennvr.alerts"


def load_config(path: str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")

    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required")

    if "subject_pattern" in raw:
        subject = str(raw.get("subject_pattern") or "").strip()
        if not subject:
            raise ValueError("config: 'subject_pattern' must not be empty")
    else:
        subject = "opennvr.inference.>"

    try:
        track_ttl = float(raw.get("track_ttl_seconds", 30.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("config: 'track_ttl_seconds' must be a number") from exc
    if track_ttl <= 0:
        raise ValueError("config: 'track_ttl_seconds' must be > 0")

    watch_labels_raw = raw.get("watch_labels")
    if watch_labels_raw is None:
        watch_labels = ["person"]
    else:
        watch_labels = [str(s).lower() for s in watch_labels_raw]
        if not watch_labels:
            raise ValueError(
                "config: 'watch_labels' must not be empty (omit the key to "
                "use the default ['person'], or list at least one label)"
            )

    cameras_raw = raw.get("cameras") or []
    if not cameras_raw:
        raise ValueError("config: at least one camera entry is required")
    cameras: dict[str, CameraWire] = {}
    for idx, c in enumerate(cameras_raw):
        try:
            wire = Tripwire.from_config(
                name=str(c.get("wire_name", f"wire-{idx}")),
                a=c["line"]["a"],
                b=c["line"]["b"],
                count_direction=str(c["line"].get("count_direction", "both")),
            )
            frame_width = int(c.get("frame_width", 1920))
            frame_height = int(c.get("frame_height", 1080))
            if frame_width <= 0 or frame_height <= 0:
                raise ValueError(
                    f"frame_width and frame_height must be > 0; got "
                    f"frame_width={frame_width}, frame_height={frame_height}"
                )
            cam = CameraWire(
                camera_id=str(c["camera_id"]),
                wire=wire,
                frame_width=frame_width,
                frame_height=frame_height,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"config: camera entry {idx} malformed: {exc}"
            ) from exc
        if cam.camera_id in cameras:
            raise ValueError(
                f"config: duplicate camera_id {cam.camera_id!r} at entry {idx}"
            )
        cameras[cam.camera_id] = cam

    nats_alerts_url = str(raw["nats_alerts_url"]).strip() if raw.get("nats_alerts_url") else None
    nats_alerts_token = str(raw["nats_alerts_token"]) if raw.get("nats_alerts_token") else None
    if "nats_alerts_subject_prefix" in raw:
        nats_prefix = str(raw["nats_alerts_subject_prefix"]).strip()
        if not nats_prefix:
            raise ValueError(
                "config: 'nats_alerts_subject_prefix' must not be empty"
            )
    else:
        nats_prefix = "opennvr.alerts"

    return AppConfig(
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
        watch_labels=watch_labels,
        track_ttl_seconds=track_ttl,
        cameras=cameras,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=nats_alerts_url,
        nats_alerts_token=nats_alerts_token,
        nats_alerts_subject_prefix=nats_prefix,
    )


# ── Per-track memory ───────────────────────────────────────────────


@dataclass
class _TrackMemory:
    """Last-seen center + timestamp for one (camera, track_id)."""

    last_point: Point
    last_seen: float


# ── Detector loop ──────────────────────────────────────────────────


class LineCrossingDetector:
    """Consumes inference events, remembers each track's last center,
    and fires when a track crosses a tripwire in a counted direction.

    State is per (camera_id, track_id) and bounded by ``track_ttl`` —
    idle tracks are garbage-collected so a busy scene doesn't grow the
    map without limit."""

    def __init__(self, config: AppConfig, dispatcher: AlertDispatcher, *, clock: Any = None) -> None:
        self._config = config
        self._dispatcher = dispatcher
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._tracks: dict[tuple[str, str], _TrackMemory] = {}
        self._warned_missing_track = False
        self._stop_event = asyncio.Event()
        self._nc: Any = None

    def stop(self) -> None:
        self._stop_event.set()

    # ── Pure handler (testable without NATS) ──────────────────────

    def handle_event(self, event: dict[str, Any]) -> list[Alert]:
        if not isinstance(event, dict):
            return []
        camera_id = event.get("camera_id")
        if not camera_id or camera_id not in self._config.cameras:
            return []
        camera = self._config.cameras[camera_id]

        event_ts = self._parse_ts(event.get("completed_at"))
        result = event.get("result") or {}
        detections = result.get("detections") if isinstance(result, dict) else None
        if not isinstance(detections, list):
            return []

        self._gc_stale_tracks(event_ts)

        fired: list[Alert] = []
        for det in detections:
            if not isinstance(det, dict):
                continue
            label = str(det.get("label", "")).lower()
            if label not in self._config.watch_labels:
                continue
            track_id = det.get("track_id")
            if track_id is None:
                # Line crossing without identity is undefined. Warn
                # once so the operator wires up a tracker, then move on.
                if not self._warned_missing_track:
                    logger.warning(
                        "detections have no 'track_id' — line-crossing needs "
                        "a tracking adapter (e.g. bytetrack) upstream. "
                        "Ignoring untracked detections."
                    )
                    self._warned_missing_track = True
                continue
            bbox = det.get("bbox")
            if not isinstance(bbox, dict):
                continue
            curr = bbox_center(bbox, camera.frame_width, camera.frame_height)
            key = (camera_id, str(track_id))
            prev_mem = self._tracks.get(key)
            self._tracks[key] = _TrackMemory(last_point=curr, last_seen=event_ts)
            if prev_mem is None:
                continue  # first sighting — no segment to test yet
            direction = camera.wire.crossing(prev_mem.last_point, curr)
            if direction is not None:
                alert = self._build_alert(
                    camera=camera, label=label, track_id=str(track_id),
                    direction=direction, event=event,
                )
                self._dispatcher.fire(alert)
                fired.append(alert)
        return fired

    def _gc_stale_tracks(self, now_ts: float) -> None:
        cutoff = now_ts - self._config.track_ttl_seconds
        stale = [k for k, m in self._tracks.items() if m.last_seen < cutoff]
        for k in stale:
            del self._tracks[k]

    def _parse_ts(self, raw: Any) -> float:
        if isinstance(raw, str):
            try:
                ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                return ts.timestamp()
            except ValueError:
                pass
        return self._clock().timestamp()

    def _build_alert(
        self,
        *,
        camera: CameraWire,
        label: str,
        track_id: str,
        direction: str,
        event: dict[str, Any],
    ) -> Alert:
        correlation_id = str(event.get("correlation_id") or "")
        return Alert(
            title=f"{label.capitalize()} crossed tripwire {camera.wire.name!r} ({direction})",
            description=(
                f"Track {track_id} ({label}) crossed tripwire "
                f"{camera.wire.name!r} on camera {camera.camera_id} in "
                f"direction {direction}."
            ),
            camera_id=camera.camera_id,
            severity="high",
            correlation_id=correlation_id,
            evidence={
                "label": label,
                "track_id": track_id,
                "direction": direction,
                "wire_name": camera.wire.name,
                "adapter": event.get("adapter"),
                "adapter_version": event.get("adapter_version"),
                "model_fingerprint": event.get("model_fingerprint"),
            },
            tags=["line-crossing", camera.wire.name, direction, label],
        )

    # ── NATS loop ─────────────────────────────────────────────────

    async def run(self, *, once: bool = False) -> None:
        import nats

        connect_kwargs: dict[str, Any] = {
            "servers": [self._config.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            "max_reconnect_attempts": -1,
        }
        if self._config.nats_token:
            connect_kwargs["token"] = self._config.nats_token
        self._nc = await nats.connect(**connect_kwargs)
        logger.info(
            "line-crossing started: %d cameras, watch=%s, track_ttl=%.1fs, subject=%r",
            len(self._config.cameras), self._config.watch_labels,
            self._config.track_ttl_seconds, self._config.subject_pattern,
        )
        try:
            sub = await self._nc.subscribe(self._config.subject_pattern)
            async for msg in sub.messages:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError as exc:
                    logger.warning("skipping non-JSON message on %r: %s", msg.subject, exc)
                    continue
                try:
                    self.handle_event(payload)
                except Exception:
                    logger.exception("handle_event failed for subject=%s", msg.subject)
                if once:
                    self.stop()
                if self._stop_event.is_set():
                    break
        finally:
            try:
                await self._nc.drain()
            except Exception:
                try:
                    await self._nc.close()
                except Exception:
                    pass


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="line-crossing",
        description="Subscribe to KAI-C inference events; alert on directional tripwire crossings.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument("--once", action="store_true", help="Process one event then exit.")
    parser.add_argument(
        "--log-level", default="INFO",
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
    detector = LineCrossingDetector(config, dispatcher)

    loop = asyncio.new_event_loop()

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(detector.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(detector.run(once=args.once))
    finally:
        dispatcher.close()
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

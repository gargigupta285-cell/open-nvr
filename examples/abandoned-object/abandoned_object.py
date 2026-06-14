# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Abandoned-object (unattended-item) example app.

Fires an alert when a watched object (bag, suitcase, backpack, box, …)
stays roughly stationary inside an operator-defined zone for longer
than a dwell threshold AND no person has been near it for a
suppression window — the classic "unattended baggage" primitive for
transport hubs, lobbies, and secure perimeters.

Architecture
------------

Subscribes to KAI-C's NATS inference broadcast surface like the other
monitoring apps (zero adapter cost on top of the detection stream
already running). It needs detections that carry a ``track_id`` so it
can tell that *the same* object has been sitting still — chain the
``bytetrack`` adapter after your detector, or use a detector that
tracks natively. Untracked detections are ignored with a one-time
warning.

How "abandoned" is decided
--------------------------

For each watched-object track in the zone we remember where it was
first seen and when. A track counts as *stationary* while its center
stays within ``move_tolerance_px`` of that anchor; if it moves further
the anchor resets (the object was carried, not abandoned). When a
track has been stationary in-zone for ``dwell_seconds`` AND no
``person`` detection has been seen within ``person_radius_px`` of it
during the last ``owner_grace_seconds``, we fire once.

The person-proximity suppression is what stops every parked bag next
to its owner from alerting — an object is only "abandoned" once its
likely owner has left its vicinity.

Run::

    python abandoned_object.py --config config.yml
    python abandoned_object.py --config config.yml --once
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import math
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from alerts import Alert, AlertDispatcher, build_dispatcher
from zone import Point, Zone, bbox_center

logger = logging.getLogger("abandoned-object")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class CameraZone:
    camera_id: str
    zone: Zone
    frame_width: int
    frame_height: int


@dataclass
class AppConfig:
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    object_labels: list[str]
    person_label: str
    dwell_seconds: float
    move_tolerance_px: float
    person_radius_px: float
    owner_grace_seconds: float
    track_ttl_seconds: float
    cameras: dict[str, CameraZone]
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

    subject = str(raw.get("subject_pattern") or "opennvr.inference.>").strip()
    if not subject:
        raise ValueError("config: 'subject_pattern' must not be empty")

    def _pos_float(key: str, default: float) -> float:
        try:
            val = float(raw.get(key, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config: {key!r} must be a number") from exc
        if val <= 0:
            raise ValueError(f"config: {key!r} must be > 0")
        return val

    dwell = _pos_float("dwell_seconds", 30.0)
    move_tol = _pos_float("move_tolerance_px", 40.0)
    person_radius = _pos_float("person_radius_px", 250.0)
    owner_grace = _pos_float("owner_grace_seconds", 10.0)
    track_ttl = _pos_float("track_ttl_seconds", 60.0)

    object_labels_raw = raw.get("object_labels")
    if object_labels_raw is None:
        object_labels = ["backpack", "handbag", "suitcase"]
    else:
        object_labels = [str(s).lower() for s in object_labels_raw]
        if not object_labels:
            raise ValueError("config: 'object_labels' must not be empty")
    person_label = str(raw.get("person_label", "person")).lower()

    cameras_raw = raw.get("cameras") or []
    if not cameras_raw:
        raise ValueError("config: at least one camera entry is required")
    cameras: dict[str, CameraZone] = {}
    for idx, c in enumerate(cameras_raw):
        try:
            zone = Zone.from_config(
                name=str(c.get("zone_name", f"zone-{idx}")),
                vertices=c["zone"],
            )
            frame_width = int(c.get("frame_width", 1920))
            frame_height = int(c.get("frame_height", 1080))
            if frame_width <= 0 or frame_height <= 0:
                raise ValueError("frame_width and frame_height must be > 0")
            cam = CameraZone(
                camera_id=str(c["camera_id"]), zone=zone,
                frame_width=frame_width, frame_height=frame_height,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"config: camera entry {idx} malformed: {exc}") from exc
        if cam.camera_id in cameras:
            raise ValueError(f"config: duplicate camera_id {cam.camera_id!r} at entry {idx}")
        cameras[cam.camera_id] = cam

    nats_alerts_url = str(raw["nats_alerts_url"]).strip() if raw.get("nats_alerts_url") else None
    nats_alerts_token = str(raw["nats_alerts_token"]) if raw.get("nats_alerts_token") else None
    nats_prefix = str(raw.get("nats_alerts_subject_prefix", "opennvr.alerts")).strip()
    if not nats_prefix:
        raise ValueError("config: 'nats_alerts_subject_prefix' must not be empty")

    return AppConfig(
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
        object_labels=object_labels,
        person_label=person_label,
        dwell_seconds=dwell,
        move_tolerance_px=move_tol,
        person_radius_px=person_radius,
        owner_grace_seconds=owner_grace,
        track_ttl_seconds=track_ttl,
        cameras=cameras,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=nats_alerts_url,
        nats_alerts_token=nats_alerts_token,
        nats_alerts_subject_prefix=nats_prefix,
    )


# ── Per-object state ───────────────────────────────────────────────


@dataclass
class _ObjectTrack:
    """Tracks one stationary-object candidate within a camera."""

    label: str
    anchor: Point          # where the object first settled
    settled_since: float    # when it settled at the current anchor
    last_seen: float
    alerted: bool = False


# ── Detector loop ──────────────────────────────────────────────────


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


class AbandonedObjectDetector:
    """Tracks stationary watched-objects in zones, suppresses those
    near a person, and fires when one is unattended past the dwell
    threshold."""

    def __init__(self, config: AppConfig, dispatcher: AlertDispatcher, *, clock: Any = None) -> None:
        self._config = config
        self._dispatcher = dispatcher
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        # (camera_id, track_id) -> _ObjectTrack
        self._objects: dict[tuple[str, str], _ObjectTrack] = {}
        # (camera_id) -> list of (person_center, ts) seen recently
        self._recent_people: dict[str, list[tuple[Point, float]]] = {}
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

        self._gc(camera_id, event_ts)

        # First pass: record person positions for proximity suppression.
        people_now: list[Point] = []
        for det in detections:
            if not isinstance(det, dict):
                continue
            if str(det.get("label", "")).lower() != self._config.person_label:
                continue
            bbox = det.get("bbox")
            if isinstance(bbox, dict):
                people_now.append(bbox_center(bbox, camera.frame_width, camera.frame_height))
        bucket = self._recent_people.setdefault(camera_id, [])
        for p in people_now:
            bucket.append((p, event_ts))

        # Second pass: update object tracks + test the abandon predicate.
        fired: list[Alert] = []
        for det in detections:
            if not isinstance(det, dict):
                continue
            label = str(det.get("label", "")).lower()
            if label not in self._config.object_labels:
                continue
            track_id = det.get("track_id")
            if track_id is None:
                if not self._warned_missing_track:
                    logger.warning(
                        "object detections have no 'track_id' — abandoned-object "
                        "needs a tracking adapter (e.g. bytetrack) upstream. "
                        "Ignoring untracked objects."
                    )
                    self._warned_missing_track = True
                continue
            bbox = det.get("bbox")
            if not isinstance(bbox, dict):
                continue
            center = bbox_center(bbox, camera.frame_width, camera.frame_height)
            # Only objects inside the watched zone are candidates.
            if not camera.zone.contains(center):
                continue
            key = (camera_id, str(track_id))
            track = self._objects.get(key)
            if track is None:
                self._objects[key] = _ObjectTrack(
                    label=label, anchor=center,
                    settled_since=event_ts, last_seen=event_ts,
                )
                continue
            track.last_seen = event_ts
            # Moved beyond tolerance → it was carried; reset the anchor
            # and the dwell clock, clear any prior alert latch.
            if _distance(center, track.anchor) > self._config.move_tolerance_px:
                track.anchor = center
                track.settled_since = event_ts
                track.alerted = False
                continue
            dwell = event_ts - track.settled_since
            if dwell < self._config.dwell_seconds or track.alerted:
                continue
            # Suppress if a person was near the object recently.
            if self._person_near(camera_id, track.anchor, event_ts):
                continue
            alert = self._build_alert(
                camera=camera, label=track.label, track_id=str(track_id),
                dwell_seconds=dwell, anchor=track.anchor, event=event,
            )
            self._dispatcher.fire(alert)
            fired.append(alert)
            track.alerted = True
        return fired

    def _person_near(self, camera_id: str, point: Point, now_ts: float) -> bool:
        cutoff = now_ts - self._config.owner_grace_seconds
        for p, ts in self._recent_people.get(camera_id, []):
            if ts >= cutoff and _distance(p, point) <= self._config.person_radius_px:
                return True
        return False

    def _gc(self, camera_id: str, now_ts: float) -> None:
        # Forget idle object tracks.
        obj_cutoff = now_ts - self._config.track_ttl_seconds
        stale = [
            k for k, t in self._objects.items()
            if k[0] == camera_id and t.last_seen < obj_cutoff
        ]
        for k in stale:
            del self._objects[k]
        # Trim the recent-people buffer to the owner-grace window.
        ppl_cutoff = now_ts - self._config.owner_grace_seconds
        bucket = self._recent_people.get(camera_id)
        if bucket:
            self._recent_people[camera_id] = [
                (p, ts) for p, ts in bucket if ts >= ppl_cutoff
            ]

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
        self, *, camera: CameraZone, label: str, track_id: str,
        dwell_seconds: float, anchor: Point, event: dict[str, Any],
    ) -> Alert:
        correlation_id = str(event.get("correlation_id") or "")
        return Alert(
            title=f"Unattended {label} in zone {camera.zone.name!r}",
            description=(
                f"A {label} (track {track_id}) has been stationary in zone "
                f"{camera.zone.name!r} on camera {camera.camera_id} for "
                f"{dwell_seconds:.0f}s with no person nearby."
            ),
            camera_id=camera.camera_id,
            severity="high",
            correlation_id=correlation_id,
            evidence={
                "label": label,
                "track_id": track_id,
                "dwell_seconds": round(dwell_seconds, 1),
                "anchor_px": [round(anchor.x, 1), round(anchor.y, 1)],
                "zone_name": camera.zone.name,
                "adapter": event.get("adapter"),
                "adapter_version": event.get("adapter_version"),
                "model_fingerprint": event.get("model_fingerprint"),
            },
            tags=["abandoned-object", camera.zone.name, label],
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
            "abandoned-object started: %d cameras, objects=%s, dwell=%.0fs, "
            "person_radius=%.0fpx, subject=%r",
            len(self._config.cameras), self._config.object_labels,
            self._config.dwell_seconds, self._config.person_radius_px,
            self._config.subject_pattern,
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
        prog="abandoned-object",
        description="Subscribe to KAI-C inference events; alert on unattended objects.",
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
    detector = AbandonedObjectDetector(config, dispatcher)

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

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Occupancy-counting example app.

Counts how many watched-label entities (people, vehicles, …) are
inside each operator-defined zone on every inference frame, and fires
an alert when a zone crosses an occupancy threshold — too many
(over-occupancy: a crowded exit, an over-capacity room) or, optionally,
too few (under-occupancy: a post that should always be staffed).

Architecture
------------

Like ``loitering-detection`` and unlike ``intrusion-detection``, this
app SUBSCRIBES to KAI-C's NATS inference broadcast surface
(``opennvr.inference.>``) rather than driving its own inference. It
rides whatever detection stream another app (e.g. intrusion-detection)
is already producing, so it pays zero adapter/GPU cost on top — one
inference fans out to N counting consumers.

State machine (per camera × zone)
---------------------------------

Occupancy is a level, not an event, so we alert on *transitions* of an
edge-triggered state machine rather than on every frame:

* ``normal``  → ``over``   when count > ``max_occupancy``   → fire OVER
* ``normal``  → ``under``  when count < ``min_occupancy``   → fire UNDER
* ``over`` / ``under`` → ``normal`` when count returns to the
  acceptable band → fire CLEARED (only if ``clear_alerts: true``)

Edge-triggering is what stops a crowded room from emitting one alert
per inference frame. A short ``debounce_frames`` requirement (default
1) can be raised so a single noisy frame doesn't flip the state.

Per-track identity is NOT used: occupancy is a count of in-zone
detections per frame, so a detector that emits ``track_id`` and one
that doesn't both work. Double-counting from duplicate boxes on the
same object is mitigated by the detector's own NMS upstream.

Run::

    python occupancy_counting.py --config config.yml
    python occupancy_counting.py --config config.yml --once
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from alerts import Alert, AlertDispatcher, build_dispatcher
from zone import Zone, bbox_center

logger = logging.getLogger("occupancy-counting")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class CameraZone:
    """One camera + one counted zone + its pixel dimensions.

    ``max_occupancy`` / ``min_occupancy`` may be set per-camera to
    override the app-level defaults — a doorway and a stadium concourse
    on the same deployment want very different thresholds.
    """

    camera_id: str
    zone: Zone
    frame_width: int
    frame_height: int
    max_occupancy: int
    min_occupancy: int | None


@dataclass
class AppConfig:
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    watch_labels: list[str]
    debounce_frames: int
    clear_alerts: bool
    cameras: dict[str, CameraZone]  # keyed by camera_id for O(1) lookup
    webhook_url: str | None
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = "opennvr.alerts"


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed AppConfig.

    Raises ``ValueError`` on malformed config so the CLI can surface a
    useful operator message and exit non-zero."""
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
        debounce = int(raw.get("debounce_frames", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("config: 'debounce_frames' must be an integer") from exc
    if debounce < 1:
        raise ValueError("config: 'debounce_frames' must be >= 1")

    clear_alerts = bool(raw.get("clear_alerts", False))

    # App-level default thresholds; per-camera entries may override.
    default_max = raw.get("max_occupancy")
    default_min = raw.get("min_occupancy")

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
                raise ValueError(
                    f"frame_width and frame_height must be > 0; got "
                    f"frame_width={frame_width}, frame_height={frame_height}"
                )
            # Threshold resolution: per-camera value wins, else the
            # app-level default. max_occupancy is mandatory (an
            # occupancy counter with no ceiling never fires OVER);
            # min_occupancy is optional (most deployments only care
            # about over-occupancy).
            raw_max = c.get("max_occupancy", default_max)
            if raw_max is None:
                raise ValueError(
                    f"camera entry {idx}: 'max_occupancy' is required "
                    "(set it per-camera or as an app-level default)"
                )
            max_occ = int(raw_max)
            if max_occ < 0:
                raise ValueError("'max_occupancy' must be >= 0")
            raw_min = c.get("min_occupancy", default_min)
            min_occ = int(raw_min) if raw_min is not None else None
            if min_occ is not None and min_occ < 0:
                raise ValueError("'min_occupancy' must be >= 0")
            if min_occ is not None and min_occ > max_occ:
                raise ValueError(
                    f"'min_occupancy' ({min_occ}) must be <= "
                    f"'max_occupancy' ({max_occ})"
                )
            cam = CameraZone(
                camera_id=str(c["camera_id"]),
                zone=zone,
                frame_width=frame_width,
                frame_height=frame_height,
                max_occupancy=max_occ,
                min_occupancy=min_occ,
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
                "config: 'nats_alerts_subject_prefix' must not be empty "
                "(omit the key to use the default 'opennvr.alerts')"
            )
    else:
        nats_prefix = "opennvr.alerts"

    return AppConfig(
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
        watch_labels=watch_labels,
        debounce_frames=debounce,
        clear_alerts=clear_alerts,
        cameras=cameras,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=nats_alerts_url,
        nats_alerts_token=nats_alerts_token,
        nats_alerts_subject_prefix=nats_prefix,
    )


# ── Per-camera occupancy state ─────────────────────────────────────


@dataclass
class _ZoneState:
    """Edge-triggered occupancy state for one camera × zone.

    ``level`` is the current alerting band: ``"normal"`` / ``"over"`` /
    ``"under"``. ``pending`` + ``pending_count`` implement the debounce:
    a candidate new level must persist for ``debounce_frames`` frames
    before it becomes the committed ``level`` and fires an alert.
    """

    level: str = "normal"
    pending: str | None = None
    pending_count: int = 0
    last_count: int = 0


# ── Counter loop ───────────────────────────────────────────────────


class OccupancyCounter:
    """Consumes inference events from NATS and counts in-zone entities
    per camera, firing edge-triggered occupancy alerts.

    Stateful — one ``_ZoneState`` per camera_id. State is bounded by
    the configured camera set (events from unknown cameras are dropped
    before any state is created)."""

    def __init__(self, config: AppConfig, dispatcher: AlertDispatcher) -> None:
        self._config = config
        self._dispatcher = dispatcher
        self._states: dict[str, _ZoneState] = {}
        self._stop_event = asyncio.Event()
        self._nc: Any = None

    def stop(self) -> None:
        self._stop_event.set()

    # ── Pure handler (testable without NATS) ──────────────────────

    def count_in_zone(self, camera: CameraZone, detections: list[Any]) -> int:
        """Count detections whose label is watched and whose bbox
        center falls inside the camera's zone."""
        count = 0
        for det in detections:
            if not isinstance(det, dict):
                continue
            label = str(det.get("label", "")).lower()
            if label not in self._config.watch_labels:
                continue
            bbox = det.get("bbox")
            if not isinstance(bbox, dict):
                continue
            center = bbox_center(bbox, camera.frame_width, camera.frame_height)
            if camera.zone.contains(center):
                count += 1
        return count

    def _classify(self, camera: CameraZone, count: int) -> str:
        """Map a raw count to an alerting band."""
        if count > camera.max_occupancy:
            return "over"
        if camera.min_occupancy is not None and count < camera.min_occupancy:
            return "under"
        return "normal"

    def handle_event(self, event: dict[str, Any]) -> list[Alert]:
        """Process one inference event. Returns alerts fired (empty if
        no band transition committed this frame). Pure w.r.t.
        ``self._states`` so it unit-tests without NATS."""
        if not isinstance(event, dict):
            return []
        camera_id = event.get("camera_id")
        if not camera_id or camera_id not in self._config.cameras:
            return []
        camera = self._config.cameras[camera_id]

        result = event.get("result") or {}
        detections = result.get("detections") if isinstance(result, dict) else None
        if not isinstance(detections, list):
            return []

        count = self.count_in_zone(camera, detections)
        candidate = self._classify(camera, count)
        state = self._states.setdefault(camera_id, _ZoneState())
        state.last_count = count

        # Already in the candidate band → nothing to commit; clear any
        # half-formed pending transition (the level is stable).
        if candidate == state.level:
            state.pending = None
            state.pending_count = 0
            return []

        # Debounce: the candidate must persist for N consecutive frames
        # before we commit the transition and fire.
        if state.pending == candidate:
            state.pending_count += 1
        else:
            state.pending = candidate
            state.pending_count = 1

        if state.pending_count < self._config.debounce_frames:
            return []

        previous = state.level
        state.level = candidate
        state.pending = None
        state.pending_count = 0

        # Returning to normal → only fire if clear_alerts is on.
        if candidate == "normal" and not self._config.clear_alerts:
            return []

        alert = self._build_alert(
            camera=camera, count=count, level=candidate,
            previous=previous, event=event,
        )
        self._dispatcher.fire(alert)
        return [alert]

    def _build_alert(
        self,
        *,
        camera: CameraZone,
        count: int,
        level: str,
        previous: str,
        event: dict[str, Any],
    ) -> Alert:
        correlation_id = str(event.get("correlation_id") or "")
        if level == "over":
            title = f"Over-occupancy in zone {camera.zone.name!r}"
            description = (
                f"{count} entities in zone {camera.zone.name!r} on camera "
                f"{camera.camera_id} (limit {camera.max_occupancy})."
            )
            severity = "high"
        elif level == "under":
            title = f"Under-occupancy in zone {camera.zone.name!r}"
            description = (
                f"Only {count} entities in zone {camera.zone.name!r} on "
                f"camera {camera.camera_id} (minimum {camera.min_occupancy})."
            )
            severity = "medium"
        else:  # cleared
            title = f"Occupancy back to normal in zone {camera.zone.name!r}"
            description = (
                f"Zone {camera.zone.name!r} on camera {camera.camera_id} "
                f"returned to normal occupancy ({count}) from {previous!r}."
            )
            severity = "low"
        return Alert(
            title=title,
            description=description,
            camera_id=camera.camera_id,
            severity=severity,
            correlation_id=correlation_id,
            evidence={
                "count": count,
                "level": level,
                "previous_level": previous,
                "max_occupancy": camera.max_occupancy,
                "min_occupancy": camera.min_occupancy,
                "zone_name": camera.zone.name,
                "watch_labels": self._config.watch_labels,
                "adapter": event.get("adapter"),
                "adapter_version": event.get("adapter_version"),
                "model_fingerprint": event.get("model_fingerprint"),
            },
            tags=["occupancy", level, camera.zone.name],
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
            "occupancy-counting started: %d cameras, watch=%s, "
            "debounce=%d, clear_alerts=%s, subject=%r",
            len(self._config.cameras), self._config.watch_labels,
            self._config.debounce_frames, self._config.clear_alerts,
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
        prog="occupancy-counting",
        description="Subscribe to KAI-C inference events; alert on zone occupancy thresholds.",
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
    counter = OccupancyCounter(config, dispatcher)

    loop = asyncio.new_event_loop()

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(counter.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(counter.run(once=args.once))
    finally:
        dispatcher.close()
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

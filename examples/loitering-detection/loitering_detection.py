# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Loitering-detection example app.

The second first-party OpenNVR example app per §12 of the AI Adapter
Contract. Watches one or more cameras for watched-label entities
(person, car, etc.) lingering in operator-defined zones beyond a
dwell threshold, and fires alerts when threshold is exceeded.

Architecture
------------

Unlike ``intrusion-detection`` (which DRIVES inference by polling
KAI-C or holding a WS session per camera), this app SUBSCRIBES to
KAI-C's NATS broadcast surface (``opennvr.inference.*`` per the NATS event bus).
That means it consumes inference results that intrusion-detection
(or any other app) is already driving — adapter GPU is paid once,
N subscribers fan out from one inference stream.

When to use which::

    intrusion-detection:   want sub-second alerts on dedicated cameras;
                           OK with adapter inference cost per app

    loitering-detection:   want to ride the inference stream another
                           app is already driving; cheaper for ops

Both can coexist on the same deployment — typically intrusion-
detection drives YOLOv8 on the cameras you care about, and one or
more loitering-detection / counting / dashboard processes subscribe
to the same NATS stream.

State machine (per camera)
--------------------------

For each camera × watched-label pair:

* No presence in zone → idle
* First presence-frame → ``present_since = event_ts``, ``alerted = False``
* Continuous presence → update ``last_seen``
* If ``now - present_since >= threshold_seconds`` AND ``not alerted``
    → fire alert, set ``alerted = True``
* If ``now - last_seen > grace_period_seconds`` (no presence frames
    for this long) → reset state (so a fresh dwell episode starts
    cleanly the next time someone enters)

Per-camera × per-label: a person and a car loitering simultaneously
fire separate alerts. Per-track tracking (one person leaves, a
different one arrives) is NOT modeled in v1 — the upstream adapter
would need to emit ``track_id`` on each detection AND we'd need to
swap to per-(camera, label, track_id) state, which is a follow-up.

Run::

    python loitering_detection.py --config config.yml
    python loitering_detection.py --config config.yml --once  # one event then exit
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from alerts import Alert, AlertDispatcher, build_dispatcher
from zone import Point, Zone, bbox_center

logger = logging.getLogger("loitering-detection")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class CameraWatch:
    """One camera + its zone + its pixel dimensions. We don't subscribe
    per-camera — we subscribe to the wildcard ``opennvr.inference.>``
    and match by ``camera_id`` from the event payload — but we still
    need this struct so the zone math + alert payload can look up the
    right zone polygon when an event arrives."""

    camera_id: str
    zone: Zone
    frame_width: int
    frame_height: int


@dataclass
class AppConfig:
    """Top-level config loaded from YAML."""

    nats_url: str
    nats_token: str | None
    subject_pattern: str
    watch_labels: list[str]
    threshold_seconds: float
    grace_period_seconds: float
    cameras: dict[str, CameraWatch]  # keyed by camera_id for O(1) lookup
    webhook_url: str | None
    # Optional NATS alert fan-out. Distinct from ``nats_url``: that one
    # is where we SUBSCRIBE for inference events, this one is where we
    # PUBLISH our alerts. In a single-host deployment both will point
    # at the same broker; in a federated setup they may differ.
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = "opennvr.alerts"


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed AppConfig.

    Raises ``ValueError`` on malformed config — caller's job to
    surface a useful operator message and exit non-zero."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")

    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required")

    # Subject pattern — default to all inference results, operator
    # can narrow to a single adapter (e.g. opennvr.inference.yolov8.>)
    if "subject_pattern" in raw:
        subject = str(raw.get("subject_pattern") or "").strip()
        if not subject:
            raise ValueError("config: 'subject_pattern' must not be empty")
    else:
        subject = "opennvr.inference.>"

    try:
        threshold = float(raw.get("threshold_seconds", 60.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("config: 'threshold_seconds' must be a number") from exc
    if threshold <= 0:
        raise ValueError("config: 'threshold_seconds' must be > 0")

    try:
        grace = float(raw.get("grace_period_seconds", 5.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("config: 'grace_period_seconds' must be a number") from exc
    if grace <= 0:
        raise ValueError("config: 'grace_period_seconds' must be > 0")

    cameras_raw = raw.get("cameras") or []
    if not cameras_raw:
        raise ValueError("config: at least one camera entry is required")
    cameras: dict[str, CameraWatch] = {}
    for idx, c in enumerate(cameras_raw):
        try:
            zone = Zone.from_config(
                name=str(c.get("zone_name", f"zone-{idx}")),
                vertices=c["zone"],
            )
            frame_width = int(c.get("frame_width", 1920))
            frame_height = int(c.get("frame_height", 1080))
            # Frame dimensions are used to scale normalized bboxes back
            # to pixels for zone math. Non-positive values silently
            # bucket every detection at the origin and miss every zone
            # check; refuse the config rather than swallow this.
            # (Peer review H3.)
            if frame_width <= 0 or frame_height <= 0:
                raise ValueError(
                    f"frame_width and frame_height must be > 0; got "
                    f"frame_width={frame_width}, frame_height={frame_height}"
                )
            cam = CameraWatch(
                camera_id=str(c["camera_id"]),
                zone=zone,
                frame_width=frame_width,
                frame_height=frame_height,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"config: camera entry {idx} malformed: {exc}"
            ) from exc
        # Two camera entries with the same id would silently overwrite —
        # the second wins and the operator's intent for the first is
        # lost without warning. Refuse at validate time.
        # (Peer review H1.)
        if cam.camera_id in cameras:
            raise ValueError(
                f"config: duplicate camera_id {cam.camera_id!r} at entry {idx}"
            )
        cameras[cam.camera_id] = cam

    # ``watch_labels`` defaults to ``["person"]`` when absent — but an
    # explicit empty list should be rejected rather than silently
    # producing a detector that never matches anything. (Peer review H2.)
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
        threshold_seconds=threshold,
        grace_period_seconds=grace,
        cameras=cameras,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=nats_alerts_url,
        nats_alerts_token=nats_alerts_token,
        nats_alerts_subject_prefix=nats_prefix,
    )


# ── Per-(camera, label) dwell state ────────────────────────────────


@dataclass
class _DwellState:
    """The state machine for one (camera_id, label) pair.

    ``present_since`` is the timestamp of the first uninterrupted
    presence frame; ``last_seen`` is the timestamp of the most recent
    presence frame. ``alerted`` flips True once we've fired the
    threshold-crossing alert so we don't re-fire on every subsequent
    frame in the same dwell episode.

    The state is RESET (set back to idle) when ``last_seen`` is older
    than ``grace_period_seconds`` from the current event — the
    grace period absorbs brief detection misses (occlusion, false
    negatives) without falsely splitting one dwell into two.
    """

    present_since: float | None = None
    last_seen: float | None = None
    alerted: bool = False


# ── Detector loop ──────────────────────────────────────────────────


class LoiteringDetector:
    """Consumes ``opennvr.inference.{adapter}.{camera_id}.completed``
    events from NATS and tracks per-camera × per-label dwell time.

    Stateful — one ``_DwellState`` per (camera_id, label) key. The
    state survives across events but is reset to idle when
    ``grace_period_seconds`` elapse without a presence frame.

    Override ``_build_alert`` or post-process ``fire`` in a subclass
    if you want to enrich the alert payload (snapshot URL, evidence
    bundle, etc.). The default fires a §11.5-shaped Alert via the
    same ``AlertDispatcher`` intrusion-detection uses.
    """

    def __init__(
        self,
        config: AppConfig,
        dispatcher: AlertDispatcher,
        *,
        clock: Any = None,
    ) -> None:
        self._config = config
        self._dispatcher = dispatcher
        # ``clock`` is a callable returning a UTC datetime; we fall
        # back to per-event timestamps from the NATS payload, but the
        # grace-period reset needs a "now" reference between events.
        # Tests pass a controlled clock for determinism.
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        # Bounded by configured cameras × watch_labels — peer review
        # confirmed: events from unknown camera_ids short-circuit
        # before reaching setdefault, so this dict can't grow past
        # ``len(config.cameras) * len(config.watch_labels)`` entries.
        self._states: dict[tuple[str, str], _DwellState] = {}
        self._stop_event = asyncio.Event()
        self._nc: Any = None

    def stop(self) -> None:
        self._stop_event.set()

    # ── Pure handlers (testable without NATS) ─────────────────────

    def handle_event(self, event: dict[str, Any]) -> list[Alert]:
        """Process one ``InferenceCompletedEvent``. Returns the list of
        alerts that were fired (empty list if no threshold crossed).
        Pure function w.r.t. ``self._states`` — safe to unit-test
        without spinning up NATS."""
        if not isinstance(event, dict):
            return []
        camera_id = event.get("camera_id")
        if not camera_id or camera_id not in self._config.cameras:
            return []
        camera = self._config.cameras[camera_id]

        # Use the event's completed_at timestamp if present (so the
        # state machine tracks wall-clock dwell on the adapter side,
        # not network latency to us). Fall back to "now" if missing.
        event_ts = self._parse_ts(event.get("completed_at"))

        result = event.get("result") or {}
        detections = result.get("detections") if isinstance(result, dict) else None
        if not isinstance(detections, list):
            return []

        # For each watched-label that has at least one in-zone
        # detection in THIS event, update its dwell state. We use a
        # set so multiple detections of the same label in one frame
        # only count once.
        labels_seen_in_zone: set[str] = set()
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
                labels_seen_in_zone.add(label)

        # Reset stale state — ONLY for (camera, label) pairs whose
        # entity is NOT present in this frame. A label that IS in
        # this frame's labels_seen_in_zone counts as a fresh
        # presence ping; we refresh its ``last_seen`` rather than
        # GC the state. This is what makes the grace-period
        # semantics correct under sparse inference frequencies
        # (1 fps with grace=5s tolerates up-to-5s detection gaps;
        # at 0.1 fps with grace=5s a continuous presence frame at
        # t=10 would otherwise look stale vs. t=0).
        self._gc_absent_labels(camera_id, labels_seen_in_zone, event_ts)

        fired_now: list[Alert] = []
        for label in labels_seen_in_zone:
            key = (camera_id, label)
            state = self._states.setdefault(key, _DwellState())
            # Defensive against out-of-order events: NATS doesn't
            # strictly order across publishers, and a misbehaving
            # publisher can emit events with non-monotonic
            # ``completed_at`` timestamps. If an event is older
            # than the most recent one we've already processed
            # for this (camera, label), treat it as a duplicate /
            # stale message and skip — dwell math relies on
            # monotonic time and a backward jump would silently
            # corrupt the state. (Peer review M2.)
            if state.last_seen is not None and event_ts < state.last_seen:
                logger.debug(
                    "skipping out-of-order event for %s/%s "
                    "(event_ts=%.3f < last_seen=%.3f)",
                    camera_id, label, event_ts, state.last_seen,
                )
                continue
            if state.present_since is None:
                # First presence of this label since the last reset.
                # State is "reset" either by never having existed
                # or by ``_gc_absent_labels`` deleting it on a
                # previous frame where this label was absent for
                # longer than grace_period.
                state.present_since = event_ts
                state.alerted = False
            state.last_seen = event_ts
            dwell = event_ts - state.present_since
            if dwell >= self._config.threshold_seconds and not state.alerted:
                alert = self._build_alert(
                    camera=camera, label=label, dwell_seconds=dwell, event=event,
                )
                self._dispatcher.fire(alert)
                fired_now.append(alert)
                state.alerted = True
        return fired_now

    def _gc_absent_labels(
        self,
        camera_id: str,
        labels_present_now: set[str],
        now_ts: float,
    ) -> None:
        """Reset state for any (camera, label) whose label is NOT
        present in the current frame AND whose ``last_seen`` is older
        than ``grace_period_seconds``. This is how a brief detection
        gap (occlusion, false negative) gets absorbed without
        prematurely ending a dwell episode."""
        cutoff = now_ts - self._config.grace_period_seconds
        stale_keys = [
            key for key, state in self._states.items()
            if key[0] == camera_id
            and key[1] not in labels_present_now
            and state.last_seen is not None
            and state.last_seen < cutoff
        ]
        for key in stale_keys:
            del self._states[key]

    def _parse_ts(self, raw: Any) -> float:
        """Extract a POSIX timestamp from a the NATS event bus ``completed_at`` ISO
        string. Falls back to the clock for missing / malformed
        values so a misbehaving publisher doesn't break the state
        machine."""
        if isinstance(raw, str):
            try:
                # Pydantic emits ISO with a trailing 'Z' or offset.
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
        camera: CameraWatch,
        label: str,
        dwell_seconds: float,
        event: dict[str, Any],
    ) -> Alert:
        correlation_id = str(event.get("correlation_id") or "")
        return Alert(
            title=f"{label.capitalize()} loitering in zone {camera.zone.name!r}",
            description=(
                f"Detected {label} dwelling in zone {camera.zone.name!r} on "
                f"camera {camera.camera_id} for {dwell_seconds:.1f}s "
                f"(threshold {self._config.threshold_seconds:.1f}s)."
            ),
            camera_id=camera.camera_id,
            severity="medium",
            correlation_id=correlation_id,
            evidence={
                "label": label,
                "dwell_seconds": round(dwell_seconds, 2),
                "threshold_seconds": self._config.threshold_seconds,
                "zone_name": camera.zone.name,
                "adapter": event.get("adapter"),
                "adapter_version": event.get("adapter_version"),
                "model_fingerprint": event.get("model_fingerprint"),
            },
            tags=["loitering", camera.zone.name, label],
        )

    # ── NATS loop ─────────────────────────────────────────────────

    async def run(self, *, once: bool = False) -> None:
        """Connect to NATS, subscribe, drive the state machine on
        every received event. Returns when ``stop()`` is called or
        when ``once=True`` and one matching event has been
        processed."""
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
            "loitering-detection started: %d cameras, watch=%s, "
            "threshold=%.1fs, grace=%.1fs, subject=%r",
            len(self._config.cameras), self._config.watch_labels,
            self._config.threshold_seconds, self._config.grace_period_seconds,
            self._config.subject_pattern,
        )
        try:
            sub = await self._nc.subscribe(self._config.subject_pattern)
            async for msg in sub.messages:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "skipping non-JSON message on %r: %s",
                        msg.subject, exc,
                    )
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
        prog="loitering-detection",
        description="Subscribe to KAI-C inference events; alert on dwell-threshold crossings.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one event then exit (smoke testing).",
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
    detector = LoiteringDetector(config, dispatcher)

    loop = asyncio.new_event_loop()

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(detector.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(detector.run(once=args.once))
    finally:
        # Drain in-flight NATS alert publishes BEFORE we close the
        # asyncio loop — the dispatcher runs its NATS client on its
        # own daemon thread, but ``close`` blocks until drain
        # completes, which is what we want at shutdown.
        dispatcher.close()
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Line-crossing (tripwire) example app — now on the ``opennvr-app-sdk``.

Fires an alert when a *tracked* entity crosses an operator-defined
oriented line in a counted direction — the canonical "perimeter
tripwire" / "directional people-counter" primitive. Use it for
perimeter intrusion (someone crosses the fence line inward), entrance
in/out counts, one-way corridors, or loading-dock gate traffic.

What lives where after the migration
------------------------------------

The SDK's :class:`~opennvr_app_sdk.Detector` base owns the NATS
subscribe loop, per-message JSON decoding + exception isolation, the
``camera_id`` / ``result.detections`` payload walk, ``completed_at``
timestamp parsing with a clock fallback, alert dispatch, the CLI, and
signal handling. The tripwire geometry moved to
``opennvr_app_sdk.geometry`` and the §11.5 alert stack to
``opennvr_app_sdk.alerts`` (thin shims remain at ``line.py`` /
``alerts.py`` for import compatibility).

What's left here is the rule — the per-(camera, track) crossing test —
plus this app's config parsing and its declarative MANIFEST.

Architecture (unchanged)
------------------------

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

Per (camera, tripwire, track_id) we remember the previous center point
(the SDK's ``keyed_state`` holds it, TTL = ``track_ttl_seconds``).
When the next center arrives, we test whether the segment
``previous → current`` crosses the tripwire AND flips to the other side
(see ``opennvr_app_sdk.geometry.Tripwire``). If it does and the
direction matches the tripwire's ``count_direction``, we fire once for
that crossing. Tracks idle longer than ``track_ttl_seconds`` are
forgotten so memory stays bounded — the GC is driven manually
(``auto_gc=False``) at the top of each event, matching the historical
semantics: a track that went stale is pruned even on the very event
that re-sights it, so its next sighting starts a fresh episode with no
segment to test.

Run::

    python line_crossing.py --config config.yml
    python line_crossing.py --config config.yml --once
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opennvr_app_sdk import (
    Alert,
    AlertType,
    AppManifest,
    Detector,
    Param,
    app,
)
from opennvr_app_sdk.config import load_yaml
from opennvr_app_sdk.geometry import Point, Tripwire, bbox_center
from opennvr_app_sdk.state import keyed_state

logger = logging.getLogger("line-crossing")


MANIFEST = AppManifest(
    id="line-crossing",
    name="Line Crossing",
    version="1.0.0",
    category="perimeter",
    summary=(
        "Alerts when a tracked object crosses an oriented tripwire in a "
        "counted direction."
    ),
    # Needs per-object identity on top of detection — chain a tracking
    # adapter (e.g. bytetrack) so detections carry ``track_id``.
    requires_tasks=["object_detection", "multi_object_tracking"],
    subscribes="opennvr.inference.>",
    params=[
        Param("watch_labels", list, default=["person"]),
        Param("track_ttl_seconds", float, default=30.0,
              description="Idle time after which a track's last position is forgotten."),
        Param("line", "geometry.tripwire", per_camera=True),  # drawn in the catalog UI
    ],
    emits=[AlertType("line-crossing", severity="high")],
)


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
    raw = load_yaml(path)

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


# ── The rule ───────────────────────────────────────────────────────


class LineCrossingDetector(Detector):
    """Consumes inference events (via the SDK's Detector loop),
    remembers each track's last center, and fires when a track crosses
    a tripwire in a counted direction.

    State is per (camera_id, track_id) — one SDK ``keyed_state`` record
    whose ``data["last_point"]`` carries the previous bbox center —
    and bounded by ``track_ttl_seconds``: idle tracks are garbage-
    collected so a busy scene doesn't grow the map without limit.
    """

    manifest = MANIFEST

    def setup(self) -> None:
        # auto_gc off: the historical GC ran unconditionally at the top
        # of each event (before the touch), so a track that went stale
        # is pruned even on the event that re-sights it — its next
        # sighting is a fresh first sighting with no segment to test.
        # ``touch`` with auto_gc would instead spare the touched key.
        self._tracks = keyed_state(
            ttl=self.cfg.track_ttl_seconds,
            auto_gc=False,
        )
        self._warned_missing_track = False

    def on_detections(
        self,
        camera_id: str,
        detections: list[dict[str, Any]],
        event: dict[str, Any],
    ) -> list[Alert]:
        """The crossing rule for one event. Returns the alerts to fire
        (the SDK base dispatches them). The existing tests drive it
        through ``handle_event`` without spinning up NATS."""
        camera = self.cfg.cameras.get(camera_id)
        if camera is None:
            # Another monitoring app may be watching this camera; we're not.
            return []

        event_ts = self.parse_event_ts(event.get("completed_at"))
        self._tracks.gc(event_ts)

        fired: list[Alert] = []
        for det in detections:
            if not isinstance(det, dict):
                continue
            label = str(det.get("label", "")).lower()
            if label not in self.cfg.watch_labels:
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
            record = self._tracks.get(key)
            prev_point: Point | None = record.data.get("last_point") if record else None
            state = self._tracks.touch(key, at=event_ts)
            state.data["last_point"] = curr
            if prev_point is None:
                continue  # first sighting — no segment to test yet
            direction = camera.wire.crossing(prev_point, curr)
            if direction is not None:
                fired.append(self._build_alert(
                    camera=camera, label=label, track_id=str(track_id),
                    direction=direction, event=event,
                ))
        return fired

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


# Spec-preferred short name; ``LineCrossingDetector`` is the historical
# one the tests (and README snippets) import.
LineCrossing = LineCrossingDetector


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``[project.scripts]``). The SDK
    runner owns argparse, logging, signals, and the dispatcher."""
    return app(LineCrossingDetector, load_config=load_config).run(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

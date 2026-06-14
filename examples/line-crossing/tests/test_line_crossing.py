# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Focused tests for the line-crossing predicate and tripwire geometry."""
from __future__ import annotations

import line_crossing as lc
from line import Point, Tripwire


# ── Geometry ───────────────────────────────────────────────────────


def test_tripwire_detects_directional_crossing():
    # Vertical wire down the middle of a 1000-wide frame, A=top B=bottom.
    wire = Tripwire.from_config("mid", a=[500, 0], b=[500, 1000], count_direction="both")
    # Moving left→right crosses it.
    assert wire.crossing(Point(400, 500), Point(600, 500)) is not None
    # Moving right→left crosses it the other way.
    assert wire.crossing(Point(600, 500), Point(400, 500)) is not None
    # Moving along one side does not.
    assert wire.crossing(Point(400, 100), Point(400, 900)) is None


def test_tripwire_respects_count_direction():
    wire = Tripwire.from_config("mid", a=[500, 0], b=[500, 1000], count_direction="a_to_b")
    one_way = wire.crossing(Point(400, 500), Point(600, 500))
    other_way = wire.crossing(Point(600, 500), Point(400, 500))
    # Exactly one of the two directions should be counted.
    assert (one_way is None) != (other_way is None)


def test_grazing_the_line_is_not_a_crossing():
    wire = Tripwire.from_config("mid", a=[500, 0], b=[500, 1000])
    # Ends exactly on the line → not a committed crossing.
    assert wire.crossing(Point(400, 500), Point(500, 500)) is None


# ── handle_event state machine ─────────────────────────────────────


def _camera() -> lc.CameraWire:
    wire = Tripwire.from_config("mid", a=[960, 0], b=[960, 1080], count_direction="both")
    return lc.CameraWire(camera_id="cam-1", wire=wire, frame_width=1920, frame_height=1080)


def _config(camera) -> lc.AppConfig:
    return lc.AppConfig(
        nats_url="nats://x:4222", nats_token=None,
        subject_pattern="opennvr.inference.>", watch_labels=["person"],
        track_ttl_seconds=30.0, cameras={camera.camera_id: camera},
        webhook_url=None,
    )


class _NullDispatcher:
    def fire(self, alert):  # noqa: ANN001
        return {}


def _event(track_id, cx_norm, *, ts="2026-01-01T00:00:00Z"):
    # A person whose bbox center sits at cx_norm of frame width.
    return {
        "camera_id": "cam-1",
        "correlation_id": "corr-1",
        "completed_at": ts,
        "result": {
            "detections": [
                {"label": "person", "track_id": track_id,
                 "bbox": {"x": cx_norm - 0.02, "y": 0.48, "w": 0.04, "h": 0.04}},
            ]
        },
    }


def _detector(camera):
    return lc.LineCrossingDetector(_config(camera), _NullDispatcher())


def test_first_sighting_does_not_fire():
    d = _detector(_camera())
    assert d.handle_event(_event("t1", 0.30)) == []   # left of wire, first frame


def test_track_crossing_fires_once():
    d = _detector(_camera())
    d.handle_event(_event("t1", 0.30, ts="2026-01-01T00:00:00Z"))      # left
    fired = d.handle_event(_event("t1", 0.70, ts="2026-01-01T00:00:01Z"))  # right → cross
    assert len(fired) == 1
    assert fired[0].evidence["track_id"] == "t1"
    assert fired[0].evidence["direction"] in ("a_to_b", "b_to_a")


def test_no_recross_without_movement_back():
    d = _detector(_camera())
    d.handle_event(_event("t1", 0.30, ts="2026-01-01T00:00:00Z"))
    d.handle_event(_event("t1", 0.70, ts="2026-01-01T00:00:01Z"))      # cross → fire
    again = d.handle_event(_event("t1", 0.75, ts="2026-01-01T00:00:02Z"))  # stays right
    assert again == []


def test_untracked_detections_ignored():
    d = _detector(_camera())
    ev = _event("t1", 0.70)
    del ev["result"]["detections"][0]["track_id"]
    assert d.handle_event(ev) == []


def test_unknown_camera_ignored():
    d = _detector(_camera())
    ev = _event("t1", 0.70)
    ev["camera_id"] = "cam-other"
    assert d.handle_event(ev) == []

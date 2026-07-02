# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
Zone geometry — point-in-polygon tests for per-camera zone evaluation.

Promoted verbatim from ``examples/loitering-detection/zone.py`` (itself
a copy of ``examples/intrusion-detection/zone.py``). The old READMEs
framed this as "polygon math is per-camera business logic, not
framework-y enough to factor into a library" — the App SDK spec (§04)
reverses that call: every zone-shaped app copies exactly this file, so
it is the definition of framework-y.

A zone is a closed polygon in pixel coordinates of the camera frame.
Detections whose bbox center falls inside the polygon contribute to
the app's per-zone logic (dwell timers, intrusion checks, counting).

Ray-casting algorithm: cast a horizontal ray from the test point to
the right and count intersections with polygon edges. Odd = inside,
even = outside. Standard textbook implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Point:
    """2D point in pixel coordinates (origin top-left, y-down)."""

    x: float
    y: float


@dataclass
class Zone:
    """A named polygonal zone in pixel coordinates.

    Polygon vertices are pixel coords on the camera frame. The polygon
    is implicitly closed (we connect the last vertex back to the
    first). A degenerate zone with < 3 vertices is rejected.
    """

    name: str
    polygon: list[Point]

    def __post_init__(self) -> None:
        if len(self.polygon) < 3:
            raise ValueError(
                f"Zone {self.name!r} requires at least 3 vertices; got {len(self.polygon)}"
            )

    def contains(self, point: Point) -> bool:
        """True if the point lies inside the polygon (ray-casting)."""
        return _point_in_polygon(point, self.polygon)

    @classmethod
    def from_config(cls, name: str, vertices: Sequence[Sequence[float]]) -> "Zone":
        """Build a Zone from config-style vertex list ``[[x, y], [x, y], ...]``."""
        return cls(
            name=name,
            polygon=[Point(float(v[0]), float(v[1])) for v in vertices],
        )


def _point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon. Points exactly on an edge are
    treated as INSIDE — the safer choice for a monitoring app (we'd
    rather track a borderline person than miss them)."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        pi, pj = polygon[i], polygon[j]
        if _point_on_segment(point, pi, pj):
            return True
        if (pi.y > point.y) != (pj.y > point.y):
            slope_x = (pj.x - pi.x) * (point.y - pi.y) / (pj.y - pi.y) + pi.x
            if point.x < slope_x:
                inside = not inside
        j = i
    return inside


def _point_on_segment(point: Point, a: Point, b: Point, *, eps: float = 1e-9) -> bool:
    cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x)
    if abs(cross) > eps:
        return False
    dot = (point.x - a.x) * (b.x - a.x) + (point.y - a.y) * (b.y - a.y)
    if dot < -eps:
        return False
    sq_len = (b.x - a.x) ** 2 + (b.y - a.y) ** 2
    return dot - sq_len <= eps


def bbox_center(bbox_normalized: dict, frame_width: int, frame_height: int) -> Point:
    """Convert a §5.1 ``NormalizedBBox`` (x/y/w/h in [0, 1]) into a
    pixel-space center point given the camera's actual frame size.
    Defensive against partial bboxes: missing keys default to 0."""
    def _coerce(key: str) -> float:
        value = bbox_normalized.get(key, 0.0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    x = _coerce("x")
    y = _coerce("y")
    w = _coerce("w")
    h = _coerce("h")
    return Point(
        x=(x + w / 2.0) * frame_width,
        y=(y + h / 2.0) * frame_height,
    )

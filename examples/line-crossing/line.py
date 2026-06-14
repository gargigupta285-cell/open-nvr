# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Directional line-crossing geometry for the tripwire example.

A tripwire is an oriented line segment A→B in pixel coordinates of the
camera frame. A tracked object crosses it when the segment between its
previous and current center positions intersects the tripwire. The
*direction* of the crossing (which way it went) is determined by which
side of the line the object started on:

    side(P) = sign of the 2D cross product (B - A) × (P - A)

    side > 0  → "left" of A→B
    side < 0  → "right" of A→B

By convention we name the two directions ``a_to_b`` and ``b_to_a`` so an
operator can label them meaningfully ("enter" / "exit", "in" / "out").
A configured ``count_direction`` of ``both`` counts either way.

This file is intentionally self-contained, copy-as-template geometry —
the same design intent as ``zone.py`` in the zone-based examples.
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
class Tripwire:
    """An oriented line segment A→B with a counted direction.

    ``count_direction`` is one of ``"a_to_b"``, ``"b_to_a"``, ``"both"``.
    """

    name: str
    a: Point
    b: Point
    count_direction: str = "both"

    def __post_init__(self) -> None:
        if self.a.x == self.b.x and self.a.y == self.b.y:
            raise ValueError(f"Tripwire {self.name!r}: A and B must differ")
        if self.count_direction not in ("a_to_b", "b_to_a", "both"):
            raise ValueError(
                f"Tripwire {self.name!r}: count_direction must be "
                f"'a_to_b', 'b_to_a' or 'both'; got {self.count_direction!r}"
            )

    @classmethod
    def from_config(
        cls, name: str, a: Sequence[float], b: Sequence[float],
        count_direction: str = "both",
    ) -> "Tripwire":
        return cls(
            name=name,
            a=Point(float(a[0]), float(a[1])),
            b=Point(float(b[0]), float(b[1])),
            count_direction=count_direction,
        )

    def side(self, p: Point) -> float:
        """Signed side of point ``p`` relative to the oriented line A→B.
        Positive = left, negative = right, ~0 = on the line."""
        return (self.b.x - self.a.x) * (p.y - self.a.y) - (
            self.b.y - self.a.y
        ) * (p.x - self.a.x)

    def crossing(self, prev: Point, curr: Point) -> str | None:
        """Return the crossing direction (``"a_to_b"`` / ``"b_to_a"``) if
        the movement ``prev → curr`` crosses this tripwire in a counted
        direction, else ``None``.

        Two conditions must both hold:
        1. the segment prev→curr intersects the segment A→B
           (so the object physically traversed the wire), and
        2. the side sign flipped from prev to curr (so it genuinely
           changed sides, not merely touched the line).
        """
        side_prev = self.side(prev)
        side_curr = self.side(curr)
        # Must end up on opposite sides (strict) — grazing the line
        # (one side == 0) is not a committed crossing.
        if side_prev == 0 or side_curr == 0:
            return None
        if (side_prev > 0) == (side_curr > 0):
            return None
        if not _segments_intersect(prev, curr, self.a, self.b):
            return None
        # side_prev > 0 means it started on the LEFT of A→B and ended on
        # the right → it moved across in the A→B-rightward sense, which
        # we name "a_to_b". The opposite is "b_to_a".
        direction = "a_to_b" if side_prev > 0 else "b_to_a"
        if self.count_direction in (direction, "both"):
            return direction
        return None


def _orientation(p: Point, q: Point, r: Point) -> int:
    """0 = collinear, 1 = clockwise, 2 = counter-clockwise."""
    val = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
    if val == 0:
        return 0
    return 1 if val > 0 else 2


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    """True if q lies on segment p–r (assuming the three are collinear)."""
    return (
        min(p.x, r.x) <= q.x <= max(p.x, r.x)
        and min(p.y, r.y) <= q.y <= max(p.y, r.y)
    )


def _segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Standard segment-intersection test (p1–p2 vs p3–p4), handling
    the collinear-overlap special cases."""
    o1 = _orientation(p1, p2, p3)
    o2 = _orientation(p1, p2, p4)
    o3 = _orientation(p3, p4, p1)
    o4 = _orientation(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p3, p2):
        return True
    if o2 == 0 and _on_segment(p1, p4, p2):
        return True
    if o3 == 0 and _on_segment(p3, p1, p4):
        return True
    if o4 == 0 and _on_segment(p3, p2, p4):
        return True
    return False


def bbox_center(bbox_normalized: dict, frame_width: int, frame_height: int) -> Point:
    """Convert a §5.1 ``NormalizedBBox`` (x/y/w/h in [0, 1]) into a
    pixel-space center point. Defensive against partial bboxes."""
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

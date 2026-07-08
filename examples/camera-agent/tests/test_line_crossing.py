# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Line-crossing counter: deterministic geometry + counting, track
extraction, and the crossing monitor mode end-to-end (with stubbed tracks)."""
from __future__ import annotations

import asyncio

from camera_agent import (
    AppConfig, CameraAgentRuntime, LineCounter, MonitorManager, _line_side,
)
from context import CameraSpec

# A vertical line at x=0.5, from (0.5,0) down to (0.5,1).
VLINE = ((0.5, 0.0), (0.5, 1.0))


def test_line_side_sign():
    # For the line (0.5,0)->(0.5,1): left of it is the positive side.
    assert _line_side(VLINE, 0.2, 0.5) > 0   # left
    assert _line_side(VLINE, 0.8, 0.5) < 0   # right
    assert _line_side(VLINE, 0.5, 0.5) == 0  # on the line


def test_counter_counts_one_crossing_per_track_per_direction():
    c = LineCounter(VLINE)
    # track 1 moves left → right; it ends on the negative side → "out".
    c.update([{"id": 1, "x": 0.2, "y": 0.5}])
    c.update([{"id": 1, "x": 0.4, "y": 0.5}])   # still left
    c.update([{"id": 1, "x": 0.7, "y": 0.5}])   # crossed → out
    assert c.totals() == {"in": 0, "out": 1, "net": -1}
    # same track drifts more on the right → no extra count
    c.update([{"id": 1, "x": 0.9, "y": 0.5}])
    assert c.totals()["out"] == 1


def test_counter_handles_both_directions_and_multiple_tracks():
    c = LineCounter(VLINE)
    c.update([{"id": 1, "x": 0.2, "y": 0.3}, {"id": 2, "x": 0.8, "y": 0.6}])
    c.update([{"id": 1, "x": 0.8, "y": 0.3}, {"id": 2, "x": 0.2, "y": 0.6}])
    assert c.totals() == {"in": 1, "out": 1, "net": 0}


def test_extract_tracks_filters_target_and_computes_center():
    result = {"tracks": [
        {"track_id": 7, "label": "person", "bbox": {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}},
        {"track_id": 8, "label": "car", "bbox": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}},
        {"track_id": 9, "label": "person", "center": [0.9, 0.5]},
    ]}
    tracks = MonitorManager._extract_tracks(result, "person")
    ids = {t["id"] for t in tracks}
    assert ids == {7, 9}                       # car filtered out
    p7 = next(t for t in tracks if t["id"] == 7)
    assert abs(p7["x"] - 0.5) < 1e-9 and abs(p7["y"] - 0.5) < 1e-9  # bbox center


def _runtime(track_frames):
    """track_frames: list of 'tracks' lists, returned one per poll."""
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="door")])
    rt = CameraAgentRuntime(cfg)
    state = {"i": 0}

    async def fake_get_frame(cam, **_kw):
        return b"\xff\xd8\xff"

    async def fake_infer(*, frame_jpeg, extra=None, **kw):
        i = state["i"]; state["i"] += 1
        frame = track_frames[i] if i < len(track_frames) else []
        return {"result": {"tracks": frame}}

    rt.context.get_frame = fake_get_frame
    rt.detection_client.infer = fake_infer
    return rt


def test_crossing_monitor_counts_through_polls():
    # Two polls: person track moves right→left across x=0.5, ending on the
    # positive side → counted as "in" (an entry).
    frames = [
        [{"track_id": 1, "label": "person", "center": [0.8, 0.5]}],
        [{"track_id": 1, "label": "person", "center": [0.2, 0.5]}],
    ]
    rt = _runtime(frames)
    rt.monitors._default_interval = 0.05

    async def go():
        mon = rt.monitors.create(kind="crossing", camera_ids=["cam1"], target="person",
                                 interval_s=0.05, line=[0.5, 0.0, 0.5, 1.0])
        for _ in range(60):
            if rt.monitors.list()[0]["peak"].get("cam1"):
                break
            await asyncio.sleep(0.02)
        m = rt.monitors.list()[0]
        rt.monitors.stop(mon.id)
        assert m["peak"]["cam1"] == 1   # one entry counted

    asyncio.run(go())

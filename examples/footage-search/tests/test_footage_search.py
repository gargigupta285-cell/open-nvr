# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the footage-search store, query parser, and the headline
'red truck' end-to-end path — all without NATS or an LLM."""
from __future__ import annotations

import datetime as _dt

from query import parse_heuristic
from store import FootageStore, Keyframe, keyframe_from_event

NOW = _dt.datetime(2026, 6, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _ts(dt: _dt.datetime) -> float:
    return dt.timestamp()


# ── Query parser ───────────────────────────────────────────────────


def test_parses_label_keyword_and_time():
    qf = parse_heuristic(
        "show me every red truck at the dock yesterday",
        now=NOW, camera_aliases={"dock": "cam-dock"},
    )
    assert "truck" in qf.labels
    assert "red" in qf.keywords
    assert qf.camera_id == "cam-dock"
    # yesterday window
    assert qf.since is not None and qf.until is not None
    y = (NOW - _dt.timedelta(days=1)).date()
    assert _dt.datetime.fromtimestamp(qf.since, _dt.timezone.utc).date() == y


def test_parses_rolling_window():
    qf = parse_heuristic("people in the last 30 minutes", now=NOW)
    assert "person" in qf.labels          # "people" → person alias
    assert qf.since is not None
    assert abs((NOW.timestamp() - qf.since) - 1800) < 2


def test_descriptor_only_query_has_no_labels():
    qf = parse_heuristic("anyone in a yellow jacket today", now=NOW)
    assert qf.labels == []                # no object class named
    assert "yellow" in qf.keywords and "jacket" in qf.keywords
    assert qf.since is not None           # today window


# ── Store + keyframe extraction ────────────────────────────────────


def test_keyframe_from_detection_and_caption_events():
    det_kf = keyframe_from_event({
        "camera_id": "cam-1", "correlation_id": "c1", "adapter": "yolov8",
        "completed_at": "2026-06-14T10:00:00Z",
        "result": {"detections": [{"label": "truck"}, {"label": "person"}]},
    })
    assert det_kf is not None and "truck" in det_kf.labels

    cap_kf = keyframe_from_event({
        "camera_id": "cam-1", "correlation_id": "c1", "adapter": "blip",
        "completed_at": "2026-06-14T10:00:00Z",
        "result": {"caption": "a red truck near a loading dock"},
    })
    assert cap_kf is not None and "red truck" in cap_kf.caption

    # Empty event → nothing to index
    assert keyframe_from_event({"camera_id": "cam-1", "result": {}}) is None


def test_red_truck_end_to_end():
    store = FootageStore(":memory:")
    # The detector indexed a truck; the captioner indexed the color.
    store.add(Keyframe(
        camera_id="cam-dock", ts=_ts(NOW - _dt.timedelta(days=1, hours=2)),
        correlation_id="corr-A", adapter="yolov8",
        labels=["truck", "person"], caption="",
    ))
    store.add(Keyframe(
        camera_id="cam-dock", ts=_ts(NOW - _dt.timedelta(days=1, hours=2)),
        correlation_id="corr-A", adapter="blip",
        labels=[], caption="a red truck parked near a loading dock",
    ))
    # A blue car yesterday — should NOT match "red truck".
    store.add(Keyframe(
        camera_id="cam-dock", ts=_ts(NOW - _dt.timedelta(days=1, hours=1)),
        correlation_id="corr-B", adapter="blip",
        labels=["car"], caption="a blue car",
    ))

    qf = parse_heuristic("red truck at the dock yesterday", now=NOW,
                         camera_aliases={"dock": "cam-dock"})
    results = store.search(
        labels=qf.labels, keywords=qf.keywords,
        since=qf.since, until=qf.until, camera_id=qf.camera_id,
    )
    captions = [r.caption for r in results]
    # The red-truck caption row matches (truck via... actually caption);
    # at least one result, and none of them the blue car.
    assert any("red truck" in c for c in captions)
    assert all("blue car" not in c for c in captions)
    store.close()


def test_time_window_excludes_old_rows():
    store = FootageStore(":memory:")
    store.add(Keyframe("cam-1", _ts(NOW - _dt.timedelta(days=5)), "old", "yolov8",
                       ["truck"], "a truck"))
    store.add(Keyframe("cam-1", _ts(NOW - _dt.timedelta(minutes=10)), "new", "yolov8",
                       ["truck"], "a truck"))
    qf = parse_heuristic("truck in the last 30 minutes", now=NOW)
    results = store.search(labels=qf.labels, since=qf.since, until=qf.until)
    ids = {r.correlation_id for r in results}
    assert ids == {"new"}
    store.close()

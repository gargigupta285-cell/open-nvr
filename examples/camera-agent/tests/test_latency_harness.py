# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the latency harness's pure helpers (percentile / summarize).
The harness itself runs against a live agent and isn't exercised in CI."""
from __future__ import annotations

import importlib.util
import pathlib

_HARNESS = pathlib.Path(__file__).resolve().parents[1] / "tools" / "latency_harness.py"
_spec = importlib.util.spec_from_file_location("latency_harness", _HARNESS)
lh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lh)


def test_percentile_basic():
    vals = [10, 20, 30, 40, 50]
    assert lh.percentile(vals, 0) == 10
    assert lh.percentile(vals, 50) == 30
    assert lh.percentile(vals, 100) == 50
    assert lh.percentile([], 50) == 0.0


def test_percentile_p95_picks_top():
    vals = list(range(1, 101))  # 1..100
    assert lh.percentile(vals, 95) == 95  # nearest-rank


def test_summarize_aggregates_walls_and_phases():
    walls = [100.0, 200.0, 300.0]
    phases = [
        {"stt": 30, "llm": 60, "tts": 10, "total": 100},
        {"stt": 40, "llm": 150, "tts": 10, "total": 200},
        {"stt": 35, "llm": 250, "tts": 15, "total": 300},
    ]
    s = lh.summarize(walls, phases)
    assert s["n"] == 3
    assert s["wall_p50"] == 200.0
    assert s["wall_avg"] == 200.0
    assert s["llm_p50"] == 150
    assert "total_p95" in s


def test_summarize_handles_empty():
    s = lh.summarize([], [])
    assert s["n"] == 0 and s["wall_p50"] == 0.0

#!/usr/bin/env python3
# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Latency / load harness for the camera-agent.

Run this against a LIVE agent (real models) to measure conversational
latency, see the per-phase breakdown (transcode / STT / LLM / TTS), and
quantify how much background polling (watches/alarms) steals from the live
turn — the thing that actually hurts UX.

It can't be run in CI (needs real Whisper/Ollama/Piper + a spoken-question
WAV). The pure helpers (percentile, summarize) are unit-tested.

Usage:
    python tools/latency_harness.py --url http://localhost:9100 \
        --audio question.wav --turns 8 --load-monitors 6 --camera all

`question.wav` should be a real recording of a spoken question (any format
ffmpeg reads). Stdlib only — no extra deps.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100). Empty → 0."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def summarize(walls: list[float], phases: list[dict]) -> dict:
    """Aggregate wall times + per-phase server timings into p50/p95/avg."""
    out = {"n": len(walls),
           "wall_p50": percentile(walls, 50), "wall_p95": percentile(walls, 95),
           "wall_avg": (sum(walls) / len(walls)) if walls else 0.0}
    for key in ("transcode", "stt", "llm", "tts", "total"):
        vals = [float(p.get(key, 0)) for p in phases if key in p]
        if vals:
            out[f"{key}_p50"] = percentile(vals, 50)
            out[f"{key}_p95"] = percentile(vals, 95)
    return out


def _post(url: str, data: bytes | None, ctype: str, timeout: float = 120.0):
    req = urllib.request.Request(url, data=data, method="POST")
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _delete(url: str):
    req = urllib.request.Request(url, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


def converse(base: str, audio: bytes, camera: str) -> tuple[float, dict]:
    t0 = time.perf_counter()
    resp = _post(f"{base}/converse?camera={camera}", audio, "application/octet-stream")
    wall = (time.perf_counter() - t0) * 1000.0
    return wall, (resp.get("timings_ms") or {})


def run_phase(base: str, audio: bytes, camera: str, turns: int) -> dict:
    walls, phases = [], []
    for i in range(turns):
        try:
            wall, timings = converse(base, audio, camera)
        except Exception as exc:  # noqa: BLE001
            print(f"  turn {i + 1}: ERROR {exc}", file=sys.stderr)
            continue
        walls.append(wall)
        phases.append(timings)
        print(f"  turn {i + 1}: wall={wall:.0f}ms  {timings}")
    return summarize(walls, phases)


def add_load(base: str, k: int, camera: str) -> list[int]:
    ids = []
    for i in range(k):
        try:
            r = _post(f"{base}/monitors", json.dumps(
                {"kind": "count", "target": "person", "camera_id": camera}).encode(),
                "application/json")
            for m in (r.get("monitors") or []):
                if m["id"] not in ids:
                    ids.append(m["id"])
        except Exception as exc:  # noqa: BLE001
            print(f"  load monitor {i + 1}: ERROR {exc}", file=sys.stderr)
    return ids


def _print_summary(label: str, s: dict) -> None:
    print(f"\n{label}  (n={s['n']})")
    print(f"  wall   p50={s['wall_p50']:.0f}ms  p95={s['wall_p95']:.0f}ms  avg={s['wall_avg']:.0f}ms")
    for key in ("stt", "llm", "tts", "total"):
        if f"{key}_p50" in s:
            print(f"  {key:<6} p50={s[f'{key}_p50']:.0f}ms  p95={s[f'{key}_p95']:.0f}ms")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:9100")
    ap.add_argument("--audio", required=True, help="WAV/any of a spoken question")
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--load-monitors", type=int, default=6)
    ap.add_argument("--camera", default="all")
    args = ap.parse_args(argv)

    base = args.url.rstrip("/")
    audio = open(args.audio, "rb").read()

    print(f"Baseline: {args.turns} turns against {base} (no background load)…")
    baseline = run_phase(base, audio, args.camera, args.turns)

    print(f"\nAdding {args.load_monitors} background watch(es) on '{args.camera}'…")
    ids = add_load(base, args.load_monitors, args.camera)
    time.sleep(3)
    print(f"Under load: {args.turns} turns…")
    loaded = run_phase(base, audio, args.camera, args.turns)

    print("\nCleaning up background watches…")
    for mid in ids:
        _delete(f"{base}/monitors/{mid}")

    _print_summary("BASELINE", baseline)
    _print_summary("UNDER LOAD", loaded)
    if baseline["wall_p50"]:
        delta = (loaded["wall_p50"] - baseline["wall_p50"]) / baseline["wall_p50"] * 100
        print(f"\nLive-turn p50 latency change under {len(ids)} watches: {delta:+.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

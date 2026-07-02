# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
KAI-C metrics collector — the thin collector from the capabilities &
observability spec §05.

The registry already polls every adapter every 60s for /health +
/capabilities; this module is the "/metrics on that same poll" half.
It has two parts:

* :func:`parse_adapter_metrics` — a small, tolerant Prometheus
  text-format parser. It extracts ONLY the contract metrics KAI-C
  cares about (``adapter_infer_latency_seconds`` histogram,
  ``adapter_infer_total{outcome}``, ``adapter_inflight_requests``,
  ``adapter_queue_depth``) and ignores everything else — unknown
  metrics, malformed lines, HELP/TYPE comments. No new dependencies.

* :class:`MetricsRollup` — a deliberately dumb per-adapter rollup
  store per the spec: a bounded ring buffer of the last
  ``MAX_SAMPLES`` scrape samples (60 samples × the 60s poll ≈ 1h) and
  a fixed window, not a mini-TSDB growing inside KAI-C. Long
  retention stays Prometheus's job — the /metrics format means an
  operator can bolt Prometheus + Grafana on later for free.

The rollup derives, over the window:

* p50/p95/p99 latency in ms — Prometheus-style linear interpolation
  over the histogram bucket deltas (newest − oldest sample; counter
  resets fall back to the newest cumulative values);
* per-outcome counts from ``adapter_infer_total``;
* the latest saturation gauges (``inflight``, ``queue_depth``);
* the fingerprint-change timeline (the registry already detects the
  drift; it records the timestamps here).
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 60 samples on the 60s registry poll ≈ 1 hour of history.
MAX_SAMPLES: int = 60
WINDOW_SECONDS: int = 3600
MAX_FINGERPRINT_CHANGES: int = 60

_HISTOGRAM = "adapter_infer_latency_seconds"
_OUTCOMES = "adapter_infer_total"
_INFLIGHT = "adapter_inflight_requests"
_QUEUE_DEPTH = "adapter_queue_depth"


# ── Prometheus text parsing ────────────────────────────────────────


def _parse_labels(raw: str) -> dict[str, str]:
    """Parse a Prometheus label body (``key="value",...``) tolerantly.

    Handles quoted values containing commas and escaped quotes. Any
    malformed remainder is skipped rather than raised.
    """
    labels: dict[str, str] = {}
    i = 0
    n = len(raw)
    while i < n:
        eq = raw.find("=", i)
        if eq < 0:
            break
        key = raw[i:eq].strip().strip(",").strip()
        j = eq + 1
        # Skip whitespace before the opening quote.
        while j < n and raw[j] in " \t":
            j += 1
        if j >= n or raw[j] != '"':
            # Unquoted value — not valid Prometheus, skip to next comma.
            nxt = raw.find(",", j)
            if nxt < 0:
                break
            i = nxt + 1
            continue
        j += 1
        value_chars: list[str] = []
        while j < n:
            ch = raw[j]
            if ch == "\\" and j + 1 < n:
                value_chars.append(raw[j + 1])
                j += 2
                continue
            if ch == '"':
                break
            value_chars.append(ch)
            j += 1
        if key:
            labels[key] = "".join(value_chars)
        # Move past closing quote and any trailing comma.
        j += 1
        while j < n and raw[j] in ", \t":
            j += 1
        i = j
    return labels


def _split_metric_line(line: str) -> tuple[str, dict[str, str], float] | None:
    """Split one exposition line into (name, labels, value).

    Returns ``None`` for comments, blanks, and anything malformed —
    the parser is deliberately lenient (spec §05: collect what's
    there, never crash the poll on a weird adapter).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "{" in line:
        brace_open = line.index("{")
        brace_close = line.rfind("}")
        if brace_close < brace_open:
            return None
        name = line[:brace_open].strip()
        labels = _parse_labels(line[brace_open + 1 : brace_close])
        rest = line[brace_close + 1 :].strip()
    else:
        parts = line.split()
        if len(parts) < 2:
            return None
        name = parts[0]
        labels = {}
        rest = " ".join(parts[1:])
    if not name:
        return None
    # ``rest`` is "<value> [timestamp]" — take the first token.
    value_token = rest.split()[0] if rest.split() else ""
    try:
        value = float(value_token)
    except ValueError:
        return None
    return name, labels, value


@dataclass
class MetricsSample:
    """One /metrics scrape, reduced to the contract signals."""

    scraped_at: float
    # Cumulative histogram buckets: upper bound (seconds) → count.
    # +Inf is represented as math.inf.
    latency_buckets: dict[float, float] = field(default_factory=dict)
    latency_sum: float | None = None
    latency_count: float | None = None
    # Cumulative per-outcome counters: outcome label → count.
    outcomes: dict[str, float] = field(default_factory=dict)
    # Point-in-time gauges.
    inflight: int | None = None
    queue_depth: int | None = None


def parse_adapter_metrics(text: str, *, scraped_at: float | None = None) -> MetricsSample:
    """Parse Prometheus exposition text into a :class:`MetricsSample`.

    Only the four contract metric families are read; everything else
    (including malformed lines) is ignored.
    """
    sample = MetricsSample(scraped_at=scraped_at if scraped_at is not None else time.time())
    for line in text.splitlines():
        parsed = _split_metric_line(line)
        if parsed is None:
            continue
        name, labels, value = parsed
        if name == f"{_HISTOGRAM}_bucket":
            le_raw = labels.get("le")
            if le_raw is None:
                continue
            try:
                le = math.inf if le_raw in ("+Inf", "Inf", "inf") else float(le_raw)
            except ValueError:
                continue
            sample.latency_buckets[le] = value
        elif name == f"{_HISTOGRAM}_sum":
            sample.latency_sum = value
        elif name == f"{_HISTOGRAM}_count":
            sample.latency_count = value
        elif name == _OUTCOMES:
            outcome = labels.get("outcome")
            if outcome:
                sample.outcomes[outcome] = sample.outcomes.get(outcome, 0.0) + value
        elif name == _INFLIGHT:
            sample.inflight = int(value)
        elif name == _QUEUE_DEPTH:
            sample.queue_depth = int(value)
    return sample


# ── Percentile math ────────────────────────────────────────────────


def histogram_quantile(buckets: dict[float, float], quantile: float) -> float | None:
    """Prometheus-style ``histogram_quantile`` over cumulative buckets.

    ``buckets`` maps upper bound (seconds; +Inf as math.inf) →
    cumulative count. Linear interpolation inside the target bucket;
    the +Inf bucket clamps to the highest finite bound (same behaviour
    as Prometheus). Returns ``None`` when there's no data.
    """
    if not buckets:
        return None
    bounds = sorted(buckets)
    total = buckets[bounds[-1]]
    if total <= 0:
        return None
    rank = quantile * total
    prev_bound = 0.0
    prev_count = 0.0
    finite = [b for b in bounds if not math.isinf(b)]
    for bound in bounds:
        count = buckets[bound]
        if count >= rank:
            if math.isinf(bound):
                # Observation beyond the last finite bucket — clamp.
                return finite[-1] if finite else None
            if count == prev_count:
                return bound
            return prev_bound + (bound - prev_bound) * (rank - prev_count) / (count - prev_count)
        prev_bound = 0.0 if math.isinf(bound) else bound
        prev_count = count
    return finite[-1] if finite else None


def _bucket_delta(newest: dict[float, float], oldest: dict[float, float]) -> dict[float, float]:
    """Windowed histogram: newest − oldest cumulative buckets.

    A negative delta anywhere means the adapter's counters reset
    (restart) inside the window — fall back to the newest cumulative
    values, which is exactly the "since restart" window. Deliberately
    dumb, per the spec.
    """
    delta: dict[float, float] = {}
    for bound, count in newest.items():
        d = count - oldest.get(bound, 0.0)
        if d < 0:
            return dict(newest)
        delta[bound] = d
    return delta


def _outcome_delta(newest: dict[str, float], oldest: dict[str, float]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome, count in newest.items():
        d = count - oldest.get(outcome, 0.0)
        if d < 0:  # counter reset — fall back to cumulative
            return {k: int(v) for k, v in newest.items()}
        counts[outcome] = int(d)
    return counts


# ── Rollup store ───────────────────────────────────────────────────


@dataclass
class _AdapterSeries:
    samples: deque[MetricsSample] = field(
        default_factory=lambda: deque(maxlen=MAX_SAMPLES)
    )
    fingerprint_changes: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_FINGERPRINT_CHANGES)
    )


class MetricsRollup:
    """Bounded in-memory rollup, one ring buffer per adapter.

    Thread-safe the same way the registry is: a plain lock around the
    state dict so synchronous readers (the FastAPI handler) and the
    async poll loop don't race.
    """

    def __init__(self) -> None:
        self._series: dict[str, _AdapterSeries] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, adapter: str) -> _AdapterSeries:
        series = self._series.get(adapter)
        if series is None:
            series = _AdapterSeries()
            self._series[adapter] = series
        return series

    def record_sample(self, adapter: str, sample: MetricsSample) -> None:
        with self._lock:
            self._get_or_create(adapter).samples.append(sample)

    def record_fingerprint_change(self, adapter: str, *, at: float | None = None) -> None:
        ts = datetime.fromtimestamp(
            at if at is not None else time.time(), tz=timezone.utc
        ).isoformat()
        with self._lock:
            self._get_or_create(adapter).fingerprint_changes.append(ts)

    def forget(self, adapter: str) -> None:
        """Drop an adapter's series (called on deregistration) so the
        store stays bounded by the number of LIVE adapters."""
        with self._lock:
            self._series.pop(adapter, None)

    def snapshot(self, adapter: str) -> dict[str, Any]:
        """The §05 rollup for one adapter — the shape
        ``GET /api/v1/adapters/{name}/metrics`` serves. All-null fields
        when nothing has been scraped yet."""
        with self._lock:
            series = self._series.get(adapter)
            samples = list(series.samples) if series else []
            fingerprint_changes = list(series.fingerprint_changes) if series else []

        latency_ms: dict[str, float | None] = {"p50": None, "p95": None, "p99": None}
        outcomes: dict[str, int] = {}
        inflight: int | None = None
        queue_depth: int | None = None

        if samples:
            newest = samples[-1]
            oldest = samples[0]
            if len(samples) > 1:
                buckets = _bucket_delta(newest.latency_buckets, oldest.latency_buckets)
                outcomes = _outcome_delta(newest.outcomes, oldest.outcomes)
            else:
                buckets = dict(newest.latency_buckets)
                outcomes = {k: int(v) for k, v in newest.outcomes.items()}
            for key, q in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
                seconds = histogram_quantile(buckets, q)
                latency_ms[key] = round(seconds * 1000.0, 3) if seconds is not None else None
            inflight = newest.inflight
            queue_depth = newest.queue_depth

        return {
            "adapter": adapter,
            "window_s": WINDOW_SECONDS,
            "latency_ms": latency_ms,
            "outcomes": outcomes,
            "inflight": inflight,
            "queue_depth": queue_depth,
            "fingerprint_changes": fingerprint_changes,
            "samples": len(samples),
        }

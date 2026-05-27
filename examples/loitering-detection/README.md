# Loitering-detection example app

The second first-party OpenNVR monitoring app, paired with `intrusion-detection` to demonstrate **two complementary monitoring patterns**: drive-vs-subscribe.

| Pattern | This app | `intrusion-detection` |
|---|---|---|
| **How it gets results** | Subscribes to KAI-C's NATS broadcast | Drives KAI-C directly (HTTP poll or WS stream) |
| **GPU cost on top of other apps** | Zero — rides existing inference | Pays per-app inference cost |
| **Detects** | Watched-label entity loitering in zone > threshold | Watched-label entity present in zone during restricted hours |
| **Predicate** | Dwell time (state machine) | Point-in-polygon × time window |
| **Use when** | You want to consume what another app drove | You want to control which cameras get inferenced |

In a real deployment you'd run BOTH: `intrusion-detection` polls/streams YOLOv8 on cameras you care about, and one or more `loitering-detection` processes (or other subscribers — your dashboard, Slack bot, etc.) ride the same inference stream without doubling adapter GPU load.

## What it does

```
KAI-C  ───publishes──→  NATS broker  ───broadcasts──→  loitering-detection
                       (opennvr.inference.*)            (this app)
                                                              │
                                                              ▼
                                                  ┌──────────────────────┐
                                                  │ filter watch_labels  │
                                                  │ → bbox_center        │
                                                  │ → zone.contains?     │
                                                  │ → update dwell state │
                                                  │   per (camera, label)│
                                                  └──────────┬───────────┘
                                                             │ dwell ≥ threshold
                                                             ▼
                                                  ┌──────────────────────┐
                                                  │  AlertDispatcher     │  stdout (always)
                                                  │                      │  + webhook (optional)
                                                  │                      │  + NATS (optional)
                                                  └──────────────────────┘
```

Each alert carries:
- `correlation_id` — joins back through KAI-C's audit log
- `evidence.adapter` / `adapter_version` — which model produced the detection
- `evidence.model_fingerprint` — §11.3 drift-detection verification
- `evidence.dwell_seconds` / `threshold_seconds` — how long the entity was there

**Alert fan-out via NATS**: set `nats_alerts_url` in `config.yml` to publish each alert as JSON onto `opennvr.alerts.app.loitering-detection.{camera_id}`. Downstream consumers — the operator UI inbox, SIEM bridges, Slack bots — subscribe to wildcards like `opennvr.alerts.>` and fan out from one publish. See `examples/alerts-subscriber/` for the canonical consumer template and the [§11.5.1 contract entry](../../docs/AI_ADAPTER_CONTRACT.md) for the full subject scheme.

## State machine

For each `(camera_id, watched_label)` pair:

```
                    ┌─────────────────────────────────────────────────┐
                    │                                                 │
                    ▼                                                 │
              [no state] ──first in-zone frame──→ [tracking]          │
                                                       │              │
                                                       │ in-zone      │
                                                       │ frame        │
                                                       │ (refresh     │
                                                       │  last_seen)  │
                                                       │              │
                                                       ▼              │
                                                  dwell≥threshold     │
                                                       │              │
                                                       │ fire alert,  │
                                                       │ mark alerted │
                                                       │              │
                                                       ▼              │
                                                  [alerted]           │
                                                       │              │
                                                       │ absent       │
                                                       │ frames for   │
                                                       │ > grace      │
                                                       │ period       │
                                                       │              │
                                                       ▼              │
                                                  [GC]────────────────┘
```

**Grace period semantics**: the state for a (camera, label) pair is reset only when an event ARRIVES for that camera in which the label is absent AND the gap since the label was last seen exceeds `grace_period_seconds`. So:

- **Gap-based**, not "cumulative ticks": the comparison is `event_ts - state.last_seen > grace_period`. Whether 2 or 20 absent frames span that gap is irrelevant.
- **Driven by RECEIVED events**, not wall-clock: if no events arrive at all (broker down, adapter idle), no expiry fires. We treat absence-of-events as "we have no signal", not "absence."
- **Absence has to be observed**: an event with the label NOT in the zone is what counts. An event that contains the label keeps refreshing `last_seen`.

This is what makes `5s` grace work fine with 1 fps inference: a few missed detections (brief occlusion, false negatives) are absorbed; an actual departure with the person being absent in 5+ consecutive frames resets cleanly.

## Quick start

```bash
cd examples/loitering-detection
uv pip install -e ".[dev]"          # or: pip install -e ".[dev]"
cp config.example.yml config.yml
# Edit config.yml: nats_token, watch_labels, threshold_seconds, cameras
python loitering_detection.py --config config.yml
```

Output:

```
2026-05-21T14:32:18 INFO loitering-detection: loitering-detection started: 2 cameras, watch=['person'], threshold=60.0s, grace=5.0s, subject='opennvr.inference.>'
ALERT [MEDIUM] 2026-05-21T14:33:24 camera=cam-back-shed title='Person loitering in zone 'shed-perimeter'' correlation_id=a4f1b… alert_id=alrt_e3d9af
```

## Operate

| Mode | Command |
|---|---|
| Daemon (production) | `python loitering_detection.py --config config.yml` |
| One event then exit (smoke test) | `python loitering_detection.py --config config.yml --once` |
| Verbose | `python loitering_detection.py --config config.yml --log-level DEBUG` |

`SIGINT` / `SIGTERM` drains the NATS connection and exits cleanly.

## Tests

```bash
PYTHONPATH=. pytest tests/
```

15 tests. Coverage: state machine (single-frame, continuous-presence, threshold-crossing, post-alert quiescence, post-grace reset, per-label independence), config validation, correlation_id + fingerprint passthrough, defensive parsing of malformed events.

## Layout

```
examples/loitering-detection/
├── loitering_detection.py   Main loop + LoiteringDetector state machine + CLI
├── zone.py                  Point-in-polygon (copied from intrusion-detection)
├── alerts.py                Alert dataclass + stdout/webhook channels (copied)
├── config.example.yml       Sample config
├── pyproject.toml           Minimal deps (nats-py, httpx, PyYAML)
├── README.md                you are here
└── tests/
    └── test_loitering_detection.py  (15 tests)
```

## Why this is a template

If you want to build a NEW monitoring app for OpenNVR — package detection, PPE compliance, fall detection, fire/smoke, abandoned object, queue-length monitoring — pick a starting point:

| Template | Start here when… |
|---|---|
| `intrusion-detection/` | Your app needs to control inference cadence (poll rate, camera selection, restricted-hours logic). You don't mind paying KAI-C call cost per app. |
| `loitering-detection/` | You want zero adapter-side cost. Your business logic is a function of inference results others are already driving. |

Both share `zone.py` + `alerts.py` verbatim (copy-as-template, not shared library — community contributors have flagged dependency hell from shared util packages). Override `handle_event(event)` (loitering-style) or `step(camera)` (intrusion-style) for your predicate.

## What's NOT in v1

- **Per-track tracking** — current state is per-`(camera, label)`. A person leaving while a different one arrives within the grace period is counted as one continuous dwell. If your adapter emits `track_id` on each detection, a follow-up can swap to per-`(camera, label, track_id)` state.
- **Per-camera threshold override** — single `threshold_seconds` applies to all watched cameras. Future config could allow per-camera overrides for "loading bay tolerates 5min" vs "doorway tolerates 30s."
- **Dwell-extension alerts** — fires once at threshold-crossing; doesn't re-fire if the dwell continues to extend (e.g., separate alerts at threshold, 2× threshold, 3× threshold). Out of scope for v1; subclasses can override `handle_event`.
- **Replay** — KAI-C's NATS broker is fire-and-forget. Events fired while no subscriber is listening are lost. Durable consumers and replay are planned for a future event-store release.

# Inference-listener example app

The second first-party OpenNVR example app, paired with `intrusion-detection` to demonstrate the **NATS event bus**. Subscribes to KAI-C's NATS broadcast surface (`opennvr.inference.{adapter}.{camera_id}.completed`) and prints every event to stdout.

This is the **subscriber-side template** for monitoring apps that prefer "receive results that someone else drove" over "drive inference myself." When one inference fans out to N consumers, the adapter's GPU does the work once instead of N times.

## When to use this pattern (vs intrusion-detection)

| Pattern | Drives inference? | Has its own camera? | Use when… |
|--|--|--|--|
| `intrusion-detection` (HTTP polling or WS streaming) | Yes — POSTs frames to KAI-C | Yes | You want to control which cameras get analyzed and how often |
| `inference-listener` (this example) | No — subscribes to NATS | No | You're a downstream consumer (dashboard, alert router, metrics aggregator, SIEM bridge) of results that other apps already drove |

In a real deployment you'd typically run BOTH: intrusion-detection drives YOLOv8 on the cameras you care about; one or more inference-listeners ride alongside (a Slack notifier, a frame counter, a "store every detection in Elasticsearch" forwarder) without each making its own KAI-C call.

## What it does

```
KAI-C  ───publishes──→  NATS broker  ───broadcasts──→  N subscribers
                       (opennvr.inference.*)
                                    │
                                    ├──→ inference-listener (this app)
                                    ├──→ your dashboard
                                    ├──→ your Slack bot
                                    └──→ your SIEM forwarder
```

Every `InferenceCompletedEvent` carries:
- `correlation_id` — joins back to KAI-C's audit log
- `adapter`, `adapter_version`
- `camera_id` (or `"unknown"`)
- `model_name`, `model_version`, `model_fingerprint` — verify against `/capabilities` for §11.3 drift detection
- `inference_ms`
- `result` — the §5.x task-specific result body (DetectionResult, AsrResult, …)

See `kai-c/kai_c/events.py` for the full Pydantic schema.

## Quick start

```bash
cd examples/inference-listener
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
cp config.example.yml config.yml
# Edit config.yml — at minimum, set nats_token to your INTERNAL_API_KEY
python inference_listener.py --config config.yml
```

You'll see one line per inference event:

```
2026-05-21T14:32:18 INFO inference-listener: connected to nats://nats:4222, subscribing to 'opennvr.inference.>'
INFERENCE [yolov8/cam-front-gate] correlation_id=a4f1b… inference_ms=38 detections=2 [person, car]
INFERENCE [yolov8/cam-back-shed] correlation_id=8c2d3… inference_ms=42 detections=0
```

## Operate

| Mode | Command |
|---|---|
| Daemon (production) | `python inference_listener.py --config config.yml` |
| Receive one event then exit (testing) | `python inference_listener.py --config config.yml --once` |
| Verbose | `python inference_listener.py --config config.yml --log-level DEBUG` |

`SIGINT` / `SIGTERM` drains the NATS connection and exits cleanly.

## Why this is a template

If you want to build a downstream consumer for KAI-C's broadcast surface — a dashboard backend, a Slack/Discord bot, an Elasticsearch forwarder, a Prometheus scraper that counts detections per camera — copy this directory and override `handle_event(subject, payload)` in `inference_listener.py`. Everything else (NATS connect, auth, subject filtering, signal handling, drain-on-shutdown) is the same template.

For richer logic (per-camera state, alert deduplication, fan-out to multiple sinks), structure your subclass with whatever architecture fits — this example deliberately stays simple so the wire-level pattern is visible.

## Layout

```
examples/inference-listener/
├── inference_listener.py    Main loop + InferenceListener class + CLI
├── config.example.yml       Sample config with every option
├── pyproject.toml           Minimal deps (nats-py, PyYAML)
├── README.md                you are here
└── tests/
    └── test_inference_listener.py  Smoke tests
```

## What's NOT in v1

- **Event replay / durability** — KAI-C's NATS deployment is fire-and-forget (no JetStream). Events fired while no subscriber is listening are lost. Durable consumer / replay lands with a separate event-store slice.
- **Per-subscriber permissions** — every subscriber that knows the token can read every subject. Per-subject NATS accounts land when operators need them.
- **Filter expressions beyond subject wildcards** — you can subscribe to `opennvr.inference.yolov8.cam-front.>` but not to "events whose result.detections[].label includes 'person'." Filter in your `handle_event` for now.

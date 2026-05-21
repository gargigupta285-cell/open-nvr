# Alerts-subscriber example app

The fourth first-party OpenNVR example. Subscribes to OpenNVR's alert fan-out NATS surface (§B1-alerts) and prints / forwards each fired alert.

This is the **alert-side** companion to `inference-listener`:

| | `inference-listener` | `alerts-subscriber` (this) |
|---|---|---|
| Subscribes to | `opennvr.inference.*` (KAI-C inference results) | `opennvr.alerts.*` (app-emitted alerts) |
| Use it for | Dashboards, counters, custom-predicate detection | UI inbox, SIEM, Slack/PagerDuty, audit log |
| Payload | `InferenceCompletedEvent` (per-inference) | `§11.5 Alert` envelope (per-alert) |

## What it does

```
intrusion-detection ──┐
loitering-detection ──┼──publishes──→  NATS broker  ───broadcasts──→  alerts-subscriber
   (your app) ───────┘                opennvr.alerts.>                    (this app)
                                                                              │
                                                                              ▼
                                                                  ┌──────────────────────┐
                                                                  │ print to stdout      │
                                                                  │ → optional webhook   │
                                                                  │ → (your subclass)    │
                                                                  └──────────────────────┘
```

The publishing-side change lives in `intrusion-detection/alerts.py` and `loitering-detection/alerts.py` — see the `NatsAlertChannel` class. This subscriber just consumes whatever those (and any future first-party or community apps) emit.

## Subject scheme

Mirrors the §11.5 Alert `source` block:

```
opennvr.alerts.{source.kind}.{source.name}.{camera_id}
```

Examples:

```
opennvr.alerts.app.intrusion-detection.cam-front-door
opennvr.alerts.app.loitering-detection.cam-back-shed
opennvr.alerts.adapter.yolov8.cam-X         (future, adapter-emitted)
opennvr.alerts.kai-c.policy-violation.cam-X (future, KAI-C-emitted)
```

Useful subscription patterns:

| Pattern | Catches |
|---|---|
| `opennvr.alerts.>` | Everything (default) |
| `opennvr.alerts.app.>` | All app-emitted alerts |
| `opennvr.alerts.app.intrusion-detection.>` | One app's alerts |
| `opennvr.alerts.*.*.cam-front-door` | All alerts about one camera |

## Quick start

```bash
cd examples/alerts-subscriber
uv pip install -e ".[dev]"          # or: pip install -e ".[dev]"
cp config.example.yml config.yml
# Edit config.yml: nats_url, nats_token, optional webhook_url
python alerts_subscriber.py --config config.yml
```

Output (with intrusion-detection or loitering-detection publishing on the same bus):

```
2026-05-21T14:32:18 INFO alerts-subscriber: connected to nats://nats:4222, subscribing to 'opennvr.alerts.>'
ALERT [MEDIUM] 2026-05-21T14:33:24 subject=opennvr.alerts.app.loitering-detection.cam-back-shed camera=cam-back-shed title='Person loitering in zone …' source=loitering-detection correlation_id=a4f1b… alert_id=alrt_e3d9af
ALERT [HIGH]   2026-05-21T14:35:02 subject=opennvr.alerts.app.intrusion-detection.cam-front-door  camera=cam-front-door title='Person in restricted zone …'    source=intrusion-detection correlation_id=b8e2c… alert_id=alrt_a1c2f3
```

## Operate

| Mode | Command |
|---|---|
| Daemon (production) | `python alerts_subscriber.py --config config.yml` |
| One alert then exit (smoke test) | `python alerts_subscriber.py --config config.yml --once` |
| Verbose | `python alerts_subscriber.py --config config.yml --log-level DEBUG` |

`SIGINT` / `SIGTERM` drains the NATS connection and exits cleanly.

## Tests

```bash
PYTHONPATH=. pytest tests/
```

Coverage: config validation, default subject pattern, message parsing (including malformed JSON), webhook forward happy-path + failure-path, custom subject pattern, `--once` exit.

## Layout

```
examples/alerts-subscriber/
├── alerts_subscriber.py    Main loop + AlertConsumer + CLI
├── config.example.yml      Sample config
├── pyproject.toml          Minimal deps (nats-py, httpx, PyYAML)
├── README.md               you are here
└── tests/
    └── test_alerts_subscriber.py
```

## Extending — write your own consumer

The default `handle_alert(subject, alert_dict)` prints + optionally webhooks. To plug in real consumer logic (DB insert, SIEM forward, custom dispatch), subclass `AlertConsumer`:

```python
from alerts_subscriber import AlertConsumer, AppConfig, load_config

class MyDashboardConsumer(AlertConsumer):
    def __init__(self, config: AppConfig, db_handle) -> None:
        super().__init__(config)
        self._db = db_handle

    def handle_alert(self, subject: str, alert: dict) -> None:
        super().handle_alert(subject, alert)   # keep stdout + webhook
        self._db.alerts.insert_one(alert)      # add your storage
```

That's the entire extension surface. The NATS connection lifecycle, signal handling, drain-on-shutdown, and malformed-message defensive parsing are all reused.

## What's NOT in v1

- **Durability** — the broker is fire-and-forget. Alerts fired while no subscriber is listening are lost. JetStream durable consumers + replay land with B2.
- **Filtering by severity in the subscription** — the subject scheme is `(kind, name, camera)` so severity is in the payload, not the subject. If you need server-side severity routing, filter on `alert["severity"]` in `handle_alert` and dispatch from there.
- **Backpressure** — high alert volumes will buffer in nats-py's pending-msgs queue. If you push to a downstream system that backs up, your subscriber memory grows. The v1 fix is "make `handle_alert` fast and async-buffer downstream calls"; a future slice can add explicit max-pending tuning.

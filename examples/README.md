# Examples gallery

Every example here is a **copy-as-template** starting point — minimal, readable,
and opinionated. Pick one that's close to what you want to build, copy the
folder, and edit the predicate.

The five shipped examples cover two orthogonal axes of the OpenNVR pipeline:
*driving* inference vs *subscribing* to it, and *inference events* vs *alerts*.

```
                      Drives inference?
                      ──────────────────
                       Yes              No
                  ┌───────────────┬──────────────────────┐
  Subscribes to   │               │ inference-listener   │
  inference       │               │ loitering-detection  │
  events          │               │                      │
                  ├───────────────┼──────────────────────┤
  Subscribes to   │ intrusion-    │ alerts-subscriber    │
  alert envelopes │ detection¹    │                      │
                  └───────────────┴──────────────────────┘

  ¹ intrusion-detection drives KAI-C directly AND emits its own alerts;
    pick this one if you want to learn the full producer side first.
```

---

## ✅ Shipped — runnable today

### [`intrusion-detection/`](intrusion-detection)

**Detect people or vehicles entering restricted zones during restricted hours,
fire an alert.** The canonical producer-side template — drives KAI-C, walks
the audit chain end to end, demonstrates both HTTP-polling and WebSocket-
streaming transports.

| | |
|---|---|
| Pattern | Drives inference (HTTP poll or WS stream) → fires alerts |
| Adapter | YOLOv8 (object detection) |
| Difficulty | ⭐ beginner |
| Best for learning | The full producer pipeline from camera → KAI-C → alert |
| Tests | 87 |

```bash
cd examples/intrusion-detection && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs, zones, hours
python intrusion_detection.py --config config.yml
```

---

### [`loitering-detection/`](loitering-detection)

**Detect people who linger in a zone longer than a threshold.** The canonical
subscriber-side template — rides someone else's NATS inference stream and
adds a dwell-time state machine. Zero adapter GPU cost on top of the producer
already running.

| | |
|---|---|
| Pattern | Subscribes to NATS inference events → fires alerts |
| Adapter | (rides upstream's YOLOv8 — no direct adapter call) |
| Difficulty | ⭐⭐ intermediate |
| Best for learning | NATS subscription, state machines, multi-app deployments |
| Tests | 50 |

```bash
cd examples/loitering-detection && uv sync --extra dev
cp config.example.yml config.yml      # edit threshold seconds, zones
python loitering_detection.py --config config.yml
```

---

### [`inference-listener/`](inference-listener)

**Minimal NATS subscriber template.** Subscribes to
`opennvr.inference.{adapter}.{camera_id}.completed` and prints every event to
stdout. Read this first if you're building a custom dashboard, metrics
aggregator, or SIEM bridge.

| | |
|---|---|
| Pattern | Subscribes to NATS inference events → consumes them |
| Adapter | (none) |
| Difficulty | ⭐ beginner |
| Best for learning | The smallest-possible OpenNVR subscriber app |
| Tests | 7 |

```bash
cd examples/inference-listener && uv sync --extra dev
cp config.example.yml config.yml
python inference_listener.py --config config.yml
```

---

### [`alerts-subscriber/`](alerts-subscriber)

**Fan out alerts to webhooks, logs, or your own tooling.** Subscribes to
`opennvr.alerts.*` and forwards each fired alert. Pair with
`intrusion-detection` or `loitering-detection` (both can publish to NATS)
to build an alert router, SIEM forwarder, or Slack/Discord/PagerDuty bridge.

| | |
|---|---|
| Pattern | Subscribes to NATS alert envelopes → routes them |
| Adapter | (none) |
| Difficulty | ⭐ beginner |
| Best for learning | The alerts-subscriber side of the event bus |
| Tests | 13 |

```bash
cd examples/alerts-subscriber && uv sync --extra dev
cp config.example.yml config.yml
python alerts_subscriber.py --config config.yml
```

---

### [`license-plate-recognition/`](license-plate-recognition)

**Detect every vehicle on your driveway, log every plate.** The first
example to drive a two-stage inference chain through KAI-C — YOLOv8 for
vehicle detection, then the `fast-plate-ocr` adapter for OCR on each
vehicle crop. Ships with watchlist (allowlist / denylist) severity
routing and a Pillow-based cropping helper.

| | |
|---|---|
| Pattern | Drives YOLOv8 + fast-plate-ocr chain → fires alerts |
| Adapters | YOLOv8 + fast-plate-ocr (Apache-2.0, ONNX, CPU-only, plate-specific) |
| Difficulty | ⭐⭐ intermediate |
| Best for learning | Chaining multiple adapters under one correlation ID |
| Tests | 29 |

```bash
cd examples/license-plate-recognition && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs + watchlists
python license_plate_recognition.py --config config.yml
```

---

## 🚧 Planned — coming in v0.1

These three are the next round of viral, demo-friendly examples. Each is
designed to be the kind of thing that earns a homelab YouTube review or
a `/r/homelab` thread. **Want to help build one?** Open a discussion and
we'll match scope to interest.

### `smart-doorbell/`

**Know who's at the door — family, friend, or stranger.** InsightFace
recognition (already shipped) + Telegram / ntfy / webhook delivery with a
snapshot. One-shot REST enrollment, no desktop app required.

| | |
|---|---|
| Pattern | Drives InsightFace adapter → routes alerts to messaging |
| Adapters | InsightFace (face recognition) |
| Difficulty | ⭐⭐ intermediate |
| Why it's interesting | Recognition + delivery in one tutorial; pure REST enrollment |

### `package-delivery/`

**Alert me when a package arrives — and when it leaves.** YOLOv8 on a porch
ROI with a state machine that distinguishes arrive → linger → disappear, so
"package picked up by owner" and "package taken by stranger" are different
events.

| | |
|---|---|
| Pattern | Drives YOLOv8 → custom state machine → fires alerts |
| Adapters | YOLOv8 |
| Difficulty | ⭐⭐ intermediate |
| Why it's interesting | Forks easily into other duration-based predicates |

### `home-assistant-relay/`

**Every OpenNVR alert in your Home Assistant dashboard.** NATS subscriber
that publishes MQTT to your HA broker (or hits HA's REST API directly), with
device_class + entity_id mapping. Drops into existing HA dashboards in
minutes.

| | |
|---|---|
| Pattern | Subscribes to NATS alerts → publishes MQTT / HA REST |
| Adapters | (none) |
| Difficulty | ⭐⭐ intermediate |
| Why it's interesting | Massive distribution multiplier — every HA user is a candidate |

---

## 💡 More on the roadmap

Beyond the four planned examples, these are explicitly welcome contributions
(see also the [adapter wishlist](https://github.com/open-nvr/ai-adapter#-adapters-wed-love-to-see)):

| Category | Idea |
|---|---|
| Safety | Fall detection, fire/smoke detection, PPE compliance (hard hat / vest / mask) |
| Analytics | Crowd density, queue length, dwell-time heatmaps, vehicle classification |
| Audio | Glass-break detection, gunshot detection, aggression detection |
| Conversational | "What's at the gate?" voice agent (ASR + TTS + LLM) |
| Wildlife | Pet / livestock detection, bird-species ID |
| Forensic | "Show me everyone in red between 2 and 4 pm" semantic search |

Open a [discussion](https://github.com/open-nvr/open-nvr/discussions) before
you start coding — we'll help scope it and (if it fits the first-party tier)
provide review.

---

## 🛠️ How an example is structured

Every shipped example follows the same layout so you can read one and know
where everything lives in the others:

```
examples/<example-name>/
├── README.md             # problem + screenshots + how to run
├── <example>.py          # main loop + the predicate class + CLI
├── alerts.py             # Alert dataclass + dispatch channels (stdout / webhook / NATS)
├── config.example.yml    # what an operator configures
├── pyproject.toml        # minimal deps (httpx, PyYAML, nats-py, websockets)
└── tests/                # focused, readable-in-5-minutes test suite
```

`alerts.py` and the config-loading shape are deliberately consistent across
all four shipped examples so you can copy one folder, rename `<example>.py`,
and replace the predicate with your domain logic — everything else (alert
routing, correlation IDs, NATS publishing, SIGINT handling) is the template.

---

## 🤝 Contributing your own example

The fastest path to a first-party example slot:

1. Open a [discussion](https://github.com/open-nvr/open-nvr/discussions) with
   your idea, the camera setup you'll demo on, and the adapter(s) you'll
   chain.
2. Fork, branch, and copy one of the four shipped examples as your starting
   template.
3. Replace the predicate (`zone.contains?`, the dwell-time state machine,
   etc.) with your domain logic.
4. Keep the file layout, the config shape, the alert dispatcher pattern, and
   the test surface roughly the same so future readers see one shape across
   the gallery.
5. Open a PR. We'll review for: clarity, test coverage of the predicate,
   honest documentation of what the example does NOT yet do, and consistency
   with the rest of the gallery.

Examples are first-class community contribution surface. If yours gets in,
your name goes on it.

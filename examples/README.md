# Examples gallery

Every example here is a **copy-as-template** starting point — minimal, readable,
and opinionated. Pick one that's close to what you want to build, copy the
folder, and edit the predicate.

The eight shipped examples cover two orthogonal axes of the OpenNVR pipeline:
*driving* inference vs *subscribing* to it, and *inference events* vs *alerts*.

```
                      Drives inference?
                      ──────────────────
                       Yes                            No
                  ┌─────────────────────────┬──────────────────────┐
  Subscribes to   │                         │ inference-listener   │
  inference       │                         │ loitering-detection  │
  events          │                         │                      │
                  ├─────────────────────────┼──────────────────────┤
  Subscribes to   │ intrusion-detection¹    │ alerts-subscriber    │
  alert envelopes │ license-plate-          │ camera-agent²        │
                  │   recognition¹          │                      │
                  │ smart-doorbell¹         │                      │
                  │ package-delivery¹       │                      │
                  └─────────────────────────┴──────────────────────┘

  ¹ These four drive KAI-C directly AND emit their own alerts —
    they're the full producer-side templates. Start here if you want
    to learn the producer flow first.
  ² camera-agent sits in the "subscribes to inference" row because
    it's reactive — the user talks to it. It runs tools (which DO
    drive KAI-C) on demand, but the example is shaped as a
    conversation, not a polling daemon.
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
| Tests | 34 |

```bash
cd examples/license-plate-recognition && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs + watchlists
python license_plate_recognition.py --config config.yml
```

---

### [`smart-doorbell/`](smart-doorbell)

**Know who's at the door — family, friend, or stranger.** Drives the
InsightFace adapter via KAI-C; classifies each detected face into
family / known / unknown and routes severity accordingly. Unknown-face
alerts include a base64 JPEG snapshot in the envelope, so a short
downstream relay (see `alerts-subscriber/`) can post the photo to
Telegram / ntfy / Discord with the notification. **Pure REST
enrollment** — `smart_doorbell.py enroll --image alice.jpg ...`, no
shared volume, no desktop tool.

| | |
|---|---|
| Pattern | Drives InsightFace → severity-routed alerts |
| Adapters | InsightFace (face detection + recognition + embedding) |
| Difficulty | ⭐⭐ intermediate |
| Best for learning | Per-person dedup, severity routing, REST enrollment flow |
| Tests | 31 |

```bash
cd examples/smart-doorbell && uv sync --extra dev
cp config.example.yml config.yml      # edit doorbell URL + tokens
# Enroll family (REST, no GUI)
python smart_doorbell.py enroll --config config.yml \
  --person-id alice --name "Alice Smith" --image alice.jpg --category family
# Run the daemon
python smart_doorbell.py daemon --config config.yml
```

---

### [`package-delivery/`](package-delivery)

**Alert me when a package arrives — and when it leaves.** YOLOv8 on a porch
ROI with a per-track state machine that distinguishes arrive → (linger) →
disappear. The "package taken by a stranger" path fires with high severity
when no person was seen near the porch at pickup time, so homelab users
aren't woken every time they bring in their own boxes.

| | |
|---|---|
| Pattern | Drives YOLOv8 → IoU tracker → state machine → fires alerts |
| Adapters | YOLOv8 |
| Difficulty | ⭐⭐ intermediate |
| Best for learning | Duration-based predicates, per-track state machines, ROI filtering |
| Tests | 54 |

```bash
cd examples/package-delivery && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs + porch ROI
python package_delivery.py --config config.yml
```

---

### [`camera-agent/`](camera-agent) — preview

**Ask your cameras.** A voice agent that listens for spoken questions
in a browser tab, grounds its answers in live camera feeds via tool
calling (BLIP scene caption + YOLOv8 detection + InsightFace
recognition + NATS event history), and replies through Piper TTS.
Pipecat-based pipeline with Silero VAD for natural turn-taking. All
CPU-runnable. The first OpenNVR example where cameras have agency,
not just data.

**Preview note:** three integration points (Whisper / Piper response
field names, WebSocket serializer pairing, the BLIP SDK service)
need verification against your deployed adapter versions before the
voice loop runs end-to-end. The infrastructure (config loader, frame
cache, event ring, tool definitions, 46 tests) is tested and stable;
the streaming round-trip is shaped but not yet pinned. See the
example's README "Status: preview" section.

| | |
|---|---|
| Pattern | WebSocket voice conversation → tool-calling LLM → live camera adapters |
| Adapters | Whisper + Ollama + Piper (voice path) + BLIP + YOLOv8 + InsightFace (tools) |
| Difficulty | ⭐⭐⭐ advanced |
| Best for learning | Pipecat pipelines, OpenAI-style tool calling against local Ollama, custom Pipecat services bridging an adapter contract |
| Tests | 46 |

```bash
cd examples/camera-agent && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs, system prompt
python camera_agent.py --config config.yml
# then open http://localhost:9100/demo, click Start, and speak
```

---

## 🚧 Planned — coming in v0.1

The next round of viral, demo-friendly examples. **Want to help
build one?** Open a discussion and we'll match scope to interest.

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

Beyond the planned example above, these are explicitly welcome contributions
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
all eight shipped examples so you can copy one folder, rename `<example>.py`,
and replace the predicate with your domain logic — everything else (alert
routing, correlation IDs, NATS publishing, SIGINT handling) is the template.

---

## 🤝 Contributing your own example

The fastest path to a first-party example slot:

1. Open a [discussion](https://github.com/open-nvr/open-nvr/discussions) with
   your idea, the camera setup you'll demo on, and the adapter(s) you'll
   chain.
2. Fork, branch, and copy one of the eight shipped examples as your starting
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

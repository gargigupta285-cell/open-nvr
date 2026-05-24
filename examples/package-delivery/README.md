# package-delivery example app

The fourth producer-side first-party OpenNVR example. Watches a porch
camera, drives YOLOv8 object detection through KAI-C, runs a per-track
state machine, and fires alerts when a package **arrives**, optionally
when it has been **lingering** for too long, and when it's **gone** —
with severity routed by whether a person was seen at pickup time.

The whole point of this example is the **state machine**: most OpenNVR
examples are stateless predicates ("is there a person in this zone right
now?"). Package delivery is a duration-based predicate ("did something
arrive, sit, and then disappear?") and the predicate forks easily into
"car arrived and stayed", "dog left the yard", "shed door open longer
than X" — copy this folder and replace the state machine with yours.

## What it does

```
┌─────────────┐    every poll_interval_seconds
│  Porch cam  │ ────────────────────────────────┐
└─────────────┘                                 │
                                                ▼
                              ┌───────────────────────────────────┐
                              │ frame_sources.fetch (HTTP / file) │
                              └──────────────┬────────────────────┘
                                             │ frame JPEG bytes
                                             ▼
                              ┌───────────────────────────────────┐
                              │ KAI-C → YOLOv8 adapter            │
                              │  POST /api/v1/infer/yolov8        │
                              │  body: {"frame_b64": "<...>"}     │
                              └──────────────┬────────────────────┘
                                             │ Detection[]
                                             ▼
                              ┌───────────────────────────────────┐
                              │ PackagePipeline                   │
                              │   • filter to package_labels      │
                              │   • apply per-camera ROI          │
                              │   • split person sightings out    │
                              └──────────────┬────────────────────┘
                                             │ FrameReads
                                             ▼
                              ┌───────────────────────────────────┐
                              │ IouTracker (per camera)           │
                              │   • greedy IoU match              │
                              │   • per-track hits / misses       │
                              └──────────────┬────────────────────┘
                                             │ track ids
                                             ▼
                              ┌───────────────────────────────────┐
                              │ PackageDelivery state machine     │
                              │   new → arrived → (lingering) →   │
                              │     gone (owner | stranger)       │
                              └──────────────┬────────────────────┘
                                             │ Alert
                                             ▼
                              ┌───────────────────────────────────┐
                              │  AlertDispatcher                  │
                              │  stdout / webhook / NATS          │
                              └───────────────────────────────────┘
```

A single `correlation_id` flows through every step so KAI-C's audit
log joins the chain end-to-end: detector inference → state-machine
event → alert.

## Why the state machine matters

Stateless predicates are easy: "person in zone right now → fire."
Duration-based predicates need state: a single missed detection
shouldn't fire "package gone" if the package will reappear in the next
frame; a single false positive shouldn't fire "package arrived" if it
won't show up again. The state machine here is two integers per track
(`hits`, `misses`) and a `state` string:

| State | Enters when | Exits to |
|---|---|---|
| `new` | track is created (first detection) | `arrived` once `hits >= arrive_consecutive_hits` |
| `arrived` | arrival fires | `lingering` if `linger_alert_after_seconds > 0` and the threshold elapses (one-shot — fires once per track); `gone` if `misses >= gone_consecutive_misses` |
| `lingering` | linger alert fires | `gone` if `misses >= gone_consecutive_misses` |
| `gone` | gone alert fires | track dropped from the tracker |

Tuning the two thresholds is the operator's main knob: bump
`arrive_consecutive_hits` to reduce false-positive arrivals; bump
`gone_consecutive_misses` to ride through a single noisy frame where the
detector misses the box.

## Owner vs porch pirate

When a package disappears, the orchestrator looks back
`pickup_person_lookback_seconds` for a person detection inside the same
porch ROI. If it finds one, the pickup is filed as **owner** (info
severity); if not, as **stranger** (high severity). It's a heuristic —
trees can flag as persons in some YOLOv8 weights, a delivery person
might also count as "stranger" — but it's a useful first filter so
homelab users aren't getting high-severity alerts every time they bring
in their own boxes.

Set `pickup_person_lookback_seconds: 0` in config to disable the
heuristic entirely; every disappearance then fires as "info" so you
review every pickup yourself.

The snapshot attached to a "gone" alert is the **camera frame at the
moment the disappearance was confirmed** (i.e. after
`gone_consecutive_misses` blank frames) — NOT a snapshot of the
pickup itself. The pickup happened some seconds earlier. For
pickup-moment evidence, configure the camera to publish continuous
inference events through KAI-C and pair this example with
`alerts-subscriber/` riding the same NATS subject.

## Honesty up front

Real-world failure modes the example does NOT yet handle:

* **No "package" class in stock YOLOv8.** The COCO model the YOLOv8
  adapter ships with knows `suitcase`, `backpack`, `handbag` — not
  `package`. The defaults use those three COCO labels as proxies,
  which works for medium-large cardboard boxes and soft parcels but
  misses small envelopes. For a real package detector, swap in custom
  YOLOv8 weights trained on your porch footage via the YOLOv8
  adapter's `OPENNVR_YOLOV8_MODEL` env var.
* **Track identity across long gaps.** The IoU tracker matches frame
  to frame. If a package is moved (kicked aside, repositioned by the
  homeowner) by more than its own size, it drops the track and starts
  a new one — which fires a fresh "arrived" event. Increasing the
  poll interval or the IoU threshold makes this worse, not better.
* **Stranger ≠ porch pirate.** YOLOv8's `person` class fires for
  delivery drivers, neighbours, kids playing — anyone with a body.
  The heuristic flags "person was here when the package vanished",
  not "person who took the package wasn't authorised". For real
  identity matching, chain the InsightFace adapter (see
  `smart-doorbell/` for the pattern).
* **Weather / lighting.** The detector is what KAI-C / YOLOv8 ship.
  Heavy rain, a brown box on a brown door, harsh midday shadows — all
  push confidence below the threshold. Set `detection_confidence`
  conservatively and review false negatives over a real day's footage
  before relying on the alerts.

## Quick start

```bash
# 1. Start the YOLOv8 adapter (in the ai-adapter repo)
#    YOLOv8 ships with the OpenNVR ai-adapter image. First boot
#    downloads the ~50MB ONNX weights to /app/model_weights —
#    mount a host directory there so the download persists.
cd ai-adapter
docker build -f adapters/yolov8/Dockerfile -t opennvr/yolov8-adapter:local .
OPENNVR_ADAPTER_TOKEN=$(openssl rand -hex 16)
mkdir -p model-weights
docker run --rm -d --name yolov8 -p 9001:9001 \
  -e OPENNVR_ADAPTER_TOKEN=$OPENNVR_ADAPTER_TOKEN \
  -v $(pwd)/model-weights:/app/model_weights \
  opennvr/yolov8-adapter:local

# 2. Start KAI-C and register the adapter
cd ../open-nvr/kai-c
INTERNAL_API_KEY=$(openssl rand -hex 32)
AI_SOVEREIGNTY=local_only INTERNAL_API_KEY=$INTERNAL_API_KEY \
  python -m uvicorn main:app --host 0.0.0.0 --port 8100 &
curl -X POST http://localhost:8100/api/v1/adapters/register \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"yolov8","url":"http://127.0.0.1:9001"}'

# 3. Configure
cd ../examples/package-delivery
cp config.example.yml config.yml
# edit config.yml: kaic_api_key, camera frame_url, porch roi (optional but
# strongly recommended — otherwise every backpack crossing the frame fires)

# 4. Run the daemon
python package_delivery.py --config config.yml
```

You'll see lines like:

```
2026-05-23T14:10:43+00:00 INFO  package-delivery: started: 1 cameras, poll=3.0s, package_labels=['suitcase', 'backpack', 'handbag']
ALERT [INFO] 2026-05-23T14:11:02+00:00 camera=front-porch title='Package arrived on front-porch' correlation_id=a4f1b... alert_id=alrt_8c2d31
ALERT [HIGH] 2026-05-23T16:02:54+00:00 camera=front-porch title='Package gone from front-porch (no person seen)' correlation_id=8d3f5... alert_id=alrt_91e2bb
```

## Telegram / ntfy / Discord delivery

Same shape as `smart-doorbell`: every alert ships a base64 JPEG in
`evidence.snapshot_b64`. A small downstream relay (≈15 lines of
Python, n8n, or Node-RED) reads the field and forwards the image to
your channel of choice. See `alerts-subscriber/` for the template.

## Operate

| Mode | Command |
|---|---|
| Daemon (continuous polling) | `python package_delivery.py --config config.yml` |
| Single cycle (debug / cron) | `python package_delivery.py --config config.yml --once` |
| Verbose logs | `python package_delivery.py --config config.yml --log-level DEBUG` |

## Configure

See `config.example.yml` for the full set. The interesting knobs:

| Field | Default | Effect |
|---|---|---|
| `package_labels` | `[suitcase, backpack, handbag]` | COCO classes that count as "package". Swap in your custom class name once you have a trained model. |
| `person_labels` | `[person]` | Used only for the owner-vs-stranger heuristic. Set `[]` to disable. |
| `detection_confidence` | `0.35` | YOLOv8 confidence floor. Start here; tighten if you see false arrivals. |
| `arrive_consecutive_hits` | `2` | Frames in a row before "arrived" fires. Higher = less flicker, more latency. |
| `gone_consecutive_misses` | `3` | Missed frames before "gone" fires. Higher = ride through detector blips. |
| `iou_threshold` | `0.30` | IoU threshold for matching detections to existing tracks across frames. |
| `linger_alert_after_seconds` | `0` | Fire one "still here after Xh" alert per package. `0` disables. |
| `pickup_person_lookback_seconds` | `8.0` | Window for the owner-vs-stranger heuristic. `0` disables (every pickup fires info). |
| `cameras[].roi` | unset | Per-camera porch ROI. Detections outside are ignored. Highly recommended; without it, every backpack crossing the frame can fire. |

## Tests

```
cd examples/package-delivery
uv sync --extra dev
uv run pytest -q
```

Tests cover:

* Config validation (kaic url, malformed ROIs, label normalisation)
* Bbox parsing across the §5.1 canonical dict shape + list shapes
* ROI point-in-polygon + AABB
* IoU helper + greedy tracker matching
* State machine transitions: arrival threshold, gone threshold, linger
* Owner-vs-stranger severity routing
* Snapshot attachment + size cap behaviour
* Dedup of repeat arrivals within the window

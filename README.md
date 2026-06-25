<div align="center">

<img src=".github/opennvr-logo.svg" alt="OpenNVR" width="300" />

### Cameras are everywhere. Almost none of them are yours.

OpenNVR™ is the open, sovereign platform for recording your cameras and running AI on them — entirely on hardware you own, with **AI you choose and control**. No vendor cloud holds your footage or watches it for you. Air-gapped by default, an audit trail you can hand to a regulator. From a homelab doorbell that never phones home, to a laptop you spin it up on in a minute, to the air-gapped government site that legally cannot use anything else.

[![CI](https://github.com/open-nvr/open-nvr/actions/workflows/ci.yml/badge.svg)](https://github.com/open-nvr/open-nvr/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.17261761-blue.svg)](https://doi.org/10.5281/zenodo.17261761)

[Quickstart](#quickstart) · [Talk to your cameras](#talk-to-your-cameras) · [Build on it](#build-on-it) · [Read the paper](https://doi.org/10.5281/zenodo.17261761)

</div>

---

## Why this exists

In 2016, a botnet called Mirai conscripted hundreds of thousands of IP cameras into the largest DDoS attack the internet had ever seen. In 2021, an attacker compromised cloud credentials at Verkada and took live feeds from around 150,000 cameras across hospitals, schools, prisons, and factories. Federal advisories continue to land against major vendors — Hikvision, Dahua, Uniview, Edimax — whose firmware quietly powers critical infrastructure around the world.

The pattern keeps repeating because the architecture is wrong. Cameras are connected to vendor clouds. The vendor holds the keys. The vendor controls the AI. The vendor's breach is your breach. A decade after Mirai, the industry has not fixed itself.

And now the rules have changed. Under NDAA §889 and the 2025–26 FCC enforcement, U.S. federal agencies, contractors, and a widening set of regulated buyers can no longer use cameras from the dominant vendors — forcing a rip-and-replace cycle in environments where cloud surveillance was never an option to begin with: defence, critical infrastructure, healthcare, schools, and government. They need a recording and AI layer they can run entirely on their own terms. That layer didn't exist as open infrastructure. OpenNVR is the bet that it should.

OpenNVR is the bet that the alternative is open-source surveillance infrastructure built around four commitments: **cameras you connect, hardware you own, AI you choose and author, audit logs you can show to a regulator.**

The architecture is published — a peer-citable paper this year, 34 references, three-tier offline-first model, six categories of systemic IP-camera weakness it structurally eliminates ([DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761)). This repo is the reference implementation.

## What makes it different

**It's secure by design.** Network isolation between the camera plane, the middleware gateway, and the analytics layer is the architecture, not a configuration toggle. Credentials are encrypted at rest with Fernet, RTSP travels over RTSPS to anything outside the host, and two independent default-deny gates — `DEPLOYMENT_MODE=offline` and `AI_SOVEREIGNTY=local_only` — keep cloud routes and AI egress returning HTTP 403 until an operator explicitly opens them. The systemic IP-camera weaknesses the paper documents — default credentials, hard-coded keys, unsigned firmware updates, exposed management interfaces, vendor-controlled cloud aggregation, opaque telemetry — are structurally eliminated rather than mitigated case by case. Threat model and control mapping in [`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md).

**It's auditable.** Every inference threads a correlation ID from the alert that fired, through the middleware that proxied it, to the model that made the call. Model weights are fingerprinted with sha256 and polled for drift. Cloud routes return HTTP 403 by default. The audit log answers *"why did this alert fire?"* without guesswork. Procurement-grade evidence in [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md).

**Its AI layer is open.** Any model behind a REST or WebSocket endpoint becomes a first-class capability through the AI Adapter Contract — a published wire spec. Object detection, open-vocabulary detection, license-plate OCR, face recognition, scene captioning, multi-object tracking, ASR, TTS, LLM tool-calling all ship out of the box. The SDK to write your own is Apache-2.0 and runs around thirty lines of Python.

**You can talk to it — light or full.** The included camera-agent lets you *ask* your cameras questions. It starts as a ~1–2 GB **text** agent on a plain CPU — one command, no GPU — and scales up to a full **hands-free voice** loop with a named persona and animated avatar. The brain runs locally (Ollama) by default, or you **bring your own** any OpenAI-compatible model — your choice, your control. A few editions cover the range from a dev laptop to a GPU box; details in [`examples/camera-agent/EDITIONS_AND_MODELS.md`](examples/camera-agent/EDITIONS_AND_MODELS.md).

**It runs on the hardware you already have.** If the machine it's on has a camera — a laptop webcam, a USB or Pi camera, the onboard sensor on a drone or robot — the agent can discover and use it with zero provisioning. Any device that can see a camera or a stream can run its own on-board sovereign agent.

**It's built for sovereignty.** For homelab users that means the doorbell that doesn't phone home. For defence, critical infrastructure, healthcare, and government deployments it means tactical AI that runs on your hardware under your control — models you've fine-tuned, models you can't share with a vendor, analytics whose detection logic itself is operationally sensitive. The procurement brief is in [`docs/GOVERNMENT_DEPLOYMENT.md`](docs/GOVERNMENT_DEPLOYMENT.md).

## Quickstart

**Three commands, five minutes** — `git clone` to YOLOv8 detection on your camera feed. Pre-built images on GHCR, no source build.

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
./start.sh up
```

That's it. First run launches a brief interactive setup (deploy mode, recording path, secrets generated for you). Run `./start.sh up` again to start containers.

> **Just want to try the AI?** `examples/camera-agent/quickstart.sh` brings up a ~1–2 GB text agent you can ask questions in one command — no GPU, and you can point it at your laptop webcam. See [Talk to your cameras](#talk-to-your-cameras).

### What you'll see when it boots

```
NIC topology: dual-NIC (cameras isolated from operator network)
  operator uplink: 192.168.1.50  ← UI bound here
  Web UI: https://192.168.1.50/

🔑 First-time setup token (one-time use — copy into the UI):
  ================================================================
   OpenNVR first-time setup token (one-time use)
  ----------------------------------------------------------------
    aXyZ_pasteThisIntoTheBrowser_4cFiRsT-tImE-sEtUp
  ----------------------------------------------------------------
  ================================================================
```

Then:

1. **Open the printed URL** on any device on your LAN.
2. **Accept the self-signed cert warning once** — *Advanced → Accept the risk and continue*. The cert lives in `./nginx-certs/` on the host and never leaves the machine.
3. **Paste the token, set an admin password, add a camera.** Detection overlays appear within ~30 seconds.

Live streams (WebRTC, HLS) and recording playback work from any LAN device.

### Common follow-ups

| Situation | Command |
|---|---|
| Lost the setup token | `docker compose logs opennvr-core \| grep -A 6 'first-time setup token' \| tail -7` |
| Your LAN IP changed (DHCP, moved boxes) | `./start.sh refresh-certs` |
| Stop everything | `./start.sh down` |
| Tail live logs | `./start.sh logs` |
| Check container status | `./start.sh status` |
| Pick up new GHCR images after an upgrade | `docker compose pull && ./start.sh up` |

### Advanced setup

Want to skip the interactive wizard — pin specific secret values, run unattended on a CI box, deploy from configuration management?

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
cp .env.example .env
./scripts/generate-secrets.sh --write    # or write your own values
./start.sh up                            # still gets pre-flight + posture + token
```

Skipping `./start.sh up` and using bare `docker compose up -d` works too (Docker Compose v2.20+ — `docker-compose.yml` is an `include:` shim → `tier0.yml`), but you'll lose: NIC topology auto-detect, the security posture banner, the one-time setup token surfacing. Grep the logs manually if you go that route.

**Need more detail?** [`DOCKER_QUICKSTART.md`](DOCKER_QUICKSTART.md) covers retention, production hardening, profile options. [`docs/DOCKER_SETUP.md`](docs/DOCKER_SETUP.md#compose-file-reference) explains every compose file in the repo and when each one applies.

## Talk to your cameras

The camera-agent lets you *ask* your cameras questions — all on your hardware. Start light with **one command** (text chat, detection only, ~1–2 GB RAM, no GPU):

```bash
examples/camera-agent/quickstart.sh        # Spotter (lite): type a question
```

Then open <http://localhost:9100/demo> and type *"how many people are at the door?"*. No camera yet? Click **"Use this machine's camera"** to run against your laptop webcam (or any USB/Pi/onboard device) with zero provisioning.

Step up an edition when you want more — one flag each:

```bash
examples/camera-agent/quickstart.sh --standard  # Watch: + scene description & visual Q&A
examples/camera-agent/quickstart.sh --voice     # Sentinel: full hands-free voice + persona
```

What each edition runs and which models it uses is laid out in [`examples/camera-agent/EDITIONS_AND_MODELS.md`](examples/camera-agent/EDITIONS_AND_MODELS.md). Prefer to drive Compose yourself? `docker compose -f docker-compose.yml -f docker-compose.camera-agent.yml --profile camera-agent-lite up -d` does the same as the lite quickstart.

**What you can ask:**

| You say | What happens |
|---|---|
| *"What's at the back gate?"* | LLM calls BLIP for a scene caption of the live frame |
| *"Is anyone in the kitchen?"* | LLM calls YOLOv8 on the current frame |
| *"Did anyone walk past in the last ten minutes?"* | LLM queries the inference event ring on NATS |
| *"Who was at the door this morning?"* | LLM calls InsightFace against your enrolled face DB |
| *"Did a red truck come by the dock earlier?"* | LLM searches the recorded-footage index (when a [`footage-search`](examples/footage-search) index is configured) |

Under the hood: a local LLM (Ollama) doing OpenAI-style tool-calling over your live frames — or a cloud brain you bring. The lite default is text-only on CPU; the full voice loop (Pipecat · Silero VAD · Whisper STT · Piper TTS) is one flag up. No cloud and no API keys unless *you* choose a cloud model.

This is the first OpenNVR example where the cameras have agency, not just data.

## Build on it

The AI Adapter Contract is what makes OpenNVR a platform, not a product. Any model behind a REST or WebSocket endpoint can become a first-class capability:

```python
from opennvr_adapter_sdk import (
    AdapterApp, AdapterService, BodyShape, BODY_BYTES_KEY,
    HardwareEvaluationResponse, HardwareVerdict,
    InferResponse, ModelInfo,
)

class MyDetector(AdapterService):
    def load(self):
        # eagerly load your weights
        ...

    def is_ready(self) -> bool:
        return True

    def fingerprint(self) -> str | None:
        return "sha256:..."

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            name="my-model", version="1.0.0",
            framework="onnx", fingerprint=self.fingerprint(),
        )

    def hardware_evaluation(self) -> HardwareEvaluationResponse:
        return HardwareEvaluationResponse(verdict=HardwareVerdict.OK, ...)

    def infer(self, payload) -> InferResponse:
        frame_bytes = payload[BODY_BYTES_KEY]
        # ... your model ...
        return InferResponse(result={"detections": [...]})

app = AdapterApp(
    service=MyDetector(),
    name="my-detector", version="1.0.0", vendor="me", license="MIT",
    tasks_advertised=["object_detection"],
    body_shape=BodyShape.IMAGE,
).fastapi_app
```

`uvicorn my_module:app --port 9100`, register the URL with KAI-C, and your adapter is online — hot-swappable, audit-chained, fingerprint-tracked. The SDK is Apache-2.0 so your adapter can ship under any compatible license, including proprietary or classified.

What the contract makes straightforward to build (some already ship as examples):

- **Natural-language footage search** — "find clips with a red truck at the dock yesterday" — ships today as the [`footage-search`](examples/footage-search) example, using scene captions plus the open-vocabulary [`vlm`](https://github.com/open-nvr/ai-adapter/tree/main/adapters/vlm) adapter.
- **Tracker-stable alert deduplication** for warehouses ("don't fire 'person detected' sixty times for the same forklift driver walking past").
- **Pose-based fall detection** for memory-care facilities (needs a pose adapter; on the roadmap).
- **Site-specific PPE compliance** for construction with the false-positive threshold tuned to what the insurer will accept.
- **Domain-specific NVRs** — dispensary compliance, school weapons detection, port cargo logging — built by forking an example and replacing the predicate.

Eight reference adapters and a one-command scaffold to start your own live in the sibling [ai-adapter](https://github.com/open-nvr/ai-adapter) repo. Full authoring walkthrough in the [SDK README](https://github.com/open-nvr/ai-adapter/blob/main/opennvr_adapter_sdk/README.md).

## Applications ship on top of it

Adapters are *capabilities*; applications are *solutions*. Each example below is a working application — adapter(s) + a pipeline + alert rules — that you install, point at a camera, and adapt. Replace the predicate (the zone check, the dwell timer, the plate watchlist) with your domain logic and you have a purpose-built NVR. This is the platform's direction: a catalog of installable applications, not a fixed feature set.

| Application | What you'll build | Difficulty |
|---|---|---|
| [`intrusion-detection`](examples/intrusion-detection) | People in restricted zones during restricted hours | beginner |
| [`loitering-detection`](examples/loitering-detection) | Dwell-time state machine on a NATS inference stream | intermediate |
| [`occupancy-counting`](examples/occupancy-counting) | Zone occupancy with edge-triggered over/under alerts | intermediate |
| [`line-crossing`](examples/line-crossing) | Directional tripwire / entry-exit counting (tracked) | intermediate |
| [`abandoned-object`](examples/abandoned-object) | Unattended-item detection with owner-proximity suppression | advanced |
| [`footage-search`](examples/footage-search) | Natural-language search over recorded inference ("red truck yesterday") | advanced |
| [`license-plate-recognition`](examples/license-plate-recognition) | YOLOv8 + fast-plate-ocr chain with allowlists | intermediate |
| [`smart-doorbell`](examples/smart-doorbell) | InsightFace recognition with REST enrollment | intermediate |
| [`package-delivery`](examples/package-delivery) | Per-track state machine for arrival, linger, pickup | intermediate |
| [`camera-agent`](examples/camera-agent) | Ask your cameras questions — ~1–2 GB text mode on a laptop, up to full hands-free voice | beginner→advanced |
| [`home-assistant-relay`](examples/home-assistant-relay) | Bridge alerts into Home Assistant via MQTT discovery | intermediate |

Eleven of the thirteen shipped examples are listed above; [`inference-listener`](examples/inference-listener) and [`alerts-subscriber`](examples/alerts-subscriber) round out the set as minimal subscriber templates. Each application is a copy-as-template starting point. Gallery walkthrough and the "drives inference vs subscribes to events" axis-grid in [`examples/README.md`](examples/README.md). The roadmap for the application catalog — audio-event detection, tamper-evident incident export, and the vertical safety/security packs — is in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Community

Bugs go in [Issues](https://github.com/open-nvr/open-nvr/issues), design questions in [Discussions](https://github.com/open-nvr/open-nvr/discussions), security reports via [private GHSA advisory](https://github.com/open-nvr/open-nvr/security/advisories/new). PR flow is in [`CONTRIBUTING.md`](CONTRIBUTING.md); the [roadmap](docs/ROADMAP.md) names where help is wanted next.

Commercial deployments — deployment assistance, NDA adapter authoring, compliance evidence packs, SLA-backed support — [contact@cryptovoip.in](mailto:contact@cryptovoip.in).

## Documentation

**Getting started** — [Docker quickstart](DOCKER_QUICKSTART.md) · [User manual](USER_MANUAL.md) · [Local dev setup](docs/LOCAL_SETUP.md) · [Use cases by industry](docs/USE_CASES.md) · [Comparisons](docs/COMPARISONS.md)

**Architecture & security** — [Security architecture](docs/SECURITY_ARCHITECTURE.md) · [Compliance mapping](docs/COMPLIANCE.md) · [Government deployment brief](docs/GOVERNMENT_DEPLOYMENT.md) · [AI Adapter Contract](docs/AI_ADAPTER_CONTRACT.md)

**Project** — [Roadmap](docs/ROADMAP.md) · [Support](docs/SUPPORT.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

## License & trademark

OpenNVR is **AGPLv3**. The [adapter SDK](https://github.com/open-nvr/ai-adapter/tree/main/opennvr_adapter_sdk) is **Apache-2.0**, so adapters you write can ship under any compatible license — including proprietary or classified for the organisations where that matters.

"OpenNVR" and the OpenNVR logo are trademarks of the project. You may use them to refer to the project and to describe software as "compatible with OpenNVR," but redistribution of modified versions under the OpenNVR name requires permission. See [`TRADEMARK.md`](TRADEMARK.md).

---

<div align="center">

**OpenNVR — cameras you connect, hardware you own, AI you choose and author, audit you can show.**

[⭐ Star on GitHub](https://github.com/open-nvr/open-nvr) · [📄 Read the paper](https://doi.org/10.5281/zenodo.17261761) · [⚡ Quickstart](#quickstart)

</div>

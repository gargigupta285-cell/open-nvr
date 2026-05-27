<div align="center">

# OpenNVR

### The self-hosted NVR you can talk to.

Object detection, license-plate OCR, face recognition, scene captioning, multi-object tracking —
and a voice agent that grounds its answers in live camera feeds. All running on your hardware.
No cloud calls by default. Pluggable AI adapter contract. AGPL.

[![CI](https://github.com/open-nvr/open-nvr/actions/workflows/ci.yml/badge.svg)](https://github.com/open-nvr/open-nvr/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.17261761-blue.svg)](https://doi.org/10.5281/zenodo.17261761)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

> **v0.1 is here.** Nine first-party example apps shipping today — from
> *"is there a person in this zone?"* to *"ask your cameras out loud."*
> Skip to the [gallery](#-examples).

[Quick start](#-quick-start-under-5-minutes) · [Why OpenNVR](#-why-opennvr) · [Examples](#-examples) · [Compliance](docs/COMPLIANCE.md) · [Architecture](docs/SECURITY_ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md)

</div>

---

> **Built on published research.** OpenNVR is the open-source reference implementation of
> *Eliminating Systemic IP Camera Vulnerabilities via Offline-First Open Security Architecture*
> ([Singh et al., 2025 — DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761)).
> 34 sources spanning CISA advisories, real CVEs (Hikvision, Dahua, Uniview, Edimax, ThroughTek
> Kalay), the 2021 Verkada breach, NIST CSF 2.0, NIST AI RMF, ISO/IEC 27001, ETSI EN 303 645,
> GDPR, and India's DPDP Act. Paper §3 → §4 → code mapping in [docs/COMPLIANCE.md](docs/COMPLIANCE.md).
>
> **For critical infrastructure, defence, government, and any organisation where IP-camera
> security is a hard requirement:** the adapter contract lets your tactical AI run on your
> hardware under your control — models you've fine-tuned, models you can't share with a
> vendor, analytics whose detection logic itself is operationally sensitive. Camera-layer
> isolation, middleware you patch on your own cadence, AI you author and run locally,
> audit chain that proves none of it touched a vendor cloud. See
> [docs/GOVERNMENT_DEPLOYMENT.md](docs/GOVERNMENT_DEPLOYMENT.md) for the procurement-grade
> brief.

---

## ⚡ Quick start (under 5 minutes)

Pulls pre-built images straight from GHCR — no toolchain, no source build, no
manual model downloads. NVR core + YOLOv8 object detection running on your
camera feed in the time it takes to make a coffee.

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
cp .env.example .env
./scripts/generate-secrets.sh --write
docker compose -f docker-compose.tier0.yml up -d
```

Open <http://localhost:8000>, paste the one-time setup token from the terminal,
choose an admin password, and add your first camera. YOLOv8 object detection is
running on every frame from the moment the camera connects — no extra config.

Want voice control? Layer the camera-agent on top:

```bash
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent run --rm ollama-model-pull
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent up -d
```

Open <http://localhost:9100/demo>, click "Start", and ask
*"is there a person at the front door?"* — the agent grounds its answer in a
live frame from your camera and speaks the reply.

Every security feature ships **on by default**. You don't configure security — you configure exceptions.

> **Building from source instead?** The legacy `./start.sh` (Linux/macOS) and
> `.\start.ps1` (Windows) launchers still work and live alongside the Tier 0
> compose for contributors and dev workflows. Pre-built images mean nobody has
> to wait for a 20-minute build to try the project.

---

## 🎯 Why OpenNVR

> Most NVRs treat AI as a bolt-on and security as a checkbox. OpenNVR inverts that.

- **🔌 Pluggable AI adapters.** Any model behind a REST or WebSocket endpoint becomes
  a first-class detector. YOLOv8, InsightFace, Whisper, Piper, your custom ONNX —
  same contract, hot-swappable, no fork required.
- **🛡️ Secure by default.** Strong-secret validators, RTSPS / HLS-TLS / WebRTC-TLS,
  loopback-only MediaMTX, offline-mode network posture, one-time admin setup token.
  No shipped default password, ever.
- **📜 Audit trail end-to-end.** Every inference carries an `X-Correlation-Id` from
  alert → middleware → adapter. Investigate "why did this alert fire at 22:14?" without
  guessing.
- **🧬 Drift detection.** Model weights are fingerprinted with sha256 and polled every
  minute. Accidental model rotation or tampering surfaces as an audit event,
  not silence.
- **📡 Real event bus.** Alerts and inference results publish to NATS with a public
  subject scheme. Build downstream apps with copy-as-template subscribers — Home
  Assistant relays, custom dashboards, your SOC pipeline.
- **🏠 Sovereignty-first.** Offline mode is the default. Cloud routes return 403
  unless you explicitly opt in. Your footage doesn't leave your hardware unless you
  wire it up.

---

## 🛡️ Security: the defaults other NVRs leave to you

Most open-source NVRs treat security as the operator's homework. OpenNVR
enforces it at boot — every relaxation is an explicit decision that lands in
the audit log.

| Concern | Frigate / Shinobi / Viseron | **OpenNVR** |
|---|---|---|
| First-boot admin account | Default credentials or unset auth | **One-time setup token, no shipped password** |
| Secret strength | Operator's responsibility | **Refuses to boot on placeholder or short secrets** |
| Cloud egress | On by default | **403 unless `DEPLOYMENT_MODE` is switched from `offline`; audit-logged** |
| AI sovereignty | No concept | **Adapters declaring `network_egress` refused under `local_only`** |
| Camera TLS | Plaintext OK | **RTSPS + HLS-TLS + WebRTC-TLS on by default; plaintext requires opt-in + audit** |
| Audit trail | Application logs | **End-to-end correlation ID from alert → adapter; append-only event log** |
| Model integrity | Trust the file | **sha256 polled every 60s; drift surfaces as `adapter.fingerprint_mismatch`** |

Built to close every systemic IP-camera weakness documented in recent academic
work on networked surveillance. Full threat model and control mapping in
[`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md) · academic
foundation at [Zenodo DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761).

---

## 🔍 How OpenNVR compares

| Concern | Frigate | Shinobi | Viseron | **OpenNVR** |
|---|---|---|---|---|
| Pluggable AI models | Hardware-detector plugins (Coral / OpenVINO / TensorRT / Hailo) | Plugin system | Built-in YOLO + face | **Open contract — any model behind REST/WS** |
| Audit trail | Event DB | Logs | Logs | **Per-request correlation ID across the stack** |
| Sovereignty enforcement | — | — | — | **Cloud routes 403 by default; opt-in only** |
| Model fingerprint drift detection | — | — | — | **sha256 polled every 60s; audit events** |
| Event bus | Internal MQTT | webhook | webhook | **NATS, public subjects, template subscribers** |
| Multi-tenant fairness signal | Single-process | Multi-monitor | Single-process | **Adapter contract declares per-camera fair-queuing intent** |
| TLS defaults | User-managed | User-managed | User-managed | **RTSPS + HLS-TLS + WebRTC-TLS on by default** |
| First-boot account | First-login password creation, bearer cookies | Default credentials | Default credentials | **One-time setup token, no shipped password** |
| Frontend | Web UI | Web UI | Web UI | **Web UI + JSON API + reusable React shell** |
| License | MIT | GPLv3 | MIT | **AGPLv3** |

OpenNVR is built for the question other NVRs don't try to answer: *what did my
system actually do, when, and on whose authority?* Every alert, every inference,
every adapter — traceable end to end, by default.

---

## 🧩 Features

| | |
|---|---|
| 📹 **Multi-camera NVR** | ONVIF / RTSP / RTSPS ingest · HLS playback · event recording · per-camera retention policies |
| 🤖 **AI pipeline** | YOLOv8 person detection · InsightFace recognition · Whisper ASR · Piper TTS · plug your own |
| 🔐 **Default-deny posture** | Loopback-only MediaMTX · placeholder-secret refusal · offline mode · sovereignty enforcement |
| 🌐 **Cross-platform** | Linux (host network) · macOS (bridge) · Windows (PowerShell) |
| 📡 **NATS event bus** | `opennvr.inference.*` and `opennvr.alerts.*` subjects · copy-as-template subscribers |
| 🧪 **Adapter SDK** | `pip install opennvr-adapter-sdk` · write a new detector in ~30 lines · ships with conformance tests |
| 🪪 **Audit log** | Every register / refresh / inference / refusal recorded with correlation ID and reason |
| 📦 **One-command install** | Interactive wizard generates secrets, certs, and brings up the full stack |

---

## 🚀 Install — for users

### Prerequisites

- Git
- Docker Desktop (macOS / Windows) **or** Docker Engine + Compose v2 (Linux)

### Tier 0 — pre-built images (recommended)

Pulls everything from GHCR. No source build, no toolchain. Target wall time on
50 Mbps broadband: under 5 minutes.

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
cp .env.example .env
./scripts/generate-secrets.sh --write     # Windows: .\scripts\generate-secrets.ps1 -Write
docker compose -f docker-compose.tier0.yml up -d
```

Open the **first-time setup token** the core container prints to its log
(`docker compose -f docker-compose.tier0.yml logs opennvr-core | grep TOKEN`),
visit <http://localhost:8000>, paste the token, set an admin password, and add
your first camera. YOLOv8 object detection runs on every frame from the moment
the camera connects.

**What ships in Tier 0:**

| Service | Image | Purpose |
|---|---|---|
| `opennvr-core` | `ghcr.io/open-nvr/core` | Backend + frontend + KAI-C connector |
| `mediamtx` | local (binary copy from `bluenviron/mediamtx`) | RTSP / HLS / WebRTC streaming |
| `yolov8-adapter` | `ghcr.io/open-nvr/yolov8-adapter` | Object detection |
| `db` | `postgres:15-alpine` | State persistence |
| `nats` | `nats:2-alpine` | Inference event bus |

**Endpoints:**

| Service | URL |
|---|---|
| Web UI | <http://localhost:8000> |
| API docs (OpenAPI) | <http://localhost:8000/docs> |
| MediaMTX HLS | <http://localhost:8888> |
| MediaMTX WebRTC | <http://localhost:8889> |

### Adding the voice agent

The camera-agent overlay layers Whisper STT + Piper TTS + Ollama LLM on top of
Tier 0 so you can talk to your cameras:

```bash
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent run --rm ollama-model-pull     # ~2 GB, one-time
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent up -d
```

Open <http://localhost:9100/demo>, click "Start", and speak.

### Stopping / restarting

```bash
docker compose -f docker-compose.tier0.yml down            # stop everything
docker compose -f docker-compose.tier0.yml ps              # show container health
docker compose -f docker-compose.tier0.yml logs -f         # tail logs
docker compose -f docker-compose.tier0.yml pull            # refresh to latest images
```

### Building from source (legacy / dev)

The smart launcher (`./start.sh` on Linux/macOS, `.\start.ps1` on Windows) still
works and builds every image locally instead of pulling from GHCR. Useful when
you're modifying the core or running an unreleased commit. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the from-source path.

---

## 🛠️ Build & run — for developers

Run all components locally without Docker, in their own venvs, for fast iteration.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)
- Node.js 18+
- PostgreSQL 13+
- [MediaMTX](https://github.com/bluenviron/mediamtx) (download the binary for your OS)

### Setup

```bash
git clone https://github.com/open-nvr/open-nvr.git
git clone https://github.com/open-nvr/ai-adapter.git     # sibling directory
cd open-nvr
make secrets-env                                          # writes server/.env
# Then edit server/.env: set DATABASE_URL to your local PostgreSQL
make check-secrets                                        # confirms no placeholders
```

### Run (5 terminals)

Each terminal starts at the parent directory where you cloned both repos side-by-side
(so `open-nvr/` and `ai-adapter/` are siblings, and the MediaMTX binary is also at
that level — copy it next to the repos).

| Terminal | Command |
|---|---|
| **1. Backend** | `cd open-nvr/server && uv venv && uv sync && alembic upgrade head && python start.py` |
| **2. KAI-C** (orchestrator) | `cd open-nvr/kai-c && uv venv && uv sync && python start.py` |
| **3. Frontend** | `cd open-nvr/app && npm install && npm run dev` (→ <http://localhost:5173>) |
| **4. MediaMTX** | `./mediamtx open-nvr/mediamtx.local.yml` |
| **5. AI Adapter** *(optional)* | `cd ai-adapter && uv venv && uv sync --extra all --extra cpu && uv run python download_models.py && uv run uvicorn app.main:app --reload --port 9100` |

The AI Adapter step downloads several hundred MB of model weights on first run.

Full bare-metal walkthrough in [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md). The
security-architecture details — startup validator, offline-first posture,
per-camera transport policy — live in
[`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md).

---

## 🧩 Add a new AI adapter (3 steps)

Want to plug a model OpenNVR doesn't ship with? You don't fork — you write a small
adapter and point KAI-C at it.

```bash
pip install opennvr-adapter-sdk
```

```python
from opennvr_adapter_sdk import (
    AdapterApp, AdapterService, BodyShape,
    HardwareEvaluationResponse, HardwareVerdict,
    InferResponse, ModelInfo,
)

class MyDetector(AdapterService):
    def load(self):                                          # eagerly load weights
        ...

    def is_ready(self) -> bool:
        return True

    def fingerprint(self) -> str | None:                     # sha256 of the weights
        return "sha256:..."

    def model_info(self) -> ModelInfo:
        return ModelInfo(name="my-model", version="1.0.0",
                         framework="onnx", fingerprint=self.fingerprint())

    def hardware_evaluation(self) -> HardwareEvaluationResponse:
        return HardwareEvaluationResponse(verdict=HardwareVerdict.OK, details="")

    def infer(self, payload) -> InferResponse:
        # Binary payloads land at payload[BODY_BYTES_KEY] — import the constant
        # from the SDK rather than hardcoding the literal so a future rename
        # doesn't silently break your adapter.
        from opennvr_adapter_sdk import BODY_BYTES_KEY
        frame_bytes = payload[BODY_BYTES_KEY]
        # ... run your model ...
        return InferResponse(result={"detections": [
            {"label": "person", "confidence": 0.93, "bbox": [10, 20, 100, 200]},
        ]})

app = AdapterApp(
    service=MyDetector(),
    name="my-detector", version="1.0.0", vendor="me", license="MIT",
    tasks_advertised=["object_detection"],
    body_shape=BodyShape.IMAGE,
).fastapi_app
```

`uvicorn my_module:app --port 9100`, then `POST` your adapter's URL to KAI-C's
`/api/v1/adapters/register`. It's hot-swappable from the dashboard. Full walkthrough
in the [ai-adapter docs](https://github.com/open-nvr/ai-adapter#-write-your-own-adapter).

---

## 🎬 Examples

Every example is a copy-as-template starting point — minimal, readable,
opinionated. **See the [gallery landing page](examples/README.md)** for the
full catalogue with screenshots, difficulty ratings, and run instructions.

| Example | What you'll build | Difficulty |
|---|---|---|
| [`intrusion-detection`](examples/intrusion-detection) | Detect people in restricted zones during restricted hours | ⭐ beginner |
| [`loitering-detection`](examples/loitering-detection) | Dwell-time state machine on a NATS inference stream | ⭐⭐ intermediate |
| [`inference-listener`](examples/inference-listener) | Minimal NATS subscriber template | ⭐ beginner |
| [`alerts-subscriber`](examples/alerts-subscriber) | Fan-out alerts to webhooks / logs / your tooling | ⭐ beginner |
| [`license-plate-recognition`](examples/license-plate-recognition) | Watch the driveway, log every plate, route allow/deny lists | ⭐⭐ intermediate |
| [`smart-doorbell`](examples/smart-doorbell) | Family / known / unknown at the front door with REST enrollment | ⭐⭐ intermediate |
| [`package-delivery`](examples/package-delivery) | Porch arrival / linger / pickup with porch-pirate severity routing | ⭐⭐ intermediate |
| [`camera-agent`](examples/camera-agent) | **Ask your cameras out loud.** Voice agent grounded in live feeds via tool calling | ⭐⭐⭐ advanced |
| [`home-assistant-relay`](examples/home-assistant-relay) | Bridge OpenNVR alerts into Home Assistant via MQTT discovery | ⭐⭐ intermediate |

Each example ships with a `config.example.yml`, a `README.md`, and a focused
test suite you can read in 5 minutes. The full v0.1 gallery — including
the axis-grid that groups examples by *drives-inference* vs *subscribes-to-events* —
is at [`examples/README.md`](examples/README.md).

---

## 🤝 Contributing

We want your help. Whether it's a typo, a new adapter, or a whole example app, the
flow is the same:

1. **Fork** the repo on GitHub.
2. **Branch** off `main` — `feature/<short-name>` or `fix/<short-name>`.
3. **Write tests.** Every behavior change needs a test. We block PRs without them.
4. **Run the suite locally** — `pytest` in each of `server/`, `kai-c/`, and the
   `examples/*/` you touched. Should be green before you push.
5. **Open the PR** against `main`. Fill out the template; it's short on purpose.

Full guidelines, coding standards, commit-message format, and the security
disclosure process: [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`SECURITY.md`](SECURITY.md).

**First-time contributors:** look for issues tagged `good first issue` on the issue
tracker, or fork any of the example apps above — copying one and replacing the
predicate (zone check, dwell-time state machine, plate-watchlist filter, …) is
the cheapest path to a real PR. The roadmap section in
[`examples/README.md`](examples/README.md) lists the adapter / example combinations
the community has asked for next.

---

## 💬 Community

- **GitHub Discussions** — questions, show & tell, feature ideas
- **Issue tracker** — bugs only, please. Use Discussions for questions.

If OpenNVR saves you a weekend, give us a ⭐ on GitHub — it's the cheapest way to help
other developers find the project.

---

## 🔐 Reporting a vulnerability

**Please don't open a public issue for security bugs.** Use GitHub's "Report
a vulnerability" feature on this repo, or follow the disclosure process in
[`SECURITY.md`](SECURITY.md). Threat model and control mapping are in
[`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md).

---

## 📚 Documentation

**Getting started**

- [Docker Quickstart](DOCKER_QUICKSTART.md) — the recommended install path
- [User Manual](USER_MANUAL.md) — using the web interface
- [Local Setup](docs/LOCAL_SETUP.md) — bare-metal developer setup
- [Use Cases by Industry](docs/USE_CASES.md) — does OpenNVR fit your environment?
- [Comparisons](docs/COMPARISONS.md) — honest evaluation vs Frigate / ZoneMinder / Verkada / Viseron / Shinobi

**Architecture & security**

- [Security Architecture](docs/SECURITY_ARCHITECTURE.md) — threat model, V-* control inventory
- [Compliance Mapping](docs/COMPLIANCE.md) — paper §3 → §4 → code, plus framework alignment
- [Government Deployment Brief](docs/GOVERNMENT_DEPLOYMENT.md) — procurement one-pager + operational sovereignty
- [AI Adapter Contract](docs/AI_ADAPTER_CONTRACT.md) — the wire spec for adapter authors

**Project**

- [Roadmap](docs/ROADMAP.md) — what's shipped, what's coming
- [Support](docs/SUPPORT.md) — community channels and commercial-support paths
- [Changelog](CHANGELOG.md) — what's new, version by version
- [Contributing](CONTRIBUTING.md) — PR flow and coding standards

---

## ⚖️ License

OpenNVR is licensed under the **GNU Affero General Public License v3.0** (AGPL v3).
The AGPL is intentional: it ensures the sovereignty story stays intact — any
service built on OpenNVR, even one offered over a network, must share its
modifications openly.

See [`LICENSE`](LICENSE) for the full terms.

> For enterprise commercial licensing, custom deployment support, or corporate
> sponsorships, reach out at **[contact@cryptovoip.in](mailto:contact@cryptovoip.in)**.

---

<div align="center">

**Star us ⭐ · [Try the quickstart](#-quick-start-under-5-minutes) · [Read the contract](docs/AI_ADAPTER_CONTRACT.md) · [Join the community](#-community)**

</div>

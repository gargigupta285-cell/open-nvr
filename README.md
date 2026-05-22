<div align="center">

# OpenNVR

### Self-hosted, AI-powered video surveillance — secure by default, sovereign by design, yours to extend.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**OpenNVR** is an open-source network video recorder with a pluggable AI adapter ecosystem,
offline-first network posture, and an end-to-end audit trail from camera to alert.
Bring your own model. Own your footage. Deploy anywhere.

[Quick start](#-quick-start-3-commands) · [Why OpenNVR](#-why-opennvr) · [Examples](#-examples) · [Architecture](docs/SECURITY_ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## ⚡ Quick start (3 commands)

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
./start.sh        # Linux / macOS   (Windows: .\start.ps1)
```

Open <http://localhost:8000>, paste the one-time setup token printed in the terminal,
choose an admin password, and add your first camera.
AI detection is **off by default for safety** — set `AI_ENABLED=true` in your `.env`
and restart (`./start.sh build`) when you're ready.

Every security feature ships **on by default**. You don't configure security — you configure exceptions.

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
| Cloud egress | On by default | **403 unless `DEPLOYMENT_MODE` is flipped; audit-logged** |
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

### Step-by-step

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
./start.sh                    # Linux / macOS
# Windows PowerShell:
# .\start.ps1
```

The smart launcher detects your OS, runs the interactive installer on first boot, and
just validates-and-starts on every subsequent run. On first boot it will:

1. Check prerequisites (Docker, Compose, Git).
2. Ask for recording-storage path and initialization options.
3. Generate cryptographically random secrets and write `.env` (read by `docker compose`).
4. Generate self-signed TLS certs for MediaMTX.
5. Build images and start the stack.

When the wizard prints the **first-time setup token banner**, copy the token,
open <http://localhost:8000>, paste it on the setup form, and choose an admin password.

Subsequent restarts skip the setup-token flow.

**Endpoints:**

| Service | URL |
|---|---|
| Web UI | <http://localhost:8000> |
| API docs (OpenAPI) | <http://localhost:8000/docs> |
| MediaMTX | <http://localhost:8889> |
| AI Adapter (when `AI_ENABLED=true`) | <http://localhost:9100> |

### Stopping / restarting

```bash
./start.sh down       # stop everything
./start.sh status     # show container health
./start.sh logs       # tail logs
./start.sh build      # rebuild images and start
```

> Prefer doing it by hand? The smart launcher is just `install.sh` + `docker compose up -d`.
> Look in `scripts/install.sh` to see exactly what gets written. For bare-metal
> development (no Docker), see the next section.

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
        frame_bytes = payload["__file__"]
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

Every example is a copy-as-template starting point — minimal, readable, opinionated.

| Example | What you'll build | Difficulty |
|---|---|---|
| [`intrusion-detection`](examples/intrusion-detection) | Detect people in restricted zones during restricted hours | ⭐ beginner |
| [`loitering-detection`](examples/loitering-detection) | NATS subscriber with a dwell-time state machine | ⭐⭐ intermediate |
| [`inference-listener`](examples/inference-listener) | Minimal NATS subscriber template | ⭐ beginner |
| [`alerts-subscriber`](examples/alerts-subscriber) | Fan-out alerts to webhooks / logs / your tooling | ⭐ beginner |
| 🚧 `license-plate-recognition` | Detect + OCR plates on driveway / parking — *coming v0.1* | ⭐⭐ intermediate |
| 🚧 `smart-doorbell` | Recognise family vs strangers + Telegram alert — *coming v0.1* | ⭐⭐ intermediate |
| 🚧 `package-delivery` | Porch arrival / departure with duration — *coming v0.1* | ⭐⭐ intermediate |
| 🚧 `home-assistant-relay` | Bridge OpenNVR alerts into Home Assistant — *coming v0.1* | ⭐⭐ intermediate |

Each example ships with a `config.example.yml`, a `README.md`, and a test suite you
can read in 5 minutes.

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
tracker, or pick any of the 🚧 examples in the table above — those are a great
on-ramp.

---

## 💬 Community

- **GitHub Discussions** — questions, show & tell, feature ideas
- **Discord** — coming with the v0.1 launch — real-time help, adapter authoring, homelab tips
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

- [User Manual](USER_MANUAL.md) — using the web interface
- [Docker Quickstart](DOCKER_QUICKSTART.md) — the recommended install path
- [Local Setup](docs/LOCAL_SETUP.md) — bare-metal developer setup
- [Security Architecture](docs/SECURITY_ARCHITECTURE.md) — threat model, controls, roadmap
- [AI Adapter Contract](docs/AI_ADAPTER_CONTRACT.md) — the wire spec for adapter authors
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

**Star us ⭐ · [Try the quickstart](#-quick-start-3-commands) · [Read the contract](docs/AI_ADAPTER_CONTRACT.md) · [Join the community](#-community)**

</div>

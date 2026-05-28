# GitHub Release Notes — v0.1.0

This is the body that goes into the GitHub release page when the
`v0.1.0` tag is pushed. Copy from the rule below into the release
body. Don't ship this internal-doc wrapper.

> **Why the link verifier flags this file as broken.** The release
> bodies below are inside a blockquote because they're paste-targets,
> not in-doc content. Their links (`[README.md](README.md)`,
> `[docs/COMPLIANCE.md](docs/COMPLIANCE.md)`, etc.) resolve from the
> *repo root* when pasted into the GitHub release page — that's
> GitHub's link-resolution behaviour for release bodies, not a bug.
> A filesystem-relative verifier sees them as broken because they
> don't resolve from `docs/internal/`, but they're correct for the
> paste target. Don't "fix" them to use `../../` paths — that breaks
> the paste.

Two releases happen at the v0.1.0 tag:

1. **open-nvr/open-nvr v0.1.0** — the NVR server + KAI-C + examples.
2. **open-nvr/ai-adapter v0.1.0** — the SDK + reference adapters + adapter template.

Each gets its own release body below. Tag the SDK with `sdk-v0.1.0`
(per `publish-sdk.yml`) if you want a synchronised PyPI publish — that
runs from the same commit but on a different ref pattern.

---

## open-nvr/open-nvr v0.1.0 release body

> **OpenNVR v0.1.0 — the self-hosted NVR you can talk to.**
>
> First public release. Built on the offline-first three-tier security
> architecture published in *Eliminating Systemic IP Camera
> Vulnerabilities via Offline-First Open Security Architecture*
> ([Singh et al., 2025 — DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761)).
>
> Object detection, license-plate OCR, face recognition, scene
> captioning, multi-object tracking — and a voice agent that grounds
> its answers in live camera feeds. All running on your hardware. No
> cloud calls by default. Pluggable AI adapter contract. AGPL.
>
> ---
>
> ## 5-minute install
>
> Pre-built images on GHCR — no source build, no toolchain, no manual model downloads.
>
> ```bash
> git clone https://github.com/open-nvr/open-nvr.git
> cd open-nvr
> cp .env.example .env
> ./scripts/generate-secrets.sh --write
> docker compose -f docker-compose.tier0.yml up -d
> ```
>
> Open <http://localhost:8000>, paste the one-time setup token from
> the terminal logs (`docker compose -f docker-compose.tier0.yml logs
> opennvr-core | grep -i 'setup token'`), choose an admin password,
> add your first camera. YOLOv8 object detection runs on every frame
> from the moment the camera connects.
>
> Voice control? Layer the camera-agent on top:
>
> ```bash
> docker compose -f docker-compose.tier0.yml \
>                -f docker-compose.camera-agent.yml \
>                --profile camera-agent run --rm ollama-model-pull
> docker compose -f docker-compose.tier0.yml \
>                -f docker-compose.camera-agent.yml \
>                --profile camera-agent up -d
> ```
>
> Open <http://localhost:9100/demo>, click "Start", and ask
> *"is there a person at the front door?"*
>
> ---
>
> ## What's new in v0.1.0
>
> Everything is new — this is the first release. Highlights:
>
> ### Architecture
> - Three-tier offline-first deployment model. Isolated camera network → middleware gateway → analytics. The paper's reference implementation.
> - Strong-secret validator refuses to boot on placeholder credentials. One-time setup token for first-boot admin provisioning. No shipped default password.
> - RTSPS / HLS-TLS / WebRTC-TLS on every operator-facing transport. Plaintext loopback for the in-host KAI-C inference tap — eliminates per-frame TLS overhead on the same-kernel hop while keeping external surfaces TLS-protected.
> - End-to-end `X-Correlation-Id` propagation from alert → middleware → adapter.
> - sha256 model-fingerprint polled every 60s; drift surfaces as `adapter.fingerprint_mismatch` audit events.
> - Append-only audit log with public NATS subject scheme for SIEM / custom dashboards.
> - Default-deny posture for anything outbound: `DEPLOYMENT_MODE=offline` (default) gates cloud-touching routes — they return HTTP 403 until the operator switches to `hybrid` or `cloud`, which is audit-logged. `AI_SOVEREIGNTY=local_only` (default) is a separate gate that refuses AI adapters declaring `network_egress` until switched to `federated` or `cloud_allowed`.
>
> ### AI capabilities (shipped adapters, contract v1)
> - **YOLOv8** object detection (ONNX, CPU + GPU, WebSocket streaming).
> - **InsightFace** face detection + recognition with REST-based face DB enrollment.
> - **Whisper** ASR via faster-whisper (CPU + GPU).
> - **Piper** TTS with inline audio response.
> - **fast-plate-ocr** license-plate recognition.
> - **BLIP** scene captioning.
> - **ByteTrack** multi-object tracking — first post-processor adapter.
> - **Ollama** integration with OpenAI-style tool-calling.
>
> ### Example apps
> Nine first-party copy-as-template apps: `intrusion-detection`, `loitering-detection`, `inference-listener`, `alerts-subscriber`, `license-plate-recognition`, `smart-doorbell`, `package-delivery`, `camera-agent`, `home-assistant-relay`.
>
> ### Developer surface
> - **`opennvr-adapter-sdk`** — Apache-2.0 SDK that adapter authors install. ~30 lines of FastAPI per adapter. PyPI publish wires off the first `sdk-v*` tag.
> - **Adapter template scaffold** (`./templates/adapter-template/scaffold.sh` in the ai-adapter repo) — one command generates a working contract-compliant adapter directory.
> - **Conformance test suite** that proves an adapter will register cleanly with KAI-C.
> - **Pre-built container images** on GHCR for the core + all seven shipped adapters.
>
> ### Documentation
> - [README](README.md) — three-audience hero (homelab / procurement / defence) + 5-minute install.
> - [COMPLIANCE.md](docs/COMPLIANCE.md) — paper § → control → code mapping plus framework alignment table.
> - [GOVERNMENT_DEPLOYMENT.md](docs/GOVERNMENT_DEPLOYMENT.md) — procurement one-pager with operational-sovereignty section.
> - [USE_CASES.md](docs/USE_CASES.md) — per-industry fit guide for 12 segments.
> - [COMPARISONS.md](docs/COMPARISONS.md) — honest head-to-head with Frigate / ZoneMinder / Shinobi / Viseron / Verkada.
> - [ROADMAP.md](docs/ROADMAP.md) — what's shipped vs what's coming.
> - [SUPPORT.md](docs/SUPPORT.md) — community vs commercial paths.
> - [SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md) — full V-* control inventory.
> - [SECURITY.md](SECURITY.md) — disclosure timeline + operator checklist.
>
> ---
>
> ## Migration notes
>
> First release — nothing to migrate from. For contributors who've
> been running pre-release builds, the breaking changes between the
> `arch-rev` development branch and `v0.1.0` are captured in the
> [CHANGELOG](CHANGELOG.md).
>
> ---
>
> ## Compatibility
>
> - **OS:** Linux (Ubuntu 22.04+ LTS recommended), macOS 14+, Windows
>   10/11 via Docker Desktop.
> - **Architecture:** linux/amd64. linux/arm64 (Raspberry Pi 5) on
>   the v0.2 roadmap.
> - **Python (for bare-metal dev):** 3.11+.
> - **Docker:** Engine 24+ with Compose v2.
> - **Cameras:** ONVIF Profile S/T or any RTSP/RTSPS source.
>
> ---
>
> ## Known limitations (documented)
>
> Honest about what doesn't ship in v0.1:
>
> - **Camera-agent overlay** currently `build:`-from-source for the
>   monolithic ai-adapter and the camera-agent service. v0.1.1 will
>   publish those as separate GHCR images so the overlay becomes a
>   pure `docker compose pull`.
> - **Multi-host deployments** work but the federation pattern isn't
>   documented end-to-end. v0.2 will land the multi-host story.
> - **`MEDIAMTX_PATH_MODE=ip`** disables the inference loopback tap
>   (the optimization works only under `path_mode=id`, which is the
>   default). Inference still works via direct camera RTSP in `ip`
>   mode.
> - **arm64 / Raspberry Pi 5 native images** not yet published —
>   build from source on arm64 hosts works fine.
>
> See [ROADMAP.md](docs/ROADMAP.md) for v0.1.x patch fixes and v0.2 / v0.3 themes.
>
> ---
>
> ## Acknowledgements
>
> The architecture is described in *Eliminating Systemic IP Camera
> Vulnerabilities via Offline-First Open Security Architecture* by
> Varun Pratap Singh, Suraj Raj Bhandari, Arjun Singh, Rajani
> Kushwaha, and Sahaj Kaura (DOI 10.5281/zenodo.17261761). The paper's
> 34 references include CISA advisories, NIST publications, ETSI EN
> 303 645, ISO/IEC 27001, real CVEs across Hikvision / Dahua / Uniview /
> Edimax / ThroughTek, the 2021 Verkada breach, and the Mirai / Persirai
> botnet campaigns — all gratefully cited.
>
> ---
>
> ## Verify the release
>
> Container image digests for this release:
>
> ```
> ghcr.io/open-nvr/core:0.1.0
> ghcr.io/open-nvr/yolov8-adapter:0.1.0
> ghcr.io/open-nvr/piper-adapter:0.1.0
> ghcr.io/open-nvr/whisper-adapter:0.1.0
> ghcr.io/open-nvr/fast-plate-ocr-adapter:0.1.0
> ghcr.io/open-nvr/insightface-adapter:0.1.0
> ghcr.io/open-nvr/blip-adapter:0.1.0
> ghcr.io/open-nvr/bytetrack-adapter:0.1.0
> ```
>
> Pull, verify, run.
>
> ---
>
> ## License
>
> OpenNVR is licensed under **AGPLv3**. The `opennvr-adapter-sdk` is
> licensed under **Apache-2.0** so adapter authors can publish under
> any compatible license, including proprietary.
>
> Commercial licensing, custom deployment support, compliance
> evidence packs, and sponsored development:
> [contact@cryptovoip.in](mailto:contact@cryptovoip.in).

---

## open-nvr/ai-adapter v0.1.0 release body

> **OpenNVR AI Adapter v0.1.0 — open contract + reference adapters.**
>
> Companion release to [OpenNVR v0.1.0](https://github.com/open-nvr/open-nvr/releases/tag/v0.1.0).
> This repo holds two things:
>
> 1. The **`opennvr-adapter-sdk`** — Apache-2.0 SDK that adapter authors
>    install to ship a contract-compliant AI adapter in ~30 lines of
>    FastAPI.
> 2. Seven **reference adapters** that demonstrate every body shape and
>    streaming mode the AI Adapter Contract v1 supports.
>
> ---
>
> ## Quick start
>
> Pull any of the pre-built per-adapter images from GHCR:
>
> ```bash
> docker pull ghcr.io/open-nvr/yolov8-adapter:0.1.0
> docker pull ghcr.io/open-nvr/whisper-adapter:0.1.0
> docker pull ghcr.io/open-nvr/piper-adapter:0.1.0
> docker pull ghcr.io/open-nvr/fast-plate-ocr-adapter:0.1.0
> docker pull ghcr.io/open-nvr/insightface-adapter:0.1.0
> docker pull ghcr.io/open-nvr/blip-adapter:0.1.0
> docker pull ghcr.io/open-nvr/bytetrack-adapter:0.1.0
> ```
>
> ---
>
> ## What's new in v0.1.0
>
> First release. The adapter ecosystem is the differentiator — Frigate
> and similar treat AI as a configuration of detection weights;
> OpenNVR treats AI as a published wire contract any model can
> implement against.
>
> ### Shipped reference adapters
> | Adapter | BodyShape | Streaming | Notes |
> |---|---|---|---|
> | yolov8 | IMAGE | WS protocol | ONNX, CPU + GPU |
> | piper | TEXT | — | TTS, inline `audio_b64` |
> | whisper | AUDIO | — | faster-whisper, multipart audio |
> | fast-plate-ocr | IMAGE | — | LPR, ONNX, CPU-only |
> | insightface | IMAGE | — | Face DB enrollment endpoints |
> | blip | IMAGE | — | Scene captioning, transformer |
> | bytetrack | TEXT | — | Post-processor; per-camera state |
>
> ### Adapter template
>
> First-class scaffolding for new adapter authors. One command
> generates a working contract-compliant adapter directory:
>
> ```bash
> ./templates/adapter-template/scaffold.sh <slug> <port> <body-shape>
> ```
>
> The skeleton boots out of the box, the generated test scaffold
> passes the seven lifecycle assertions, and the placeholder `TODO`
> markers are explicit about which methods need real model
> integration. See `templates/adapter-template/README.md`.
>
> ### `opennvr-adapter-sdk`
>
> Apache-2.0 SDK published to PyPI from the first `sdk-v*` tag. Public
> API:
> - `AdapterService` — ABC every adapter implements (~4 abstract methods).
> - `AdapterApp` — FastAPI builder; six contract endpoints + auth + correlation_id + metrics auto-wired.
> - `ServiceError` — typed §7 failure envelope.
> - `BodyShape` — enum for input shape negotiation.
> - Every contract Pydantic type re-exported.
>
> ---
>
> ## Compatibility
>
> - **SDK:** Python 3.10+ (`opennvr-adapter-sdk`); 3.11+ for the reference
>   adapter Dockerfiles.
> - **Architecture:** linux/amd64. arm64 on v0.2 roadmap.
> - **Contract version:** v1. Supported through at least v0.4 per
>   ROADMAP.
>
> ---
>
> ## Known limitations
>
> - **WebSocket streaming** (§6 protocol) is implemented in the YOLOv8
>   adapter only; other adapters return 501 as advertised in their
>   `/capabilities` response.
> - **arm64 native images** not yet published — build from source
>   works.
> - **Shared-memory fast path** (§6.2) is deferred; all WS adapters
>   advertise `supports_shared_memory: false`.
>
> ---
>
> ## License
>
> Reference adapter server: **AGPLv3**.
> SDK: **Apache-2.0** — author adapters under any compatible licence
> including proprietary or classified.

---

## One-time package visibility setup

**Do this before the testing checklist runs**, not after — testers will
hit `unauthorized` when pulling otherwise.

GitHub creates GHCR packages as **private** by default on the first
push from a workflow. The default `GITHUB_TOKEN` can push but cannot
flip visibility, so this is a manual one-time step per package per
organisation.

### Recommended: GitHub CLI (one command per package)

```bash
# Make sure your gh auth has admin:packages scope first:
gh auth refresh -s admin:packages -h github.com

# open-nvr packages:
for pkg in core; do
  gh api --method PATCH \
    "/orgs/open-nvr/packages/container/$pkg/visibility" \
    -f visibility=public
done

# ai-adapter packages:
for pkg in yolov8-adapter piper-adapter whisper-adapter \
           fast-plate-ocr-adapter insightface-adapter \
           blip-adapter bytetrack-adapter; do
  gh api --method PATCH \
    "/orgs/open-nvr/packages/container/$pkg/visibility" \
    -f visibility=public
done
```

### Manual via web UI (if you don't have gh CLI set up)

For each of the eight packages (core + seven adapters):

1. Go to `https://github.com/orgs/open-nvr/packages/container/<package>/settings`.
2. Scroll to "Danger Zone" → "Change visibility".
3. Set to "Public", confirm.

Eight packages total — budget five minutes the first time.

### Verify

```bash
docker pull ghcr.io/open-nvr/core:latest
docker pull ghcr.io/open-nvr/yolov8-adapter:latest
# ...etc
```

Each should pull without an `unauthorized` error from an unauthenticated
terminal (run `docker logout ghcr.io` first if you've previously
auth'd, to confirm the public path).

This is a v0.1.1 candidate for automation via a PAT-authenticated
workflow step.

---

## Tagging workflow

Once package visibility is set, both release bodies are ready, and the
[`RELEASE_TESTING_CHECKLIST.md`](RELEASE_TESTING_CHECKLIST.md) sign-off
is complete:

### open-nvr/open-nvr
```bash
cd open-nvr
git checkout main
git pull
# (do any final CHANGELOG / README polish)
git tag -s v0.1.0 -m "OpenNVR v0.1.0"
git push origin v0.1.0
```
The `publish-images.yml` workflow fires on the tag push, building
and pushing `ghcr.io/open-nvr/core:0.1.0` (and matching minor / major
tags).

### open-nvr/ai-adapter
```bash
cd ai-adapter
git checkout main
git pull
# Adapter-server tag:
git tag -s v0.1.0 -m "OpenNVR AI Adapter v0.1.0"
git push origin v0.1.0

# SDK tag (kicks off the PyPI publish workflow):
git tag -s sdk-v0.1.0 -m "opennvr-adapter-sdk v0.1.0"
git push origin sdk-v0.1.0
```

`v0.1.0` triggers `publish-images.yml` (all seven adapter images).
`sdk-v0.1.0` triggers `publish-sdk.yml` (PyPI release + smoke matrix).

Then create the GitHub releases (one per repo) by pasting the bodies
above into the release page.

After the GHCR images and PyPI package are live, execute the launch
sequence per [`GTM_PLAN.md`](GTM_PLAN.md).

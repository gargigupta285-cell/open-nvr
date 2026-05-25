# Changelog

All notable changes to OpenNVR are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — targeting v0.1.0

First public release. The architecture has been redesigned around three
principles: secure by default, sovereign by design, and pluggable by contract.

### Added

#### Architecture

- **Pluggable AI adapter ecosystem.** Any model behind a REST or WebSocket
  endpoint becomes a first-class detector via the AI Adapter Contract v1.
  YOLOv8 object detection, InsightFace recognition, Whisper ASR, and Piper TTS
  ship as reference adapters. Wire spec lives in
  [`docs/AI_ADAPTER_CONTRACT.md`](docs/AI_ADAPTER_CONTRACT.md).
- **`opennvr-adapter-sdk`.** Apache-2.0 SDK that adapter authors install to
  write a new detector in ~30 lines of code. Lives in the
  `open-nvr/ai-adapter` repository; PyPI publish wires off the first
  `sdk-v*` tag — until then, install from source.
- **NATS event bus.** Inference results and alerts publish to
  `opennvr.inference.*` and `opennvr.alerts.*` subjects. Build downstream
  applications with the copy-as-template subscriber pattern.
- **KAI-C middleware.** Sits between the OpenNVR server and adapter containers,
  enforcing sovereignty, recording the audit chain, and proxying HTTP and
  WebSocket inference calls.
- **End-to-end correlation ID.** Every inference carries an
  `X-Correlation-Id` joined from alert → middleware → adapter line, so an
  operator investigating "why did this alert fire at 22:14?" never has to
  guess.
- **Model fingerprint drift detection.** sha256 of every loaded model is polled
  every 60 seconds. Drift surfaces as an `adapter.fingerprint_mismatch` audit
  event — never silence.
- **Append-only audit log.** Adapter registration, refusal, drift, inference,
  and sovereignty violations are all recorded with reason codes that grep
  cleanly.

#### Examples

- `intrusion-detection` — detect people in restricted zones during restricted
  hours, with HTTP and WebSocket transports.
- `loitering-detection` — NATS subscriber with a dwell-time state machine.
- `inference-listener` — minimal NATS subscriber template for community
  contributors.
- `alerts-subscriber` — fan-out alerts to webhooks, logs, or your own tooling.
- `license-plate-recognition` — drives YOLOv8 + the fast-plate-ocr adapter
  via KAI-C in a two-stage chain, with allowlist / denylist watchlist
  routing and Pillow-based plate cropping.
- `smart-doorbell` — drives the InsightFace adapter via KAI-C, classifies
  visitors into family / known / unknown with severity routing, and embeds
  a base64 JPEG snapshot of unknown faces directly in the alert envelope
  so downstream relays (a ~15-line Telegram / ntfy / Discord bridge — see
  `alerts-subscriber/` for the template) can post the photo with the
  notification. Pure-REST enrollment flow — `python smart_doorbell.py
  enroll --image alice.jpg ...` works from any machine that can reach the
  adapter, no shared volume, no desktop GUI.
- `package-delivery` — drives YOLOv8 against a porch ROI, threads detections
  with a per-camera IoU tracker, and runs a state machine that distinguishes
  arrival, optional linger, and disappearance. The disappearance event
  routes "owner pickup" vs "porch pirate" by whether a person was sighted
  in the ROI during a configurable lookback window — info vs high severity.
  The whole point is the state machine: copy this folder and replace the
  predicate to ship "car arrived and stayed", "dog left the yard", "shed
  door open longer than X" without rewriting the alert plumbing.
- `home-assistant-relay` — a NATS subscriber that bridges
  `opennvr.alerts.>` into Home Assistant entities via MQTT discovery
  (recommended — HA auto-creates the entities on the first fire with
  the right device_class, friendly name, and device card) or HA's
  REST `/api/states` endpoint. Built-in mapping rules cover every
  shipped OpenNVR producer-side example (smart-doorbell,
  package-delivery, intrusion-detection, loitering-detection,
  license-plate-recognition); operators override per source and per
  camera via the `mappings:` config block. Binary sensors hold ON
  for a configurable window then auto-flip OFF so HA automations
  read the alert as an event, not a sticky alarm. Closes the loop:
  OpenNVR fires alerts → HA dashboards and automations consume them
  with zero extra wiring beyond the standard HA UI.
- `camera-agent` — a voice agent that grounds its answers in live
  OpenNVR camera feeds via tool calling. Pipecat pipeline with Silero
  VAD on a WebSocket transport; custom Pipecat services wrap the
  Whisper / Ollama / Piper adapters for the streaming voice path, and
  four registered tools (BLIP scene caption + YOLOv8 detection +
  InsightFace recognition + NATS event history) hit KAI-C for the
  auditable inference. All CPU-runnable; default model `llama3.2:3b`
  is roughly 3 GB RAM and 5-15 tok/s on a modern CPU. The first
  OpenNVR example where cameras have agency, not just data — operators
  ask "what's on the front porch?", the agent runs the right tool
  against the right camera and answers in voice. Ships in v0.1 with
  the three v0.1 integration gaps closed: the BLIP SDK adapter is
  now live alongside InsightFace + YOLOv8; the Piper SDK adapter
  supports inline ``audio_b64`` responses so the camera-agent's
  Pipecat TTS service gets audio bytes back over plain HTTP; and a
  camera-agent-local raw-PCM WebSocket serializer lets the
  self-contained ``/demo`` page work with vanilla JS + AudioContext
  without bundling Pipecat's JS client. 52 unit tests cover the
  config loader, frame cache, event ring, tool dispatch, and the
  raw-PCM serializer.

Each example ships with a `config.example.yml`, a `README.md`, and a focused
test suite designed to be read in five minutes.

#### Installation

- **Interactive installer.** `./start.sh` (Linux / macOS) and `.\start.ps1`
  (Windows) detect your OS, generate cryptographically random secrets, create
  self-signed TLS certificates for MediaMTX on first boot, build the images,
  and bring the stack up.
- **`make secrets` / `make secrets-env` / `make check-secrets`** for
  bare-metal developer workflows.
- **Reusable React frontend** with a first-time-setup flow that gates admin
  activation on a one-time token.

#### Documentation

- [`docs/AI_ADAPTER_CONTRACT.md`](docs/AI_ADAPTER_CONTRACT.md) — wire spec for
  adapter authors.
- [`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md) — threat
  model, control mapping, and the academic paper that informs the architecture
  ([Zenodo DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761)).
- [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) — bare-metal developer setup.
- [`docs/DOCKER_SETUP.md`](docs/DOCKER_SETUP.md) — Docker-only path.
- Per-example `README.md` files documenting each reference application.

### Security

OpenNVR was rebuilt to close every systemic IP-camera weakness documented in
recent academic work on networked surveillance. The defaults are deliberately
strict; every relaxation is an explicit operator decision recorded in the
audit log.

- **No shipped default password.** The admin account activates via a one-time
  setup token printed to stdout on first boot. The token is consumed on first
  successful use; a fresh one is minted on every restart that finds a pending
  user.
- **Strong-secret validators.** The server refuses to boot if `SECRET_KEY`,
  `MEDIAMTX_SECRET`, `INTERNAL_API_KEY`, or `CREDENTIAL_ENCRYPTION_KEY` is
  shorter than 32 characters or matches a placeholder pattern.
- **Camera credentials encrypted at rest** under a Fernet key the validator
  refuses to accept if it's a placeholder.
- **Offline mode by default.** Cloud-touching routes (cloud recording, cloud
  AI inference, federated streams) return 403 unless `DEPLOYMENT_MODE` is
  explicitly set to `hybrid` or `cloud`. The deviation is audit-logged at
  boot and surfaced at `/api/v1/system/posture`.
- **AI sovereignty enforcement.** KAI-C refuses to register adapters that
  declare `network_egress` permissions under the default
  `AI_SOVEREIGNTY=local_only` policy.
- **MediaMTX bound to loopback by default.** Cloud-style `0.0.0.0` binds are
  rejected by the boot validator.
- **Path-traversal hardening** on recording storage. `..` and absolute paths
  are refused server-side; symlinks resolve within the configured root.
- **RTSPS, HLS-TLS, and WebRTC-TLS on by default.** MediaMTX refuses to start
  without `server.crt` / `server.key`; the bundled `mediamtx-certs-init`
  service generates a 10-year self-signed pair on first boot if none exists.
  Plaintext outputs require `MEDIAMTX_ALLOW_PLAINTEXT_OUTPUTS=true`, which is
  recorded in the boot audit log.
- **Per-camera transport-security policy.** Each camera carries a
  `transport_security` field — `rtsps_required` / `rtsps_preferred` /
  `plaintext_allowed`. RTSPS reachability is probed on add; operators can
  override per-camera. Stream provisioning refuses plaintext for
  `rtsps_required` cameras across every code path that touches stream config.
- **Account lockout** after repeated failed logins, with a clear feedback
  message and a 180-second cool-down.

### Changed

- Migrated FastAPI lifecycle from the deprecated `@app.on_event("startup")` /
  `("shutdown")` decorators to the lifespan async context manager pattern.
  Same behaviour; no more deprecation warnings in test runs.

### License

OpenNVR is licensed under [GNU Affero General Public License v3.0](LICENSE).
The AGPL is intentional: it ensures the sovereignty story stays intact — any
service built on OpenNVR, even one offered over a network, must share its
modifications openly. Commercial licensing is available — see the contact
address in the README.

---

[Unreleased]: https://github.com/open-nvr/open-nvr/compare/...HEAD

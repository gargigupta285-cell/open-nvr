# OpenNVR for Government & Public-Sector Deployments

*A printable one-pager for IT decision-makers, security officers, and
compliance leads.*

## The problem

Many IP cameras currently deployed in government, defense, healthcare,
education, and critical-infrastructure environments fall into one of three
categories:

1. **FCC Covered List vendors.** Hikvision, Dahua, and certain Hytera /
   Hangzhou Hikvision Digital Technology equipment are restricted for
   public-safety, government-facility, and critical-infrastructure
   surveillance use under the Secure Networks Act. ([FCC Covered List](https://www.fcc.gov/supplychain/coveredlist))
2. **End-of-life or unmaintained.** Devices running firmware that stopped
   receiving updates ~3 years after release, with unpatched high-severity
   CVEs still active in production networks.
3. **Vendor-cloud-bound.** Cameras whose live feeds, recordings, or AI
   analytics flow through a vendor-managed cloud aggregation layer — the
   architecture that produced the 2021 Verkada breach, exposing ~150,000
   cameras across hospitals, schools, and enterprises in a single
   credential compromise.

Procurement officers need a path that mitigates exposure **without ripping
out the camera fleet itself.**

## The substitution

OpenNVR is a self-hosted middleware layer that sits between your cameras
and everything downstream. It:

- **Works with any ONVIF or RTSP camera** you already own. No vendor lock-in.
- **Re-streams over TLS** through a hardened software gateway you control
  (MediaMTX with RTSPS, HLS-TLS, WebRTC-TLS by default).
- **Records, plays back, and serves the web UI** entirely on your hardware.
  No cloud egress unless you explicitly opt in — and that opt-in lands in
  an audit log.
- **Runs AI workloads locally.** Object detection, license-plate OCR, face
  recognition, scene captioning, multi-object tracking, voice query — all
  via the open AI Adapter Contract v1, all on your hardware, all
  customer-controlled.

The camera vendor's flaws stop at the camera. Everything past that point is
software you own, audit, and patch on your own schedule.

## Why it's defensible

OpenNVR's architecture is described in a **peer-citable published paper**:

> *Eliminating Systemic IP Camera Vulnerabilities via Offline-First Open
> Security Architecture* — Singh, Bhandari, Singh, Kushwaha, Kaura (2025).
> [DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761)

The paper synthesizes 34 authoritative sources — CISA advisories
(AVTECH, Edimax), NVD CVE records (Hikvision CVE-2021-36260, Dahua
CVE-2022-30563, Uniview CVE-2023-0773, Edimax CVE-2025-1316, ThroughTek
Kalay SDK CVE-2021-28372, iLnkP2P CVE-2019-11219/11220), the 2021 Verkada
breach, the Mirai / Persirai botnet campaigns, and academic measurement
studies — into a six-category framing of systemic IP-camera weaknesses
and a three-tier offline-first architecture that structurally eliminates
each. OpenNVR is that architecture's open-source reference
implementation. Every paper § maps to OpenNVR code; the mapping is at
[`docs/COMPLIANCE.md`](COMPLIANCE.md).

## What it aligns with

| Framework | What it covers |
|---|---|
| **CISA Secure-by-Design** | Secure defaults, customer-managed cryptography, minimized attack surface. |
| **NIST CSF 2.0** | Identify / Protect / Detect / Respond / Recover — full lifecycle. |
| **NIST AI RMF 1.0** | AI sovereignty enforcement at the adapter contract layer. |
| **ISO/IEC 27001:2022** | Certificate-based auth, RBAC, append-only audit log, ISMS evidence. |
| **ETSI EN 303 645** | Consumer-IoT baseline: no default passwords, secure update path, encrypted comms. |
| **GDPR (EU)** | Customer-owned keys, customer-controlled retention, no vendor-cloud egress. |
| **DPDP Act 2023 (India)** | Same posture — local-only processing, operator-managed retention. |

The framework-by-framework evidence trail is in
[`docs/COMPLIANCE.md`](COMPLIANCE.md). For ISO 27001 / SOC 2 evidence packs,
the audit-chain quick reference at the bottom of that page maps each
auditor question to its answer in the OpenNVR audit log.

## Operational sovereignty: your AI, your tactics, your hardware

Camera security is half the story. The other half is **what runs on the
video.** For defence, critical-infrastructure, and government operators,
the AI analytics layer is where tactical doctrine lives — what you watch
for, how you weight signals, what triggers escalation, what an anomaly
looks like in *your* environment. That doctrine is operationally
sensitive. It is not something you want sitting in a vendor's cloud, on
a vendor's roadmap, or visible through a vendor's support team.

OpenNVR's adapter contract is the mechanism that puts the AI layer
under operator control the same way the recording and transport layers
already are. Every analytic capability — detection, recognition,
tracking, OCR, scene understanding, audio events, anomaly scoring — is
a contract-compliant container you run on your hardware. Bring a model
you've fine-tuned on your own deployment data. Bring a model you cannot
share with a vendor for classification reasons. Bring a model whose
inference behaviour you cannot disclose to anyone who didn't sign your
NDA. The contract is the interface; what's behind it is yours.

This matters concretely:

| Domain | What "your AI" looks like in practice |
|---|---|
| **Defence / military bases** | Perimeter intrusion classifiers trained on your specific terrain and threat signatures. Asset-tracking models tied to your inventory. Behaviour-anomaly detectors weighted for your operational tempo. None of which can leave the base. |
| **Critical infrastructure (power, water, telecom)** | Equipment-tamper detectors trained on your specific cabinet and substation imagery. Drone-detection models tuned to your local airspace baseline. Fence-line and yard-monitoring analytics that escalate to your SCADA stack, not a vendor's. |
| **Government facilities** | Visitor-flow analytics with retention policies your records office sets, not a vendor's TOS. Tailgating and access-deviation detection bound to your badge system. Package screening with item lists that change weekly without a vendor product cycle. |
| **Healthcare** | Fall and patient-deterioration models that respect HIPAA boundaries by never leaving the host. Restricted-area logic with site-specific rules (medication rooms, NICU corridors, behavioural-health units) that no vendor product covers. |
| **Education** | Weapon-detection models you can re-train as adversaries adapt. After-hours intrusion detection with school-specific schedules. Behavioural alerting with rules your safety committee — not a vendor — defines. |
| **Industrial / OT** | PPE compliance against site-specific rules. Hazardous-zone entry detection tied to your lockout-tagout system. Equipment-behaviour anomaly models trained on your normal operational baseline. |

What this transforms, in plain terms: tactical doctrine that today lives
in human-staffed control rooms, in standard operating procedures, and in
officer training becomes deployable AI that runs on every camera, 24×7,
under your roof. It evolves as your threat model evolves — not as a
vendor's roadmap permits. Iteration is days, not vendor-product
quarters, because the platform is yours.

**Why the contract matters, not just the models we ship today.** OpenNVR
ships YOLOv8, InsightFace, Whisper, Piper, fast-plate-ocr, BLIP, and
ByteTrack out of the box — useful demonstrations of the contract, not
the limit of it. The contract is what lets a uniformed-services
engineering team, a national lab, or a contracted SI add capabilities
under whatever licensing arrangement your programme requires.
Apache-2.0 SDK so your adapter can ship under any licence you choose,
including proprietary or classified. ~30 lines of Python per adapter
plus your model. Conformance test suite that proves the adapter will
register cleanly with KAI-C. Template scaffold (`templates/adapter-template/`
in the [ai-adapter repo](https://github.com/open-nvr/ai-adapter)) that
generates a working adapter directory from a single command.

The combination — camera-layer offline-first isolation, middleware that
you patch on your own cadence, AI capabilities you author and run
locally, audit chain that proves none of it touched a vendor cloud — is
what the paper means by "data and AI sovereignty." It is the difference
between buying a surveillance product and operating a surveillance
*capability*.

## What it does

- **Records:** every camera, configurable retention per camera, encrypted
  at rest under your `CREDENTIAL_ENCRYPTION_KEY`.
- **Plays back:** web UI at `https://your-host:8000`, timeline-indexed
  segments, MP4 export.
- **Detects:** YOLOv8 object detection out of the box; plug in any
  detector that speaks the AI Adapter Contract.
- **Recognizes:** InsightFace face recognition with operator-managed face DB
  (REST enrollment, no shared volume).
- **Reads plates:** fast-plate-ocr adapter for license-plate recognition.
- **Captions scenes:** BLIP scene-caption adapter (semantic context for
  audit log entries).
- **Tracks:** ByteTrack multi-object tracking with persistent IDs across
  frames.
- **Listens and speaks:** Whisper STT + Piper TTS + Ollama LLM = voice
  agent (`/demo` page). Ask out loud, "is there a person at the front
  door?" — get an answer grounded in a live frame.
- **Audits:** end-to-end correlation ID joining every alert to the model
  inference, the model weights' sha256, the operator who provisioned the
  camera, and the transport policy in effect.

## What it costs

- **Software:** AGPL v3 — no per-camera license fees, no per-seat fees,
  no cloud subscription.
- **Hardware:** runs on commodity x86 from Raspberry Pi-class to
  enterprise servers. Reuse what you have. Storage scaling is whatever
  retention you configure (1080p H.264 at 24×7 is roughly 25 GB / camera
  / week, much less with motion-triggered recording).
- **Commercial support and indemnification:** available through
  **[contact@cryptovoip.in](mailto:contact@cryptovoip.in)**. Includes
  deployment assistance, custom-adapter authoring, compliance evidence
  packs, and SLA-backed incident response for regulated environments.

## What it doesn't do

We're up-front about scope (paper §8):

- **Doesn't replace the cameras.** You keep your existing fleet. OpenNVR
  reduces what the camera vendor can compromise by isolating it behind a
  middleware layer you control. It does not patch device-level firmware
  CVEs — that remains the camera vendor's responsibility.
- **Doesn't provide hardware tamper detection.** Locked racks, port
  security, and tamper alarms are operator controls outside OpenNVR's
  scope.
- **Doesn't guarantee against insider threats.** A malicious actor with
  physical access to the camera VLAN could still exploit unpatched
  device flaws. Architectural isolation reduces the attack surface but
  doesn't eliminate it.
- **Doesn't defend against hardware supply-chain implants.** Undocumented
  SoC backdoors would bypass network isolation. No widespread evidence
  this exists in commercial cameras, but the paper notes it as a
  theoretical residual risk (§8.3).

These limitations are part of the architecture's defensibility — they're
documented, scoped, and verifiable.

## Getting started

Pilot deployment, single host:

```bash
git clone https://github.com/open-nvr/open-nvr.git && cd open-nvr
cp .env.example .env
./scripts/generate-secrets.sh --write
docker compose -f docker-compose.tier0.yml up -d
```

Five minutes later, browse to `https://localhost:8000` and add your
first camera. Pre-built container images are pulled from `ghcr.io/open-nvr/`
— no source build required.

For a production deployment: [Docker Quickstart](../DOCKER_QUICKSTART.md)
covers the production-hardening checklist (reverse proxy with real TLS
certs, retention sizing, backup strategy, audit-log forwarding). For
custom AI adapters: [`ai-adapter` repo](https://github.com/open-nvr/ai-adapter)
with the SDK + template scaffold.

## Contact

- **Technical questions:** [GitHub Discussions](https://github.com/open-nvr/open-nvr/discussions)
- **Security disclosures:** [GitHub Security Advisories](https://github.com/open-nvr/open-nvr/security/advisories) or `security@cryptovoip.in`
- **Procurement, commercial licensing, SLA support:** **[contact@cryptovoip.in](mailto:contact@cryptovoip.in)**

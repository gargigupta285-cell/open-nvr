# Launch Copy — Drafts

Pre-launch copy for the v0.1 release. Pick a slot in the schedule, edit the
[TODO]s, post. All copy is written for an external audience — no internal
slice IDs or dev-task vocabulary.

---

## 1. Hacker News — Show HN post

**Title:**

> Show HN: OpenNVR – Self-hosted NVR you can talk to (with an audit chain and a paper)

**Body:**

> Hi HN — author here.
>
> OpenNVR is an open-source NVR for IP cameras. There are several good ones
> already (Frigate, ZoneMinder, Viseron) — what we built differently centres
> on two things existing options don't fully address:
>
> **1. Architecturally audit-ready.** Every inference gets an
> `X-Correlation-Id` from alert → middleware → adapter. Model weights are
> sha256-fingerprinted and polled every 60s — drift surfaces as an
> `adapter.fingerprint_mismatch` audit event. No shipped default password
> (one-time setup token at first boot). Strong-secret validator refuses to
> start if `SECRET_KEY` etc. are placeholders. Two independent default-deny
> gates for anything outbound: `DEPLOYMENT_MODE=offline` (default) makes
> cloud routes return HTTP 403; `AI_SOVEREIGNTY=local_only` (default) refuses
> AI adapters declaring `network_egress`. Both flips are audit-logged.
> The threat model isn't ad-hoc: we published a paper this year
> describing a three-tier offline-first architecture for IP camera deployments,
> grounded in 34 references including CISA advisories, real CVEs (Hikvision
> CVE-2021-36260, Dahua CVE-2022-30563, Uniview CVE-2023-0773, Edimax
> CVE-2025-1316, ThroughTek Kalay SDK CVE-2021-28372), the 2021 Verkada
> aggregation-layer breach, NIST CSF 2.0, NIST AI RMF, ETSI EN 303 645, and
> the GDPR / DPDP regulatory framing. OpenNVR is the reference
> implementation. The paper-section → control → code mapping is in
> `docs/COMPLIANCE.md`.
>
> Paper: https://doi.org/10.5281/zenodo.17261761
>
> **2. Open AI adapter contract.** Any model behind a REST/WebSocket
> endpoint becomes a first-class capability. Object detection (YOLOv8),
> license-plate OCR (fast-plate-ocr), face recognition (InsightFace), scene
> captioning (BLIP), multi-object tracking (ByteTrack), ASR (Whisper), TTS
> (Piper), an LLM with OpenAI-style tool-calling (Ollama) — all wired
> through the same Apache-2.0 SDK (`pip install opennvr-adapter-sdk`, ~30
> lines of FastAPI per adapter). Frigate's plugin surface is "swap your
> detection model"; ours is "add any AI task." For defence,
> critical-infrastructure, and government operators, that gap matters
> beyond convenience — it lets tactical AI run on your hardware under
> your control: models you've fine-tuned, models you can't share with a
> vendor, analytics whose detection logic itself is operationally
> sensitive. The procurement-grade brief is in
> `docs/GOVERNMENT_DEPLOYMENT.md`.
>
> The killer demo is `camera-agent`: a voice loop on top of Whisper + Ollama
> + Piper, with BLIP + YOLOv8 + InsightFace tools registered for grounding.
> You ask out loud "is there a person at the front door?" and it answers
> from a live frame — the LLM calls the tools, the tools hit the adapters,
> the adapters hit the cameras, all on your hardware, no cloud calls.
>
> **5-minute install (pre-built images on GHCR):**
>
> ```
> git clone https://github.com/open-nvr/open-nvr.git && cd open-nvr
> cp .env.example .env && ./scripts/generate-secrets.sh --write
> docker compose -f docker-compose.tier0.yml up -d
> ```
>
> Add the camera-agent overlay for the voice path:
>
> ```
> docker compose -f docker-compose.tier0.yml \
>                -f docker-compose.camera-agent.yml \
>                --profile camera-agent run --rm ollama-model-pull
> docker compose -f docker-compose.tier0.yml \
>                -f docker-compose.camera-agent.yml \
>                --profile camera-agent up -d
> ```
>
> Open `http://localhost:9100/demo`, click "Start", speak.
>
> **Honest about gaps.** Frigate has 5 years of polish, deeper Home
> Assistant integration, and better HEVC. Where we beat them is the audit
> chain, security defaults (Frigate's recommended deployment is HTTP basic
> auth), and the adapter contract breadth. ZoneMinder has broader camera
> support but predates most of modern AI surveillance. We're not displacing
> anyone — we're proposing a different category for the people whose
> compliance officer or threat model needs the paper.
>
> AGPL on the server, Apache-2.0 on the SDK so adapter authors can publish
> under any license they want. Commercial / closed-source adapter licensing
> at contact@cryptovoip.in.
>
> Repo: https://github.com/open-nvr/open-nvr
> AI adapter SDK: https://github.com/open-nvr/ai-adapter
> Paper: https://doi.org/10.5281/zenodo.17261761
> Compliance mapping: https://github.com/open-nvr/open-nvr/blob/main/docs/COMPLIANCE.md
> Demo video: [TODO — record the camera-agent demo, ~30 seconds, embed]
>
> Happy to answer questions about the architecture, the threat model, why
> we made specific trade-offs (MediaMTX over go2rtc, ByteTrack over
> DeepSORT, supervision pin to <0.30, etc.), or any of the per-adapter
> design decisions.

**Submission notes (delete before posting):**

- Submit between 9–11am PT Tuesday–Thursday for highest engagement.
- Have the demo video ready before posting — HN comments will ask for it.
- Stay in the thread for the first 2 hours. The first 10 comments shape
  the trajectory; engaging fast and substantively keeps the thread alive.
- Don't post the same week as a major Frigate / Home Assistant release —
  check their changelogs.
- Title tested: "Show HN: OpenNVR – Self-hosted NVR you can talk to (with an audit chain and a paper)" — leads with the demo claim (HN-attractive) but signals depth via "paper" so the security/compliance audience doesn't bounce.

---

## 2. selfh.st — Newsletter blurb

### Short (one paragraph for the weekly digest)

> **OpenNVR** — self-hosted NVR with a voice agent over your camera feeds,
> an open AI adapter contract (any model on REST/WS becomes a plugin —
> YOLOv8, Whisper, Piper, ByteTrack, BLIP, InsightFace shipping out of the
> box), and a published security architecture grounded in 34 academic +
> regulatory references. Tier 0 install in 5 minutes via Docker Compose
> with pre-built images on GHCR. AGPL.
> [github.com/open-nvr/open-nvr](https://github.com/open-nvr/open-nvr)

### Feature spotlight (~200 words, for the longer slot)

> **OpenNVR — self-hosted AI surveillance with an audit chain**
>
> If you've been running Frigate or ZoneMinder for a while and you've ever
> wondered "could I just *talk* to my cameras?" — this is the project. The
> headline is the camera-agent: a voice loop where you ask out loud
> "is anyone at the front door?" and an Ollama-hosted LLM answers from a
> live frame, with YOLOv8 + InsightFace + BLIP registered as tools for
> grounding. All on your hardware, no cloud calls, no API keys.
>
> Underneath that, the architecture itself is more interesting than it
> sounds. Every AI capability is a contract-compliant container that you
> can publish under any license, plus a sha256-fingerprinted audit chain
> with end-to-end correlation IDs joining every alert to the inference
> that produced it. The threat model is documented in a peer-citable
> paper (DOI 10.5281/zenodo.17261761) grounded in CISA advisories, real
> CVEs, and the 2021 Verkada breach.
>
> Tier 0 install is `docker compose -f docker-compose.tier0.yml up -d`
> and you have NVR + YOLOv8 detection running in five minutes. Add the
> camera-agent overlay for the voice path. AGPL on the server,
> Apache-2.0 on the adapter SDK.
>
> [github.com/open-nvr/open-nvr](https://github.com/open-nvr/open-nvr)

**Submission notes:**

- selfh.st's submission email is on their site; check current URL.
- Lead with the camera-agent demo (their audience cares about "what can
  I do with this?"), bring up the paper only in the feature spotlight,
  not the one-liner.

---

## 3. README hero (already landed)

The README hero is rewritten in the main branch. Keeping it here for
parallel reference / quick paste-back if it gets edited and we want to
restore.

```markdown
# OpenNVR

### The self-hosted NVR you can talk to.

Object detection, license-plate OCR, face recognition, scene captioning,
multi-object tracking — and a voice agent that grounds its answers in
live camera feeds. All running on your hardware. No cloud calls by
default. Pluggable AI adapter contract. AGPL.

> **Built on published research.** OpenNVR is the open-source reference
> implementation of *Eliminating Systemic IP Camera Vulnerabilities via
> Offline-First Open Security Architecture* (Singh et al., 2025 —
> DOI 10.5281/zenodo.17261761). 34 sources spanning CISA advisories,
> real CVEs (Hikvision, Dahua, Uniview, Edimax, ThroughTek Kalay), the
> 2021 Verkada breach, NIST CSF 2.0, NIST AI RMF, ISO/IEC 27001, ETSI
> EN 303 645, GDPR, and India's DPDP Act. Paper §3 → §4 → code mapping
> in docs/COMPLIANCE.md. If your procurement team needs to defend the
> choice, hand them that page.
```

---

## 4. Twitter / X — launch thread

Time with the HN Show HN post. Lead with the demo video embedded in
the opening tweet. Mastodon version below differs because of the
character-limit headroom — Mastodon gets the longer-form variant.

### Twitter thread (6 tweets, ~280 chars each)

**Tweet 1 (the hook, attach the demo video):**

> We just released OpenNVR — the self-hosted NVR you can talk to.
>
> Ask out loud "is there a person at the front door?" and it answers from a live frame. All on your hardware. No cloud calls.
>
> Demo video ↓ Repo: github.com/open-nvr/open-nvr

**Tweet 2 (the differentiation — security):**

> Why does it matter? Because every existing IP-camera deployment is one of: vendor-cloud breach risk (Verkada 2021 — 150k cameras), FCC-Covered-List vendor (Hikvision/Dahua), or homelab NVR built before audit chains were a thing.
>
> We built one that fixes all three.

**Tweet 3 (the paper):**

> The architecture is described in a peer-citable paper (DOI 10.5281/zenodo.17261761) grounded in 34 references including CISA advisories, NIST CSF 2.0, NIST AI RMF, ETSI EN 303 645, GDPR, India's DPDP Act.
>
> OpenNVR is its open-source reference implementation.

**Tweet 4 (the AI breadth):**

> The killer differentiator: open AI adapter contract.
>
> Any model behind a REST/WS endpoint becomes a first-class capability. Object detection, ASR, TTS, LLMs with tool-calling, OCR, tracking, captioning — all wired through the same Apache-2.0 SDK in ~30 lines.

**Tweet 5 (the install):**

> 5-minute install. Pre-built images on GHCR. No source build.
>
> ```
> git clone github.com/open-nvr/open-nvr
> cd open-nvr
> cp .env.example .env
> ./scripts/generate-secrets.sh --write
> docker compose -f docker-compose.tier0.yml up -d
> ```

**Tweet 6 (the close + links):**

> AGPL server, Apache-2.0 SDK. Build adapters under any compatible licence including proprietary.
>
> 🔗 Repo: github.com/open-nvr/open-nvr
> 🔗 Paper: doi.org/10.5281/zenodo.17261761
> 🔗 HN thread: [insert HN URL once posted]
>
> Questions? Drop them below.

### Mastodon thread (5 toots — fewer character constraints)

Mastodon's audience skews more privacy / security-conscious. Lead
with the architecture / sovereignty story rather than the demo.

**Toot 1:**

> Today we released OpenNVR — an open-source NVR for IP cameras built on a peer-citable security architecture for offline-first surveillance.
>
> The paper (DOI 10.5281/zenodo.17261761) describes a three-tier model that structurally eliminates the systemic IP-camera weaknesses that produced the Mirai botnet, the 2021 Verkada breach, and the recent CISA advisories against Hikvision / Dahua / Uniview / Edimax.
>
> OpenNVR is the implementation. AGPL on the server, Apache-2.0 SDK. 1/5

**Toot 2 (the demo):**

> The novel piece: a voice agent over your camera feeds. You speak the question, it grounds the answer in a live frame and replies via Piper TTS.
>
> Whisper STT + Ollama LLM (with OpenAI-style tool-calling) + Piper TTS, with YOLOv8 / BLIP / InsightFace as the grounding tools — all on your hardware, no cloud calls.
>
> [demo video] 2/5

**Toot 3 (the adapter contract):**

> The architectural differentiator from Frigate / ZoneMinder / Viseron: an open AI Adapter Contract v1 with a published wire spec. Any model behind a REST or WebSocket endpoint becomes a first-class capability.
>
> Ship adapters under any compatible licence — including proprietary or classified for the organisations where that matters. ~30 lines of Python plus your model.
>
> Seven shipped today: object detection, face recognition, ASR, TTS, LPR, scene captioning, multi-object tracking. More on the roadmap. 3/5

**Toot 4 (the audit chain):**

> Every inference carries an X-Correlation-Id from alert → middleware → adapter. Model weights are sha256-fingerprinted and polled every 60s for drift. No shipped default password. Cloud routes return 403 unless explicitly opted in — and the opt-in is itself audit-logged.
>
> Procurement-grade evidence trail that satisfies CISA Secure-by-Design, NIST CSF 2.0, ISO/IEC 27001, ETSI EN 303 645, GDPR, India's DPDP. 4/5

**Toot 5 (the install + links):**

> 5-minute install via pre-built GHCR images:
>
> ```
> git clone github.com/open-nvr/open-nvr
> cd open-nvr
> cp .env.example .env
> ./scripts/generate-secrets.sh --write
> docker compose -f docker-compose.tier0.yml up -d
> ```
>
> Repo: https://github.com/open-nvr/open-nvr
> Paper: https://doi.org/10.5281/zenodo.17261761
> Compliance mapping: https://github.com/open-nvr/open-nvr/blob/main/docs/COMPLIANCE.md
>
> Happy to answer questions. 5/5

### LinkedIn long-form post (separate from the thread)

Targets the procurement / SMB IT / sector-publication audience. Don't
crosspost from Twitter — different register entirely.

> **Why open-source security infrastructure matters more than it did five years ago — and why we wrote a paper before we wrote the code.**
>
> In 2021, the Verkada breach exposed ~150,000 cameras across hospitals, schools, and enterprises through a single set of compromised cloud credentials. CISA advisories continue to document high-severity flaws across major IP-camera vendors. The FCC's Covered List restricts widely-deployed vendors for critical-infrastructure use.
>
> Most operators respond by buying a different vendor's product. That's a procurement decision, not an architectural one.
>
> Today we released OpenNVR, an open-source NVR built on a peer-citable architectural paper (DOI 10.5281/zenodo.17261761) that describes — and structurally eliminates — the six systemic weakness categories documented in the IP-camera literature: internet exposure, plaintext streaming, vendor-controlled cloud, opaque firmware supply chains, lifecycle gaps, and inconsistent ONVIF compliance.
>
> What ships with the v0.1 release:
>
> • An offline-first three-tier deployment architecture (isolated camera network → middleware gateway → analytics layer) implementing the paper's reference design.
> • An open AI Adapter Contract v1 with an Apache-2.0 SDK — any model behind a REST/WebSocket endpoint becomes a first-class capability without forking OpenNVR. Critical for organisations whose tactical AI is operationally sensitive (defence, critical infrastructure, regulated industries).
> • End-to-end correlation-ID audit chain joining every alert to its inference call, model weights' sha256 fingerprint, and the operator who provisioned the camera. Procurement-grade evidence.
> • Compliance alignment mapped against CISA Secure-by-Design, NIST CSF 2.0, NIST AI RMF, ISO/IEC 27001, ETSI EN 303 645, GDPR, and India's DPDP Act. Full evidence-to-control mapping at [github.com/open-nvr/open-nvr/blob/main/docs/COMPLIANCE.md](https://github.com/open-nvr/open-nvr/blob/main/docs/COMPLIANCE.md).
> • A 5-minute install via pre-built container images on GHCR. No source build required.
>
> For defence, critical-infrastructure, healthcare, education, and government deployments, the architecture transforms tactical doctrine into deployable AI that runs on your hardware under your control — not a vendor's. Procurement brief: [github.com/open-nvr/open-nvr/blob/main/docs/GOVERNMENT_DEPLOYMENT.md](https://github.com/open-nvr/open-nvr/blob/main/docs/GOVERNMENT_DEPLOYMENT.md).
>
> AGPL on the server, Apache-2.0 on the SDK so adapters can ship under any compatible licence. Commercial support, deployment assistance, compliance evidence packs, and sponsored development available via contact@cryptovoip.in.
>
> [demo video link]
> [repo link]
> [paper link]

### Reddit posts (per-subreddit framing)

Same launch, three different framings.

**r/selfhosted** (lead with the demo, install, and AGPL):

> ## OpenNVR — self-hosted NVR with a voice agent and an open AI adapter contract (v0.1 released)
>
> Hey r/selfhosted —
>
> Just released OpenNVR v0.1. It's an alternative to Frigate / ZoneMinder / Viseron with three angles I haven't seen elsewhere:
>
> 1. **Voice agent over your camera feeds.** Ask out loud "is there a person at the front door?" — the agent grounds its answer in a live frame and replies via Piper TTS. All on your hardware, no cloud calls. [demo video]
>
> 2. **Open AI adapter contract.** Any model on a REST/WS endpoint becomes a first-class capability. ~30 lines of Python in the Apache-2.0 SDK. Seven adapters ship out of the box (YOLOv8, InsightFace, Whisper, Piper, fast-plate-ocr, BLIP, ByteTrack); the contract makes it easy to add more.
>
> 3. **Audit chain.** Every inference gets an `X-Correlation-Id` from alert → middleware → adapter, model weights sha256-fingerprinted, no shipped default password, cloud routes 403 by default. Architecture described in a published paper (DOI 10.5281/zenodo.17261761).
>
> 5-minute install via pre-built GHCR images:
>
> ```
> git clone https://github.com/open-nvr/open-nvr
> cd open-nvr && cp .env.example .env
> ./scripts/generate-secrets.sh --write
> docker compose -f docker-compose.tier0.yml up -d
> ```
>
> Honest comparison with Frigate / ZoneMinder / Shinobi / Viseron / Verkada at github.com/open-nvr/open-nvr/blob/main/docs/COMPARISONS.md — we acknowledge what each does well and where OpenNVR fits differently.
>
> Happy to answer questions.

**r/homeassistant** (lead with the HA integration angle):

> ## OpenNVR v0.1 — bridges to HA via MQTT discovery + voice agent over cameras
>
> r/homeassistant — released OpenNVR v0.1 today.
>
> Frigate-adjacent project but with a different shape: open AI adapter contract so any model becomes a first-class capability (Whisper, Piper, Ollama with tool-calling, ByteTrack tracker, BLIP captioner all shipping out of the box).
>
> Includes a Home Assistant bridge example (`examples/home-assistant-relay`) that fans alerts into HA via MQTT discovery — each alert source auto-creates the right HA entities.
>
> The novel piece is the camera-agent: a Pipecat + Whisper + Ollama + Piper voice loop where you ask out loud "is there a package on the porch?" and it grounds the answer in a live frame. [demo video]
>
> 5-minute install via Docker Compose, pre-built images on GHCR. AGPL.
>
> Repo: github.com/open-nvr/open-nvr

**r/cybersecurity** (lead with the architecture paper and audit):

> ## OpenNVR — open-source IP camera middleware with peer-citable architecture paper
>
> Released v0.1 of OpenNVR today. The cybersecurity angle:
>
> Most self-hosted NVRs (Frigate, ZoneMinder, Viseron) are built primarily for ease of use, with security as a configuration concern. OpenNVR inverts that. The architecture is described in a peer-citable paper (DOI 10.5281/zenodo.17261761) grounded in 34 references including CISA advisories for Hikvision/Dahua/Uniview/Edimax CVEs, the 2021 Verkada breach, NIST CSF 2.0, NIST AI RMF, ETSI EN 303 645.
>
> What's enforced at the protocol / process layer:
>
> - No shipped default credentials (one-time setup token at first boot).
> - Strong-secret validator refuses to boot on placeholder values for `SECRET_KEY`, `INTERNAL_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, `MEDIAMTX_SECRET`.
> - RTSPS / HLS-TLS / WebRTC-TLS on every operator-facing transport. Plaintext loopback only for the in-host inference tap (documented trade-off in `docs/SECURITY_ARCHITECTURE.md` §"RTSP encryption posture").
> - Two independent default-deny gates: `DEPLOYMENT_MODE=offline` (default) makes cloud routes return HTTP 403 — override `=hybrid` or `=cloud`. `AI_SOVEREIGNTY=local_only` (default) refuses AI adapters declaring `network_egress` — override `=federated` or `=cloud_allowed`. Both flips audit-logged.
> - End-to-end `X-Correlation-Id` from alert → middleware → adapter. Append-only audit log with NATS fan-out for SIEM forwarding.
> - sha256 model-fingerprint polled every 60s; drift surfaces as `adapter.fingerprint_mismatch` events.
> - AI sovereignty enforcement at the adapter contract: adapters declaring `network_egress` refused under `local_only` policy.
>
> Compliance mapping (paper §3 → §4 → code): github.com/open-nvr/open-nvr/blob/main/docs/COMPLIANCE.md
>
> AGPL. Repo: github.com/open-nvr/open-nvr

---

## Posting order (recommended)

1. **README hero rewrite + docs/COMPLIANCE.md + docs/GOVERNMENT_DEPLOYMENT.md**
   land first (already done). These are the credibility anchors that
   everything else points at.
2. **selfh.st short-form blurb** goes second — gets early homelab signal
   without burning the HN slot. selfh.st has weekly cadence so the timing
   is forgiving.
3. **HN Show HN** goes third, ~1 week after selfh.st, so there's
   already some early-user feedback / stars / discussion thread to point
   at when HN commenters ask "how mature is this?"
4. **Twitter / Mastodon** announcement coincides with the HN post — link
   to the HN thread, link to the camera-agent demo video. The audience
   that finds OpenNVR through Twitter is mostly there for the demo.
5. **Discord / Matrix** announcement (homelab + selfhosted communities)
   the same day as HN. Same demo-video lead.
6. **Direct outreach** (academics citing the paper, government IT contacts
   already in the user's network) goes after the public launch is over —
   send the COMPLIANCE.md + GOVERNMENT_DEPLOYMENT.md links, not the HN
   thread, because those audiences respond to the procurement-grade
   collateral, not the demo.

The ordering keeps the dev/homelab audience in the warm-leads slot
(selfh.st → HN) and the procurement audience in the warm-prospect slot
(direct outreach with the compliance docs). They're different funnels;
trying to use one piece of copy for both dilutes both.

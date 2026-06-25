<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# OpenNVR Camera Agent — Editions & Models

This is the product decision for how the camera-agent ships: a small set of
clearly-named **editions** that trade footprint against capability, each with a
**deliberately efficient model stack**, so a newcomer can be talking to their
cameras in a minute *and* a security-conscious operator can run the whole thing
air-gapped. One stack can't be both "tiny and instant" and "sovereign and
complete," so we stop pretending it can and name the trade instead.

The business position is unchanged and stated up front: **the fully-local,
sovereign Sentinel edition is the flagship and the moat.** No-vendor-egress,
runs-on-your-own-iron, NDAA-clean is what nobody else in the consumer/SMB space
credibly offers. The lighter editions are not a retreat from that — they are the
*on-ramp* to it. The reason the project sees few stars and forks is that today
the only door in is the 12 GB, ten-container, voice-first door. Spotter and
Sentinel Cloud add doors; they don't move the house.

## The four editions

| Edition | What it does | Footprint | Brain | Sovereignty | Profile / config |
|---------|--------------|-----------|-------|-------------|------------------|
| **Spotter** (Lite) | Text chat over your cameras: see / count / recent events / standing monitors / alarms | **~1–2 GB**, 1 vision model | small local LLM **or** cloud | `local_only` capable | `camera-agent-lite` · `config.lite.yml` |
| **Watch** (Standard) | Spotter **+ scene description & visual Q&A + open-vocabulary "find the red truck"** | ~3–4 GB | small/mid local LLM | `local_only` capable | `camera-agent-standard` · `config.standard.yml` |
| **Sentinel** (Full / Voice) | Watch **+ hands-free voice, avatar, named persona (Shailaja / Sidhu)** — the full agent | ~6–8 GB* | mid local LLM | **`local_only` — the flagship** | `camera-agent` · `config.docker.yml` |
| **Sentinel Cloud** (Hybrid) | Local vision, **cloud brain** (and optionally cloud voice). Best reliability, near-zero local RAM | ~1–2 GB | cloud LLM (BYO key) | **Not sovereign — explicit opt-in** | `config.cloud.yml` |

\* ~6–8 GB once the voice adapters run as **one combined container** instead of
three (see *Container topology*, below). Today it is ~10–12 GB.

The first-run **default is Spotter**. That single choice is the adoption fix: it
turns "only the maintainer can run this" into "anyone can try it in a minute,"
and every heavier edition is one flag away.

```mermaid
graph LR
  subgraph Local["Always local (never leaves the box)"]
    CAM[Cameras] --> DET[YOLOv8n detector]
    DET --> AGENT
  end
  AGENT[Camera Agent] -->|Spotter / Watch / Sentinel| LOCALLLM[Local LLM<br/>Qwen2.5 1.5B–3B]
  AGENT -->|Sentinel Cloud| CLOUDLLM[Cloud LLM<br/>BYO key]
  AGENT -.Watch+.-> CAP[Caption / VQA<br/>BLIP · Moondream2]
  AGENT -.Watch+.-> OV[Open-vocab find<br/>OWLv2]
  AGENT -.Sentinel.-> STT[STT · faster-whisper]
  AGENT -.Sentinel.-> TTS[TTS · Piper]
  style Local fill:#0d1b2a,stroke:#2a9d8f,color:#fff
  style CLOUDLLM stroke:#e76f51,stroke-dasharray: 4 4
```

Frames never leave the machine in any edition. In Sentinel Cloud, only the
*chat/tool-call text* goes to the chosen provider — which is why it is labelled
non-sovereign and gated behind an explicit operator opt-in, with the boot-time
audit entry the security model already emits when the posture is relaxed.

## The model stack — chosen for efficiency, not size

The rule is **don't build models — orchestrate the best small existing ones**,
and weight every choice by CPU latency because that *is* the user experience.

| Job | Pick (default) | Why this one | Cost |
|-----|----------------|--------------|------|
| **Object detection** | **YOLOv8n** (ONNX) | The workhorse — counts people/cars/etc. Tiny, fast, runs everywhere | ~6 MB · ~20–40 ms/frame CPU |
| Detection (accuracy opt) | YOLO11m | When nano misses small/distant objects | ~40 MB |
| **Open-vocab find** | **OWLv2-base** (existing `vlm` adapter) | "Find the red truck / a person on a bicycle" — no retraining, query in plain text | ~600 MB |
| **Scene description / VQA** | **BLIP-base** now → **Moondream2** next | BLIP captions today; Moondream2 (~1.8B) adds real *visual Q&A* ("is the gate open?") at edge size — a feature unlock, not just a caption | BLIP ~990 MB · Moondream2 ~1.7 GB |
| **Speech-to-text** | **faster-whisper `base.en`** (CTranslate2) | ~4× faster than vanilla Whisper on CPU at equal accuracy; English-only avoids foreign-token hallucination | ~140 MB · near-real-time |
| **Text-to-speech** | **Piper** (`en_US-libritts-high`, `-low` for snappier first audio) | Already the most efficient quality TTS for local; sub-second synthesis | ~60 MB |
| **Brain — snappy** | **Qwen2.5-1.5B-Instruct** | Smallest model that still tool-calls reliably; the Spotter default | ~1.5 GB · ~10–20 tok/s CPU |
| **Brain — reliable** | **Qwen2.5-3B-Instruct** | Markedly better multi-tool prompts; the Watch/Sentinel default | ~3 GB |
| **Brain — cloud** | gpt-4o-mini · Groq Llama-3.3-70B · Claude Haiku | Best tool-call reliability, ~0 local RAM, lowest latency | BYO key |
| **Faces / watchlist** | **InsightFace `buffalo_s`** | Small face pack is plenty for enroll + watchlist match; half the RAM of `buffalo_l` | ~0.3 GB |

Two honest trade-offs worth stating in the docs: small CPU LLMs *will*
occasionally miss a tool call on a tool-heavy prompt — mitigated by the
anti-fabrication forced-grounding guard (shipped) and constrained tool-call
decoding (next) — and snappy-on-CPU vs reliably-agentic is a real dial you set
per machine, which is exactly what the editions encode.

### Exciting features these unlock
The point of picking *capable* small models (not just *small* ones) is that each
adds a feature, not just a footprint line: **visual Q&A** ("is the driveway gate
open?") from Moondream2, **open-vocabulary search** ("tell me if a red truck
shows up") from OWLv2, **hands-free voice with a named persona** from
faster-whisper + Piper, and **reliable standing monitors & alarms** from
Qwen2.5's tool-calling. None of these requires training anything.

## Run on the hardware you already have — zero camera provisioning

You shouldn't have to wire up an RTSP camera just to try this. If the machine
the agent runs on *has* a camera, it uses it: set `auto_discover_cameras: true`
(see `config.local.yml`) and the agent finds the local capture device — a
**laptop webcam** for a dev, a **USB or Pi camera**, or the onboard camera on a
**drone / robot** (`/dev/video*`). Frame URLs gain a `device:` scheme
(`device:0`, `device:/dev/video1`, `device:auto`) alongside the existing
`http(s)://`, `rtsp://`, and `file://` sources, so *any* device that exposes a
camera or a stream can run its own on-board sovereign agent.

This is the lightweight end of the hardware dial taken to its logical end: the
camera-agent isn't only something you point *at* cameras — it can be the app
that *ships on* the camera-bearing device itself. (Local device capture needs
OpenCV on the host; it's imported lazily so nothing else depends on it.)

## Container topology — the other half of "too heavy"

Running the agent today starts ~10 containers, and three of them —
`whisper-adapter`, `piper-adapter`, `blip-adapter` — are **separate images that
each bundle their own ~2 GB copy of PyTorch**. That triplication is a large
share of both the RAM and the GHCR image bloat called out in issue #82.

The fix already exists in the repo: the `ai-adapter` project builds **one
combined image that serves whisper, piper, blip and the vision models on a
single port** (`app/main.py`, `uv sync --extra all`). The direction, therefore:

- **Sentinel runs one combined "voice adapter" container** instead of three,
  collapsing 3 → 1 and removing the duplicate torch installs. (Needs a real
  `docker build` + a route-contract check before flipping the default — the
  per-adapter images expose `/infer` while the combined image routes by task —
  so this is staged, not silently switched.)
- **A shared model-cache volume** so weights are pulled once and mounted across
  adapters rather than re-downloaded per container — fast pulls with the `#79`
  offline pin (`TRANSFORMERS_OFFLINE=1`) preserved, so a cold cache fails closed
  instead of phoning home.
- **Spotter and Watch sidestep the problem entirely** — they don't start the
  voice adapters at all.

Modularity is still correct for production scale-out (independent GPU placement,
swap-a-model, fault isolation), so the per-adapter images stay available as the
advanced/at-scale option; the combined container is the right *default* for the
single-box experience that 95% of first-runs are.

## How this maps to the issue #82 complaints

Heavy stack / ~12 GB RAM → **Spotter & Watch** make the light path real and the
default. Bloated GHCR images / duplicate torch → **combined voice adapter +
shared cache** (staged). Sovereignty blocking dev → **Sentinel Cloud** is the
labelled, audited escape hatch; `local_only` stays the secure default. Small-LLM
unreliability → **Qwen2.5 picks + cloud brain tier**. Cloud comparison →
**Sentinel Cloud** is the apples-to-apples profile, with the sovereign Sentinel
still the differentiator nobody else offers.

The local sovereign stack stays the *promise*; the editions are the *doors* that
let people actually walk up to it.

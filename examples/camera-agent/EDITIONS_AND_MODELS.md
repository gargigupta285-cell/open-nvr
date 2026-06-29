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
| **Scene understanding / VQA** | **a VLM**: Moondream2 (CPU/edge), SmolVLM-2B or Qwen2.5-VL-3B (richer, **video-capable**) — replacing BLIP | This is the key model for *conversations about video*. BLIP only *captions* ("a man at a desk") and can't answer "what is he wearing/doing?" (test-report S-6). A VLM does real **visual Q&A + dialogue**, and SmolVLM/Qwen-VL take **video frames** directly. All **Apache-2.0** | Moondream2 0.5B ~0.5 GB / 2B ~1.7 GB · SmolVLM-2B ~2 GB · Qwen2.5-VL-3B ~3 GB |
| **Speech-to-text** | **faster-whisper `base.en`** (CTranslate2; `tiny.en` for low power, `small.en` for max accuracy) | Accurate on natural speech AND fewer silence hallucinations than tiny; ~4× faster than vanilla Whisper on CPU; English-only avoids foreign-token hallucination | ~140 MB · near-real-time |
| **Text-to-speech** | **Piper** (`en_US-libritts-high`, `-low` for snappier first audio) | Already the most efficient quality TTS for local; sub-second synthesis | ~60 MB |
| **Brain — default** | **Qwen3-1.7B** (non-thinking) | Per Qwen, ~Qwen2.5-3B quality with better tool-calling, at ~half the RAM; the default across editions. Run non-thinking (`llm_think: false`) for snappy replies | ~1.5–2 GB · ~10–20 tok/s CPU |
| **Brain — nano** | **Qwen3-0.6B** | The floor that still tool-calls; `quickstart.sh --nano`. Misses more tool calls (forced-grounding mitigates) — for demo / very low-RAM boxes | **~0.5 GB** |
| **Brain — roomier** | Qwen3-4B / a cloud model | When you have the headroom or want max reliability | ~3 GB+ |
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

## Best models for the purpose: no hallucination + good video conversation

Two goals deserve a direct answer, because they drove the picks above:

**No hallucination.** This is handled *structurally*, not just by hoping the
model behaves:
- **Forced grounding** — the agent injects a real tool result and re-asks, so
  the model literally *cannot* answer a camera question from imagination; if it
  tries, the tool result overrides it (shipped).
- **Qwen3 tool-adherence** — Qwen3 calls the tool on the first pass far more
  often than Qwen2.5/Llama-3.2 did, so the override fires less. For near-zero
  hallucination, `qwen3:4b` or a cloud model calls tools every time (the live
  test confirmed gpt-4o-mini never fabricated).
- **STT noise filter + base.en** — drops Whisper's silence hallucinations so
  noise can't start a phantom turn.
- **Routing fix** — "describe/what's-he-doing/wearing" now goes to the VLM, not
  the object detector, so answers are about the *actual* attributes.

**Good conversations about video.** The single biggest lever is replacing the
BLIP *captioner* with a **vision-language model** (Moondream2 / SmolVLM /
Qwen2.5-VL — all Apache-2.0). A captioner can only say "a man at a desk"; a VLM
*answers questions* ("what is he wearing?", "is the gate open?", "what just
happened?") and SmolVLM/Qwen-VL ingest **video frames**, not just stills. The
agent is already wired for this: `describe_camera` forwards the user's question
to the vision adapter, uses its `answer`, and degrades gracefully to a caption
when only BLIP is present. Register a VLM adapter as the caption adapter and
video Q&A works end-to-end — no agent changes.

Pairing: **Qwen3 (brain) + a VLM (eyes) + faster-whisper base.en (ears) + Piper
(voice)** — all small, all Apache-2.0, all CPU-runnable — is the
no-hallucination, video-capable, sovereign stack. The one piece still to build
is the VLM adapter itself (the agent side is ready).

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

Running the full agent today starts ~10 containers, and three of them —
`whisper-adapter`, `piper-adapter`, `blip-adapter` — run as separate images. To
be accurate about where the weight actually is: **whisper** uses faster-whisper
(CTranslate2, *no* torch) and **piper** uses piper-tts + onnxruntime (*no*
torch); only **`blip-adapter` carries a full PyTorch + Transformers stack**
(~2 GB of torch plus its ~990 MB baked weights). So the cost isn't "torch ×3" —
it's three separate containers to pull, start, and health-check, one of which
(BLIP) is genuinely heavy on its own.

The fix: a **combined "voice adapter" image** that runs all three adapter apps
(`adapters.whisper/piper/blip.main`) in **one container** on their existing
ports. This is a true contract match — same per-adapter `/infer` endpoints the
camera-agent already calls — so it's low-risk, unlike the monolithic
`app/main.py` (port 9100, task-routed) which is a *different* contract. What it
buys, honestly:

- **One image instead of three; one container instead of three.** Fewer things
  to pull, start, and restart — the real, immediate win.
- **Modest RAM savings** (one Python base, shared libs), but be honest: the RAM
  of the full edition is dominated by the *models* — the Ollama LLM (2–6 GB) and
  BLIP+torch (~3 GB) — not by container overhead. **Merging containers does not
  halve RAM.**
- **The big RAM lever is the editions and model choices, not the merge.** Spotter
  and Watch don't start the voice adapters at all; a smaller LLM and
  faster-whisper `tiny.en` cut the most. That's where "~12 GB → ~6–8 GB" actually
  comes from.

Needs a real `docker build` + a live bring-up to verify (can't build in-sandbox),
so the combined service ships as an opt-in profile, not a silent default swap.
Modularity stays correct for production scale-out (independent GPU placement,
swap-a-model, fault isolation), so the per-adapter images remain the advanced
option.

## Honest RAM accounting (why "~12 GB → ~6–8 GB")

The full edition's memory is the *models*, not container overhead. The defaults
now favour the light end (override per box via `.env`):

| Component | Old default | New default | Resident RAM |
|-----------|-------------|-------------|--------------|
| LLM (Ollama, kept warm) | `llama3.2:3b` | **`qwen3:1.7b`** (non-thinking; `qwen3:0.6b` for nano) | ~3.5 GB → **~1.5–2 GB** (~0.5 GB nano) |
| STT (Whisper) | `base.en` | **`tiny.en`** | ~1 GB → **~0.3–0.5 GB** |
| Captions (BLIP + torch) | on | on (full only) | ~2.5–3 GB |
| TTS (Piper) | on | on (full only) | ~0.1 GB |
| Detector (YOLOv8n) + core | on | on | ~1–1.5 GB |

So a default **full** edition lands around **~6–8 GB** instead of ~11–12 GB,
and **Spotter/Watch** (no Whisper/Piper/BLIP) sit at ~1–4 GB. The single var
`OLLAMA_MODEL` drives both the model pull and the agent config, and
`WHISPER_MODEL_SIZE` swaps the STT model — bump them to `qwen3:4b` / `base.en`
for more reliable tool-calling and transcription when you have the headroom.

This is the real RAM lever (the combined-image merge above is operational
simplicity, not memory). Be honest in conversation about the trade: a sub-2B
model on CPU is snappy but will occasionally miss a tool call on a busy prompt —
the forced-grounding guard catches the worst of it, and the cloud / `qwen3:4b`
tiers are there when reliability matters more than footprint.

**Licensing note:** all the local models above are permissively licensed for
commercial use — every **Qwen3** dense model (0.6B/1.7B/4B/…) is **Apache-2.0**,
as are YOLOv8n, BLIP, faster-whisper, Piper, and InsightFace's small pack. We
deliberately default to Qwen3 (not Qwen2.5-3B, which ships under the non-Apache
Qwen Research License) so the sovereign stack stays clean to redistribute.

## How this maps to the issue #82 complaints

Heavy stack / ~12 GB RAM → **Spotter & Watch** make the light path real and the
default, and the heavy hitters are the LLM + BLIP, so a smaller LLM and dropping
BLIP where it isn't needed is the lever. Container/image sprawl → **combined
voice adapter** (3 images → 1; staged behind a profile). Sovereignty blocking dev → **Sentinel Cloud** is the
labelled, audited escape hatch; `local_only` stays the secure default. Small-LLM
unreliability → **Qwen2.5 picks + cloud brain tier**. Cloud comparison →
**Sentinel Cloud** is the apples-to-apples profile, with the sovereign Sentinel
still the differentiator nobody else offers.

The local sovereign stack stays the *promise*; the editions are the *doors* that
let people actually walk up to it.

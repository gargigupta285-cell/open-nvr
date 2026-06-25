# Camera-agent architecture review (issue #82)

The local camera-agent works end-to-end but is heavy: ~12 GB RAM, bloated
images, slow warmup, and small local LLMs that miss tool calls. This is the
plan to make it light, reliable, and easy to run — without abandoning the
sovereign default.

## Deployment profiles (the core idea)

One stack can't be both "sovereign and complete" and "tiny and instant." So we
offer **profiles** and pick the right *default* for first-run:

| Profile | Models running | RAM (approx) | Use it for |
|---------|----------------|--------------|------------|
| **lite** (new) | YOLOv8n detector + small LLM, **text chat only** | ~1–2 GB | The fast on-ramp; modest dev boxes; CI; demos |
| standard | + BLIP captions | ~3–4 GB | Richer answers, still local |
| full | + Whisper STT + Piper TTS + Ollama voice loop | ~8–12 GB | The full hands-free voice agent |
| **cloud / hybrid** | vision local, LLM/STT/TTS via BYO cloud API | ~1–2 GB | Reliable, low-latency dev/demos; low-resource machines |

`lite` should be the **documented default** — it's the difference between "only
the maintainer can run it" and "anyone can try it in a minute" (which is also
the project's adoption problem).

### Lite is implemented now (increment 1)
- `POST /ask` — text in → grounded reply out. No mic, no Whisper, no Piper, so
  the heavy audio models never load.
- `config.lite.yml` — detection-grounded skills (see/count/recent-events/watch/
  alarm) on the single YOLOv8 detector + a small LLM.
- `text_mode` config; the demo UI shows a text box (and `/agent` reports the
  mode). Voice stays available but optional.
- Tests: `tests/test_lite_ask.py`.

## 2. Model packaging — reconcile with #79

#79 baked BLIP's ~990 MB weights into the image for sovereignty; #82 (rightly)
flags the bloat. **These aren't in conflict** once you separate the concern:
sovereignty needs "no runtime egress to a *vendor*", not "weights inside the
image." Direction:
- **Bake only tiny weights** (e.g. yolov8n) where image size is negligible.
- **Cache large weights in a named volume via a model-cache init container**
  that pulls from **our own registry/GHCR** (not HuggingFace) — lean images,
  shared cache across adapters, still zero vendor egress at runtime.
- Keep the runtime offline pin (`TRANSFORMERS_OFFLINE=1`) so a missing cache
  fails closed rather than phoning home.

This makes image pulls fast *and* preserves `local_only`.

## 3. Resource management
- **Lazy-load + idle-unload** models so idle adapters don't sit on RAM.
- Default to the **lite footprint**; make the voice stack opt-in.
- Honest **hardware matrix** in docs (lite vs full; CPU vs GPU) and a GPU build
  path so detection/LLM latency drops and background polling stops competing
  with the live turn.
- Conservative poll intervals are already in place (monitors 8 s, alarms 5 s,
  reports 30 s) — see `MODELS_AND_LATENCY.md`.

## 4. Sovereignty: keep the guarantee, unblock dev
- Keep `local_only` as the **secure default**.
- `federated` / `cloud_allowed` already exist — the gap is they **hard-block**.
  Add a **`dev` / hybrid** posture that **logs loudly + audits** the egress
  instead of refusing, so experimentation (cloud LLM/vision, remote adapters,
  fallback providers) isn't a wall.
- Ship a **documented cloud/hybrid profile** (BYO key), clearly labeled
  non-sovereign, with the boot-time audit entry the security model already
  emits when the posture is relaxed.

## 5. Small-model reliability
Small CPU LLMs (Qwen-1.5B / Llama-3B) *will* miss tool calls on a tool-heavy,
multi-context prompt. Layered mitigations:
- Keep the **anti-fabrication forced-grounding** guard (already in).
- Add **constrained / grammar-based tool-call decoding** so the model can only
  emit valid tool JSON.
- Offer a **larger or cloud LLM tier** for reliability (the cloud profile).
- Be honest in docs: snappy-CPU and reliable-agentic are a real tradeoff; pick
  per machine.

## Sequencing
1. **lite text `/ask` + UI** ✅ done (`config.lite.yml`, `tests/test_lite_ask.py`).
2. **lite docker-compose profile** ✅ done (`--profile camera-agent-lite` runs
   core + YOLOv8 + LLM only — no Whisper/Piper/BLIP, ~1–2 GB; `config.docker.lite.yml`).
3. **Cloud/hybrid LLM client (OpenAI-compatible)** ✅ done (`llm_provider: openai`,
   `config.cloud.yml`, `tests/test_cloud_llm.py`).
4. Model-cache init container — fix image bloat without breaking #79.  *(next)*
5. Lazy-unload + GPU build + hardware matrix docs.  *(next)*
6. Constrained tool-calling + `dev` sovereignty posture.  *(next)*

Status vs the issue: #1 & #3 (heavy stack / RAM) now have a real fix via the
lite profile; #5 (reliability) and the cloud-comparison ask are addressed by the
cloud profile; #2 (image bloat) and #4 (sovereignty dev mode) remain.

## Container topology — the duplicate-torch finding
Running the full agent starts ~10 containers; three of them (`whisper-adapter`,
`piper-adapter`, `blip-adapter`) are **separate images each bundling its own
~2 GB PyTorch** — a big share of the RAM and GHCR bloat. But `ai-adapter`
already builds **one combined image serving all three (+ vision) on one port**
(`app/main.py`, `uv sync --extra all`). Direction:
- **Combined "voice adapter"** for the full edition → 3 containers → 1, no
  duplicate torch. *Staged:* per-adapter images expose `/infer` while the
  combined image routes by task, so this needs a real `docker build` + contract
  check before flipping the default (can't verify in-sandbox).
- **Shared model-cache volume** so weights download once and mount across
  adapters, `#79` offline pin preserved.
- Spotter/Watch don't start the voice adapters at all, so they sidestep it.

## Product framing — editions (see `EDITIONS_AND_MODELS.md`)
Four named editions encode the footprint/capability trade so the security
position stays explicit: **Spotter** (lite, text, ~1–2 GB) · **Watch**
(standard, +caption/VQA +open-vocab, ~3–4 GB) · **Sentinel** (full voice,
the flagship `local_only` product) · **Sentinel Cloud** (hybrid, cloud brain,
labelled non-sovereign). Spotter is the documented default — the adoption fix.
Model picks are decided there (YOLOv8n, OWLv2, BLIP→Moondream2, faster-whisper,
Piper, Qwen2.5-1.5B/3B, cloud brains, InsightFace buffalo_s) — all efficient,
all existing (no model training).

Implemented this round: **Watch/Standard edition** — `config.standard.yml`,
`config.docker.standard.yml`, and the `camera-agent-standard` compose profile
(detector + BLIP + small LLM, no voice; validated to exclude whisper/piper).

The local sovereign stack stays the *promise*; the editions are the *doors*
that let people actually reach it.

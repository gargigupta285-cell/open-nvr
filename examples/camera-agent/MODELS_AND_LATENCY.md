# Models & latency — choosing for good UX

This agent does **not** build or train any models. It orchestrates pre-built
adapters (Whisper, Ollama, Piper, YOLOv8, BLIP, InsightFace). Which models you
point it at, and how hard you poll them, decides whether the experience feels
snappy or sluggish. This page records the deliberate choices and the knobs.

## The latency budget (one spoken turn)

```
mic → ffmpeg transcode → Whisper STT → LLM (tool-calling) → vision detect →
LLM (compose) → Piper TTS → playback
```

On CPU, the LLM and STT dominate. Targets that feel acceptable:
- First turn: a few seconds (cold model load is pre-warmed away — see below).
- Warm turns: ~2–5 s end to end.

Anything that adds a synchronous step (an extra tool call, a slow model) is
felt directly, so the model picks below favour *fast and good-enough* over
*slow and perfect*.

## Running on limited hardware (no GPU, little RAM)

A real CPU-only test (Win 11, 11.5 GiB) gave the numbers that drive this advice:

- **The LLM is *not* CPU-intensive — it's memory-bandwidth-bound.** Ollama showed
  only ~5–25% CPU while generating; it's slow because it streams the model's
  weights through CPU caches each token. The practical consequence: **a smaller
  model is both lighter *and* faster** on weak hardware. No GPU is required —
  a GPU just makes it ~10–20× faster.
- **The CPU hog is actually Piper TTS** (~390%, nearly 4 cores) — so **text mode
  avoids the most CPU-intensive stage entirely.**
- **RAM is modest** (~3.4 GiB peak for the full stack); compute is the limit.

So, in order, for limited hardware:

1. **Use the nano tier (smallest model, text mode):**
   `examples/camera-agent/quickstart.sh --nano` → `qwen3:0.6b` (~0.5 GB, the
   fastest tool-caller) + text chat (no Whisper/Piper/BLIP). ~2–3 GB total.
2. **Cap CPU + context in config** so the LLM doesn't peg the box:
   ```yaml
   llm_num_threads: 2     # leave cores for the rest of the machine
   llm_num_ctx: 2048      # smaller window = less RAM + faster prefill
   ```
   and in `.env`, `OLLAMA_KEEP_ALIVE=5m` frees the model's RAM when idle (at the
   cost of a cold reload on the next turn). The compose already sets
   `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1`.
3. **Or offload the brain to the cloud** (lightest *local* footprint): the
   Sentinel-Cloud path runs vision locally but the LLM on your key — ~0 local
   LLM RAM/CPU, ~1 s replies, no hallucination (see the comparison below). Not
   sovereign — an explicit opt-in.
4. **Stay in text mode** unless you need voice — it skips the CPU-heavy TTS.

Rule of thumb: **nano + text = lightest fully-local; cloud LLM = lightest on the
local machine.** Below `qwen3:0.6b` there isn't a model that tool-calls reliably,
so that's the floor.

### Disk / image size (it's not "for CPU")

Two images look huge but the size isn't a CPU requirement:

- **`ollama/ollama` ~10 GB** — the official image **bundles NVIDIA CUDA + AMD
  ROCm GPU libraries** so it *can* use a GPU. On a CPU box that's ~6–7 GB of
  unused libs. **Lighter alternative: the `camera-agent-llamacpp` profile** runs
  llama.cpp's server (a few hundred MB) serving a GGUF — same llama.cpp core,
  ~9 GB less disk, talked to via the OpenAI client (`--profile
  camera-agent-llamacpp`).
- **`blip-adapter` ~5.5 GB** — PyTorch + transformers + baked weights (the torch
  CPU wheel alone is ~2.5–3 GB). **Lighter alternative: the Moondream adapter**
  (quantized, *no torch*) is well under 1.5 GB **and** adds visual Q&A.

So the light, capable stack is **llama.cpp (brain) + Moondream (eyes) + YOLOv8n +
faster-whisper** — a fraction of the disk of Ollama + BLIP, no GPU needed.

### Measuring it
Every `/converse` turn returns a `timings_ms` breakdown
(`transcode`/`stt`/`llm`/`tts`/`total`), and the text `/ask` turn returns
`latency_ms`. The demo UI shows a per-turn chip (`STT 420 · LLM 1200 · TTS 300
· 1.9s`). For a repeatable load test against a live agent, run the harness:

```bash
python tools/latency_harness.py --url http://localhost:9100 \
    --audio question.wav --turns 8 --load-monitors 6
```

It reports p50/p95 per phase and quantifies how much background polling
(watches/alarms) steals from the live turn.

## Turn detection & background-noise rejection

A hands-free loop only feels good if it (a) ends the turn when you stop talking
and (b) doesn't react to room noise. Two layers handle this:

- **Client VAD (browser).** The mic uses `echoCancellation` + `noiseSuppression`
  + `autoGainControl`, then an RMS energy gate that **adapts to the ambient
  noise floor** (a rolling EMA of quiet frames): the start gate is
  `max(floor, noiseFloor × 2.2)`, so steady background noise never crosses it,
  while soft speech still does. Endpointing stops the turn after ~900 ms of
  silence (min 350 ms, max 15 s), and capture is suppressed while the agent is
  speaking so it never hears itself.
- **Server STT guard (`stt_noise_filter`, on by default).** Whisper hallucinates
  stock phrases from silence/noise — "Thank you.", "you", "Thanks for watching".
  `looks_like_noise()` drops these so a noisy room can't trigger a phantom turn;
  the UI just keeps listening. Set `stt_noise_filter: false` to disable.

## Recommended model choices

| Role | Default (snappy) | Upgrade (quality, slower) | Why |
|------|------------------|---------------------------|-----|
| LLM | `qwen3:1.7b` (non-thinking) | `qwen3:4b` / `llama3.1:8b-instruct` | Must support tool-calling. 1.7B answers tool calls in ~1–2 s warm on CPU; 8B is noticeably slower. All Qwen3 dense models are Apache-2.0. |
| STT | faster-whisper `base.en` | `small.en` | `.en` is English-only — faster and far fewer hallucinated tokens on quiet audio than multilingual. |
| TTS | Piper `en_US-libritts-high` | (voice of choice) | Piper is fast and CPU-friendly; pick the voice to match the persona gender. |
| Detect | YOLOv8n (`yolov8n.onnx`) | YOLOv8s/m | n is the fastest; larger nets cost latency per frame and per poll. |
| Caption | BLIP | — | Optional; `describe_camera` falls back to the detector if absent. |
| Faces | InsightFace | — | Optional; only loaded for recognition/enrollment. |

Keep the LLM **warm**: `OLLAMA_KEEP_ALIVE=-1` plus the startup pre-warm (model +
system/tools prompt prefix) means even the first real question skips the cold
load. The vision detector is pre-warmed too.

## Where latency hides — and the knobs

The biggest risk to UX isn't a single turn; it's **background polling stealing
the detector** from the live conversation:

- Each watch (`monitor`), alarm, and crossing counter polls every camera it
  covers on its interval and runs a detect. `kind=all` × N alarms × short
  intervals can saturate a CPU detector and make the chat path stall.
- Defaults are deliberately conservative: monitors poll every **8 s**, alarms
  every **5 s**, the report scheduler ticks every **30 s**, and fetched frames
  are cached for **2 s** so multiple tools in one turn hit the camera once.

Tuning guidance:
- Prefer specific cameras over `all` for always-on alarms/watches.
- Raise the poll intervals if you add many watches/alarms (they're set in
  `MonitorManager` / `AlarmManager` constructors).
- On a busy box, give the vision adapter a GPU (`USE_GPU=true` in the adapter
  build) — detection latency drops an order of magnitude and polling stops
  competing with the live turn.
- Crossing counters request tracking (`task=track`), which is heavier than a
  plain detect and needs a higher frame rate to be accurate — use sparingly.

## Honest limits
- Snapshot counts and crossing counts are only as good as the poll rate and the
  tracker; they are not certified people-counting.
- Detection is limited to the model's classes (COCO YOLOv8 has no "fire" — the
  Fire alarm preset needs a fire/smoke model registered).

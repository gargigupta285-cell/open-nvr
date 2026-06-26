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

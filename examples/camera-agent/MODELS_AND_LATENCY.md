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

## Recommended model choices

| Role | Default (snappy) | Upgrade (quality, slower) | Why |
|------|------------------|---------------------------|-----|
| LLM | `qwen2.5:1.5b` | `qwen2.5:3b` / `llama3.1:8b-instruct` | Must support tool-calling. 1.5B answers tool calls in ~1–2 s warm on CPU; 8B is noticeably slower. |
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

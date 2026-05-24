# camera-agent example app

**Ask your cameras.** A voice agent that listens for spoken
questions, grounds its answers in live camera feeds via tool calling
(YOLOv8 / InsightFace / BLIP), and replies through Piper TTS — all
running on CPU, on your homelab, no cloud round-trip.

This is the agent example for OpenNVR v0.1. It demonstrates the
pattern of "OpenNVR camera as participant", not just camera as data
source. The next milestone (v0.2) extends the same agent to join
LiveKit rooms as a virtual participant.

## What it does

```
┌───────────────┐    speech (WS, 16k mono PCM)
│  Browser tab  │ ──────────────────────────────┐
│  /demo page   │                               │
└───────────────┘                               ▼
                              ┌───────────────────────────────────┐
                              │ FastAPI /ws + Pipecat transport   │
                              └──────────────┬────────────────────┘
                                             │ audio frames
                                             ▼
                              ┌───────────────────────────────────┐
                              │ Silero VAD (turn detection)       │
                              └──────────────┬────────────────────┘
                                             │ utterance bytes
                                             ▼
                              ┌───────────────────────────────────┐
                              │ Whisper adapter (STT)             │
                              └──────────────┬────────────────────┘
                                             │ "what's at the porch?"
                                             ▼
                              ┌───────────────────────────────────┐
                              │ Ollama adapter (llama3.2:3b) with │
                              │   4 registered tools              │
                              └──────────────┬────────────────────┘
                                             │ tool calls →
              ┌──────────────────────────────┼─────────────────────┐
              │                              │                     │
              ▼                              ▼                     ▼
    ┌────────────────┐  ┌────────────────────┐  ┌──────────────────┐
    │ KAI-C → BLIP   │  │ KAI-C → YOLOv8     │  │ KAI-C →          │
    │ scene caption  │  │ object detection   │  │ InsightFace      │
    └────────────────┘  └────────────────────┘  └──────────────────┘
                                             │
                                             │ final assistant text
                                             ▼
                              ┌───────────────────────────────────┐
                              │ Piper adapter (TTS)               │
                              └──────────────┬────────────────────┘
                                             │ PCM audio
                                             ▼
                              ┌───────────────────────────────────┐
                              │ FastAPI /ws transport → browser   │
                              └───────────────────────────────────┘
```

Single correlation_id threads through every step so KAI-C's audit
log shows: agent question → tool calls → final answer.

## Why this matters

OpenNVR already does "watch and notify". This example flips the
posture — the cameras have a voice. You ask a question, the agent
runs only the tools needed to answer (one frame from one camera,
not all of them), and tells you. It's deliberately a SMALL agent,
not "AGI for cameras":

* The model has four tools, no general-purpose memory, no web
  access. It can describe what it sees, count objects, recognise
  faces, and look back at recent inference events.
* It can't drive cameras (pan-tilt-zoom), can't arm / disarm
  anything, can't speak first. Strictly conversational.
* Latency is "homelab-fine, not real-time" — expect 3-6 seconds
  per round-trip on CPU. Not Alexa-snappy. Defensible for
  "ask your cameras" but not for streaming dialogue.

## Status: preview

Camera-agent ships in v0.1 as a **preview**, not a tested
production path. The architecture, code shape, and tests are all
in place, but three integration points need verification against
your specific adapter versions before the loop runs end-to-end:

1. **Whisper / Piper response field names.** Different adapter
   generations name the transcript field `transcript` / `text` /
   `transcription`, and the synth output as `audio_b64` /
   `audio_uri` / `audio`. The clients in `adapter_clients.py` try
   each in turn, but if your deployed adapters use yet another
   name, STT or TTS will silently no-op — check the adapter's
   response shape and add an alias in `WhisperClient.transcribe`
   or `PiperClient.synthesize`.
2. **WebSocket serializer pairing.** `camera_agent.py` builds the
   transport with Pipecat's `ProtobufFrameSerializer`. The demo
   HTML in `demo/index.html` sends raw PCM `Int16Array` over the
   WebSocket, which will NOT decode against the protobuf serializer.
   Swap one to match: either change the server to a JSON / raw-PCM
   serializer (depends on your Pipecat version), or use Pipecat's
   reference `@pipecat-ai/client-js` library in the browser (which
   knows the protobuf wire format). The demo is here to demonstrate
   shape, not to ship as-is.
3. **BLIP as a KAI-C-registered adapter.** The `describe_camera`
   tool calls a BLIP adapter via KAI-C, but the SDK-based BLIP
   service hasn't shipped in `ai-adapter` yet (only the legacy
   in-tree one exists). Until it does, either route the `caption`
   tool against the legacy BLIP endpoint directly, or set
   `caption_adapter` to a different adapter that returns a
   `caption` field, or temporarily remove `describe_camera` from
   the tool list. `detect_objects` and `recognize_faces` work
   today.

These are tracked for v0.2 in the OpenNVR roadmap. For v0.1 the
example demonstrates the pattern (Pipecat + tool calling against
live cameras) and the tested infrastructure (config loader, frame
cache, event ring, tool definitions, 46 unit tests). The voice
round-trip "just works" once those three integration points are
pinned for your deployment.

## Honesty up front

Real-world limitations the example does NOT yet handle:

* **Audit-chain split.** KAI-C v0.1 only proxies application/json
  inference calls. The streaming voice path (Whisper, Ollama,
  Piper) therefore calls the adapters DIRECTLY — bypassing the
  central KAI-C audit log. Each adapter still records the call
  in its own audit log, so nothing is invisible, but you won't
  see the voice path in KAI-C's central history until v0.2. The
  tool calls into BLIP / YOLOv8 / InsightFace DO go through
  KAI-C and are fully audited.
* **Model quality.** llama3.2:3b is fast on CPU but its tool
  calling is occasionally confused — it'll call `describe_camera`
  when you asked it to count faces. Bumping to llama3.1:8b-instruct
  noticeably improves grounding at ~2x the RAM and slower
  inference. See `config.example.yml`.
* **No memory across sessions.** Each new WebSocket connection
  starts fresh. "What did you tell me yesterday?" won't work.
  Use the `recent_events` tool with a long window for ad-hoc
  recall against NATS history.
* **No real interrupts.** Pipecat's barge-in support is set up
  (`allow_interruptions=True`), but the demo HTML client doesn't
  yet send the right cancel frames when you start talking again.
  Wait for the agent to finish before asking the next thing for
  v0.1.
* **Browser demo is minimal.** ~200 lines of vanilla JS. Audio
  worklets, jitter buffering, transcript display — none of it.
  The intent is to demonstrate the agent shape; production UIs
  should use `@pipecat-ai/client-js`.

## Quick start

```bash
# 1. Start the adapters you need (in the ai-adapter repo). The
#    agent uses six adapters total: Whisper, Ollama, Piper for
#    the voice path; BLIP, YOLOv8, InsightFace for the tool path.
#    The bundled ai-adapter docker-compose covers them all.
cd ai-adapter
docker compose up -d whisper ollama piper blip yolov8 insightface

# 2. Pull a tool-capable LLM into Ollama (one-time, ~2GB).
docker exec ai-adapter-ollama-1 ollama pull llama3.2:3b

# 3. Start KAI-C and register the vision adapters. Ports come from
#    the docker-compose service definitions in ai-adapter — adjust
#    if you remapped them.
cd ../open-nvr/kai-c
INTERNAL_API_KEY=$(openssl rand -hex 32)
AI_SOVEREIGNTY=local_only INTERNAL_API_KEY=$INTERNAL_API_KEY \
  python -m uvicorn main:app --host 0.0.0.0 --port 8100 &

register() {
  local name=$1 url=$2
  curl -X POST http://localhost:8100/api/v1/adapters/register \
    -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"url\":\"$url\"}"
}
register yolov8      http://127.0.0.1:9001
register insightface http://127.0.0.1:9005
register blip        http://127.0.0.1:9002

# 4. Configure
cd ../examples/camera-agent
cp config.example.yml config.yml
# edit config.yml: kaic_api_key, the three streaming-adapter tokens,
# and the camera_id + frame_url for at least one camera

# 5. Run
python camera_agent.py --config config.yml

# 6. Open http://localhost:9100/demo, click Start, and speak.
```

## Try these

* "What's on the front porch?"
* "Is there a person at the front door right now?"
* "Did anyone come to the porch in the last ten minutes?"
* "Who's at the back door?"
* "How many cars are in the driveway?"

## Configure

See `config.example.yml` for the full set. Key knobs:

| Field | Default | Effect |
|---|---|---|
| `llm_model` | `llama3.2:3b` | Tool-capable Ollama model. Bump to llama3.1:8b-instruct for better grounding. |
| `llm_temperature` | `0.4` | Tool calling works best at low-but-not-zero temperatures. |
| `frame_cache_ttl_seconds` | `2.0` | How long to reuse one camera's frame across tool calls in a single LLM turn. |
| `event_ring_size` | `256` | Per-camera ring buffer size for the `recent_events` tool. |
| `nats_inference_url` | unset | Set this to enable `recent_events` against your inference bus. Without it the tool returns "no events". |
| `cameras[].role` | `"(no role configured)"` | One-sentence role description per camera — gets baked into the system prompt so the LLM knows what each camera watches. |

## Tests

```
cd examples/camera-agent
uv sync --extra dev
uv run pytest -q
```

Tests cover:

* Config loader (required fields, numeric coercion, role defaults,
  per-camera roster in system prompt)
* `CameraContext` frame cache (TTL, concurrent fetches, invalidation,
  unknown-camera + missing-source errors, FrameSourceError propagation)
* Event ring (window filter, per-camera filter, ordering, bounded
  size, all-cameras wildcard)
* NATS event parser (subject parsing, fallback for unknown payloads,
  face/object/scene summary phrasing)
* Tool handlers (describe / detect / recognise / recent_events:
  happy path, unknown camera, empty result, adapter exception,
  arg validation, label-count cap)
* Tool definitions (camera enum baked into schema, `__any__`
  wildcard for `recent_events`, sentinel-when-empty)

The Pipecat pipeline assembly itself (`services.py` and
`build_pipeline_task`) is exercised by running the daemon and
talking to it — no unit tests against Pipecat's internals since
those APIs are still maturing.

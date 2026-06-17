# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
camera-agent — a voice agent that grounds its answers in live OpenNVR
camera feeds via tool calling.

Pipeline:
    WebSocket transport (browser ⇄ server raw PCM 16k mono)
        ↓
    SileroVADAnalyzer (turn detection)
        ↓
    OpenNvrWhisperSTT (Whisper adapter → text)
        ↓
    LLM context aggregator (Pipecat)
        ↓
    OpenNvrOllamaLLM (Ollama adapter + 4 registered tools)
        ↓
    OpenNvrPiperTTS (Piper adapter → PCM audio)
        ↓
    WebSocket transport (audio back to browser)

Run:
    python camera_agent.py --config config.yml

Then visit http://localhost:9100/demo in your browser, click "Start",
and speak.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from adapter_clients import (
    KaicAdapterClient,
    OllamaClient,
    PiperClient,
    WhisperClient,
)
from context import CameraContext, CameraSpec, run_event_subscriber
from frame_sources import build_frame_source
from tools import CameraTools, build_tool_definitions

logger = logging.getLogger("camera-agent")


# ── Config ──────────────────────────────────────────────────────────


@dataclass
class AppConfig:
    """Operator-tunable settings. Validated in ``load_config``."""

    # KAI-C (for the vision tool calls — auditable).
    kaic_url: str
    kaic_api_key: str
    detection_adapter: str = "yolov8"
    recognition_adapter: str = "insightface"
    caption_adapter: str = "blip"

    # Direct adapter URLs (streaming voice path — bypasses KAI-C in v0.1).
    whisper_url: str = "http://127.0.0.1:9003"
    whisper_token: str = ""
    ollama_url: str = "http://127.0.0.1:9004"
    ollama_token: str = ""
    piper_url: str = "http://127.0.0.1:9001"
    piper_token: str = ""

    # LLM tuning.
    llm_model: str = "llama3.2:3b"
    llm_temperature: float = 0.4
    llm_max_tokens: int = 256

    # Which tools to advertise to the LLM. None = all. Restricting this
    # shortens the prompt (faster CPU prefill) and stops small models
    # picking tools whose adapters aren't deployed. See build_tool_definitions.
    enabled_tools: list[str] | None = None

    # Caching / event ring.
    frame_cache_ttl_seconds: float = 2.0
    event_ring_size: int = 256

    # Optional NATS for the recent_events tool.
    nats_inference_url: str | None = None
    nats_inference_token: str | None = None

    # Optional path to the footage-search SQLite index. When set and the
    # file exists, the agent gains a ``search_footage`` tool that answers
    # natural-language questions about the recorded past ("did a red
    # truck come by earlier?"). Build the index with the footage-search
    # example's ``index`` subcommand.
    footage_index_path: str | None = None

    # Optional OpenNVR camera discovery. Docker uses this so camera-agent can
    # reuse cameras configured in OpenNVR instead of duplicating RTSP URLs.
    opennvr_cameras_url: str | None = None
    opennvr_api_key: str | None = None

    # HTTP listen address.
    host: str = "127.0.0.1"
    port: int = 9100

    # System prompt + cameras.
    system_prompt: str = ""
    cameras: list[CameraSpec] = None  # type: ignore[assignment]


_DEFAULT_SYSTEM_PROMPT = (
    "You are a concise voice assistant for a home security camera system. "
    "You have NO knowledge of what any camera currently shows — the ONLY way "
    "to know is to call a tool.\n\n"
    "RULES:\n"
    "- For ANY question about what a camera sees, what is happening, who or "
    "what is present, or how many of something, you MUST call detect_objects "
    "or describe_camera BEFORE answering.\n"
    "- NEVER invent, guess, or describe what is on a camera from imagination. "
    "If you have not called a tool this turn, you do not know.\n"
    "- Base your answer ONLY on the tool result, in 1-2 short spoken sentences.\n"
    "- If a tool says a camera cannot be reached, tell the user that camera "
    "appears to be offline."
)


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"config file {path} did not parse to a dict")

    for required in ("kaic_url", "kaic_api_key"):
        if not raw.get(required):
            raise SystemExit(f"config: {required} is required")

    cameras_raw = raw.get("cameras") or []
    cameras: list[CameraSpec] = []
    for entry in cameras_raw:
        if not isinstance(entry, dict):
            raise SystemExit("config: each camera must be a mapping")
        cam_id = entry.get("camera_id")
        url = entry.get("frame_url")
        if not cam_id or not url:
            raise SystemExit("config: camera entries need camera_id + frame_url")
        cameras.append(CameraSpec(
            camera_id=str(cam_id),
            frame_url=str(url),
            role=str(entry.get("role") or "(no role configured)"),
        ))
    if not cameras and raw.get("opennvr_cameras_url"):
        cameras = _load_opennvr_cameras(
            url=str(raw["opennvr_cameras_url"]),
            api_key=str(raw.get("opennvr_api_key") or raw.get("kaic_api_key") or ""),
        )
    # An empty camera list is allowed. The agent still serves the /demo
    # page and runs the full voice loop; vision tools simply report that
    # no cameras are configured until the operator adds some (the Docker
    # install ships ``cameras: []`` so the stack comes up cleanly before
    # any RTSP source is wired). Each entry that IS supplied is still
    # validated above.
    if not cameras:
        logger.warning(
            "config: no cameras configured — the agent will serve the demo "
            "and voice loop, but vision tools will report no cameras until "
            "you add some under 'cameras:' in the config."
        )

    def _str(key: str, default: str) -> str:
        val = raw.get(key, default)
        return str(val) if val is not None else default

    def _float(key: str, default: float) -> float:
        try:
            return float(raw.get(key, default))
        except (TypeError, ValueError):
            raise SystemExit(f"config: {key} must be a number; got {raw.get(key)!r}")

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            raise SystemExit(f"config: {key} must be an integer; got {raw.get(key)!r}")

    return AppConfig(
        kaic_url=str(raw["kaic_url"]),
        kaic_api_key=str(raw["kaic_api_key"]),
        detection_adapter=_str("detection_adapter", "yolov8"),
        recognition_adapter=_str("recognition_adapter", "insightface"),
        caption_adapter=_str("caption_adapter", "blip"),
        whisper_url=_str("whisper_url", "http://127.0.0.1:9003"),
        whisper_token=_str("whisper_token", ""),
        ollama_url=_str("ollama_url", "http://127.0.0.1:9004"),
        ollama_token=_str("ollama_token", ""),
        piper_url=_str("piper_url", "http://127.0.0.1:9001"),
        piper_token=_str("piper_token", ""),
        llm_model=_str("llm_model", "llama3.2:3b"),
        llm_temperature=_float("llm_temperature", 0.4),
        llm_max_tokens=_int("llm_max_tokens", 256),
        enabled_tools=(
            list(raw["enabled_tools"])
            if isinstance(raw.get("enabled_tools"), list)
            else None
        ),
        frame_cache_ttl_seconds=_float("frame_cache_ttl_seconds", 2.0),
        event_ring_size=_int("event_ring_size", 256),
        nats_inference_url=raw.get("nats_inference_url"),
        nats_inference_token=raw.get("nats_inference_token"),
        footage_index_path=raw.get("footage_index_path"),
        opennvr_cameras_url=raw.get("opennvr_cameras_url"),
        opennvr_api_key=raw.get("opennvr_api_key"),
        host=_str("host", "127.0.0.1"),
        port=_int("port", 9100),
        system_prompt=str(raw.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT),
        cameras=cameras,
    )


def _load_opennvr_cameras(*, url: str, api_key: str) -> list[CameraSpec]:
    """Load frame sources from OpenNVR's internal camera-agent endpoint."""
    import httpx

    headers = {"X-Internal-Api-Key": api_key}
    try:
        response = httpx.get(url, headers=headers, timeout=15.0, trust_env=False)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "config: could not load cameras from OpenNVR (%s): %s",
            url,
            exc,
        )
        return []

    raw_cameras = payload.get("cameras") if isinstance(payload, dict) else None
    if not isinstance(raw_cameras, list):
        logger.warning("config: OpenNVR cameras response had no 'cameras' list")
        return []

    cameras: list[CameraSpec] = []
    for entry in raw_cameras:
        if not isinstance(entry, dict):
            continue
        cam_id = entry.get("camera_id")
        frame_url = entry.get("frame_url")
        if not cam_id or not frame_url:
            continue
        role = entry.get("role") or entry.get("name") or "(OpenNVR camera)"
        cameras.append(
            CameraSpec(
                camera_id=str(cam_id),
                frame_url=str(frame_url),
                role=str(role),
            )
        )
    logger.info("config: loaded %d camera(s) from OpenNVR", len(cameras))
    return cameras


# ── Runtime assembly ───────────────────────────────────────────────


class CameraAgentRuntime:
    """Owns the long-lived objects (context, clients, tool registry,
    NATS subscriber). One instance per process; each WebSocket
    conversation builds its own Pipecat pipeline on top of these
    shared pieces so per-call state stays per-call.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

        self.context = CameraContext(
            cameras=cfg.cameras,
            frame_cache_ttl_seconds=cfg.frame_cache_ttl_seconds,
            event_ring_size=cfg.event_ring_size,
        )
        for cam in cfg.cameras:
            self.context.register_frame_source(
                cam.camera_id,
                build_frame_source(camera_id=cam.camera_id, url=cam.frame_url),
            )

        self.whisper = WhisperClient(url=cfg.whisper_url, token=cfg.whisper_token)
        self.ollama = OllamaClient(
            url=cfg.ollama_url, token=cfg.ollama_token, model=cfg.llm_model,
        )
        self.piper = PiperClient(url=cfg.piper_url, token=cfg.piper_token)

        self.caption_client = KaicAdapterClient(
            kaic_url=cfg.kaic_url,
            api_key=cfg.kaic_api_key,
            adapter_name=cfg.caption_adapter,
        )
        self.detection_client = KaicAdapterClient(
            kaic_url=cfg.kaic_url,
            api_key=cfg.kaic_api_key,
            adapter_name=cfg.detection_adapter,
        )
        self.recognition_client = KaicAdapterClient(
            kaic_url=cfg.kaic_url,
            api_key=cfg.kaic_api_key,
            adapter_name=cfg.recognition_adapter,
        )

        # Optional read-only footage-search index → enables search_footage.
        self.footage_index = None
        if cfg.footage_index_path:
            from footage_index import FootageIndex

            self.footage_index = FootageIndex(cfg.footage_index_path)
            if self.footage_index.available:
                logger.info(
                    "camera-agent: footage index loaded from %s; "
                    "search_footage tool enabled",
                    cfg.footage_index_path,
                )
            else:
                logger.info(
                    "camera-agent: footage_index_path set (%s) but the index "
                    "isn't readable yet; search_footage will report it's "
                    "unavailable until the footage-search indexer has run",
                    cfg.footage_index_path,
                )

        self.tools = CameraTools(
            context=self.context,
            caption_client=self.caption_client,
            detection_client=self.detection_client,
            recognition_client=self.recognition_client,
            footage_index=self.footage_index,
        )
        self.tool_definitions = build_tool_definitions(
            [cam.camera_id for cam in cfg.cameras],
            enabled=cfg.enabled_tools,
        )
        self.tool_handlers = {
            "describe_camera": self.tools.describe_camera,
            "detect_objects": self.tools.detect_objects,
            "recognize_faces": self.tools.recognize_faces,
            "search_footage": self.tools.search_footage,
            "recent_events": self.tools.recent_events,
        }

        self._stop_event = asyncio.Event()
        self._subscriber_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────

    async def startup(self) -> None:
        if self.cfg.nats_inference_url:
            self._subscriber_task = asyncio.create_task(
                run_event_subscriber(
                    context=self.context,
                    nats_url=self.cfg.nats_inference_url,
                    nats_token=self.cfg.nats_inference_token,
                    stop_event=self._stop_event,
                ),
                name="camera-agent-nats-subscriber",
            )
            logger.info(
                "camera-agent: NATS subscriber started on %s",
                self.cfg.nats_inference_url,
            )
        else:
            logger.info(
                "camera-agent: NATS not configured; recent_events tool "
                "will always report 'no events'"
            )

        # Pre-warm the LLM in the background so the FIRST real question
        # doesn't pay the ~80s cold-load (Ollama loads the model into RAM
        # + prefills on first inference). We fire a throwaway one-token
        # chat; with OLLAMA_KEEP_ALIVE=-1 the model then stays resident.
        # Best-effort: failures here must never block startup.
        self._warmup_task = asyncio.create_task(
            self._prewarm_llm(), name="camera-agent-llm-prewarm"
        )

    async def _prewarm_llm(self) -> None:
        try:
            logger.info(
                "camera-agent: pre-warming LLM (loading model + caching the "
                "system+tools prompt prefix)…"
            )
            # Mirror the real turn's prompt shape (system prompt + tool
            # definitions) so Ollama caches that prefix's KV. Subsequent real
            # turns share the identical system+tools prefix, so even the FIRST
            # question skips the expensive cold prefill — not just the model
            # weight load. (KEEP_ALIVE=-1 keeps the cache resident.)
            await self.ollama.chat(
                messages=[
                    {"role": "system", "content": self.build_system_prompt()},
                    {"role": "user", "content": "hello"},
                ],
                tools=self.tool_definitions,
                max_tokens=1,
            )
            logger.info("camera-agent: LLM warm — first question will be fast.")
        except Exception as exc:
            logger.warning(
                "camera-agent: LLM pre-warm failed (%s); the first question "
                "will pay the cold-start cost instead.",
                exc,
            )

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._subscriber_task is not None:
            try:
                await asyncio.wait_for(self._subscriber_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._subscriber_task.cancel()
            except Exception:
                logger.exception("subscriber shutdown raised")
        # Close all reusable HTTP clients so pytest / uvicorn don't
        # log warnings about unclosed AsyncClient instances on exit.
        await asyncio.gather(
            self.whisper.aclose(),
            self.ollama.aclose(),
            self.piper.aclose(),
            self.caption_client.aclose(),
            self.detection_client.aclose(),
            self.recognition_client.aclose(),
            return_exceptions=True,
        )

    # ── System prompt construction ────────────────────────────────

    def build_system_prompt(self) -> str:
        """Compose the system prompt the LLM sees: operator's base
        prompt + a per-camera roster so the model can pick the right
        ``camera_id`` from natural-language references."""
        roster = "\n".join(
            f"- {cam.camera_id}: {cam.role}" for cam in self.cfg.cameras
        )
        return (
            f"{self.cfg.system_prompt.strip()}\n\n"
            f"Cameras available to you:\n{roster}\n\n"
            f"Always pass one of the camera_id values exactly as listed "
            f"when calling a tool."
        )


# ── Pipecat pipeline factory ───────────────────────────────────────


def build_pipeline_task(runtime: CameraAgentRuntime, transport: Any) -> Any:
    """Construct one Pipecat pipeline per WebSocket conversation.
    Imported here (not at module top) so the camera-agent module
    stays importable in test environments without Pipecat."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.openai_llm_context import (
        OpenAILLMContext,
    )
    # Context-aware aggregators (not the message-list variants).
    # The plain LLMUserResponseAggregator / LLMAssistantResponseAggregator
    # accept a ``List[dict]`` and call .append() on it; passing an
    # OpenAILLMContext to those crashes with AttributeError on the
    # first turn. The *Context* variants below take ``context=...``
    # and route .add_message() correctly, which also mirrors the
    # final assistant turn back into the context for observers.
    from pipecat.processors.aggregators.llm_response import (
        LLMUserContextAggregator,
        LLMAssistantContextAggregator,
    )

    from services import (
        OpenNvrOllamaLLM,
        OpenNvrPiperTTS,
        OpenNvrWhisperSTT,
    )

    stt = OpenNvrWhisperSTT(client=runtime.whisper)
    llm = OpenNvrOllamaLLM(
        client=runtime.ollama,
        tools=runtime.tool_definitions,
        tool_handlers=runtime.tool_handlers,
        temperature=runtime.cfg.llm_temperature,
        max_tokens=runtime.cfg.llm_max_tokens,
    )
    tts = OpenNvrPiperTTS(client=runtime.piper)

    context = OpenAILLMContext(messages=[
        {"role": "system", "content": runtime.build_system_prompt()},
    ])

    user_agg = LLMUserContextAggregator(context=context)
    assistant_agg = LLMAssistantContextAggregator(context=context)

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])

    return PipelineTask(
        pipeline,
        params=PipelineParams(
            # Interruptions are DISABLED for v0.1: the bundled demo client
            # doesn't send proper cancel frames, so any speech/noise while
            # the agent is thinking would otherwise cancel the in-flight
            # reply before it reaches TTS. With this off, the agent always
            # finishes its answer, then listens again. (See README "No real
            # interrupts".)
            allow_interruptions=False,
            enable_metrics=True,
        ),
    )


# ── FastAPI app + WebSocket entry point ────────────────────────────


def build_app(runtime: CameraAgentRuntime) -> FastAPI:
    app = FastAPI(title="OpenNVR camera-agent", version="1.0.0")

    @app.on_event("startup")
    async def _startup() -> None:
        await runtime.startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await runtime.shutdown()

    @app.get("/health")
    async def _health() -> dict[str, Any]:
        return {
            "status": "ok",
            "cameras": [cam.camera_id for cam in runtime.cfg.cameras],
            "tools": list(runtime.tool_handlers.keys()),
            "llm_model": runtime.cfg.llm_model,
        }

    @app.get("/demo", response_class=HTMLResponse)
    async def _demo() -> HTMLResponse:
        return HTMLResponse(_load_demo_html())

    # Demo-local conversation memory (single user). Kept tiny and turn-text
    # only; reset via POST /reset. A multi-user UI would key this per session.
    demo_history: list[dict[str, str]] = []
    _MAX_HISTORY_TURNS = 8  # 4 user + 4 assistant

    @app.post("/reset")
    async def _reset() -> dict[str, str]:
        demo_history.clear()
        return {"status": "ok"}

    @app.post("/converse")
    async def _converse(request: Request) -> JSONResponse:
        """Push-to-talk turn: audio blob in → {transcript, reply, audio_b64} out."""
        blob = await request.body()
        if not blob:
            return JSONResponse(
                {"error": "empty audio"}, status_code=400
            )

        # 1) Normalise the recording to 16 kHz mono WAV (ffmpeg handles
        #    whatever container MediaRecorder produced).
        try:
            wav = await asyncio.to_thread(_transcode_to_wav16k, blob)
        except Exception as exc:
            logger.warning("converse: transcode failed: %s", exc)
            return JSONResponse({"error": "could not decode audio"}, status_code=400)

        # 2) Transcribe.
        try:
            transcript = (await runtime.whisper.transcribe(wav)).strip()
        except Exception:
            logger.exception("converse: STT failed")
            return JSONResponse({"error": "transcription failed"}, status_code=502)
        logger.info("converse: transcript=%r", transcript)
        if not transcript:
            # Nothing intelligible — tell the UI so it can prompt a retry
            # instead of sending the LLM an empty turn.
            return JSONResponse({"transcript": "", "reply": "", "audio_b64": None})

        # 3) LLM tool-calling loop.
        try:
            reply = await _run_conversation_turn(runtime, demo_history, transcript)
        except Exception:
            logger.exception("converse: LLM turn failed")
            return JSONResponse({"error": "assistant failed"}, status_code=502)
        logger.info("converse: reply=%r", reply[:160])

        # Persist this turn's text (bounded).
        demo_history.append({"role": "user", "content": transcript})
        demo_history.append({"role": "assistant", "content": reply})
        del demo_history[:-_MAX_HISTORY_TURNS]

        # 4) Synthesise the reply.
        audio_b64 = None
        try:
            audio = await runtime.piper.synthesize(reply)
            if audio:
                audio_b64 = base64.b64encode(audio).decode("ascii")
        except Exception:
            logger.exception("converse: TTS failed")  # text still returned

        return JSONResponse(
            {"transcript": transcript, "reply": reply, "audio_b64": audio_b64}
        )

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket) -> None:
        # The ``websocket: WebSocket`` annotation is REQUIRED — without it
        # FastAPI treats ``websocket`` as a query parameter and rejects the
        # handshake with 403 Forbidden before this handler runs.
        # Lazy-imported so the module loads without Pipecat installed.
        from pipecat.transports.network.fastapi_websocket import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from serializer import RawPcmSerializer

        await websocket.accept()
        # RawPcmSerializer is camera-agent-local — it speaks raw int16
        # PCM on both directions of the WebSocket so the self-contained
        # /demo HTML page can use vanilla JS + AudioContext without
        # bundling the Pipecat JS client. Production deployments can
        # swap to ProtobufFrameSerializer + @pipecat-ai/client-js for
        # richer frame types (transcripts, control frames, etc.).
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_in_sample_rate=16000,
                audio_in_channels=1,
                audio_out_enabled=True,
                audio_out_sample_rate=22050,
                audio_out_channels=1,
                add_wav_header=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(
                    sample_rate=16000,
                    params=VADParams(
                        confidence=0.55,
                        start_secs=0.15,
                        stop_secs=0.7,
                        min_volume=0.08,
                    ),
                ),
                vad_audio_passthrough=True,
                serializer=RawPcmSerializer(),
            ),
        )

        task = build_pipeline_task(runtime, transport)
        from pipecat.pipeline.runner import PipelineRunner
        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        except Exception:
            logger.exception("websocket conversation crashed")

    return app


# ── Request/response conversation turn (push-to-talk path) ─────────
#
# The /converse endpoint below is the reliable, demo-friendly path: the
# browser records ONE complete utterance with MediaRecorder and POSTs the
# whole blob. We transcode it to clean 16 kHz mono WAV with ffmpeg (so the
# input format never matters), transcribe it, run the tool-calling LLM loop,
# synthesise the reply, and return text + audio in one JSON response. No
# streaming, no custom resampler, no server-side VAD — none of the moving
# parts that made the live WebSocket path hallucinate.


def _transcode_to_wav16k(blob: bytes) -> bytes:
    """Any audio container (WebM/Opus, Ogg, MP4, …) → 16 kHz mono PCM WAV.

    MediaRecorder emits whatever the browser supports (usually
    ``audio/webm;codecs=opus``); ffmpeg normalises it to exactly what
    Whisper wants. Raises on failure so the caller can report it cleanly.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-i", "pipe:0",
            "-ar", "16000", "-ac", "1",
            "-f", "wav", "pipe:1",
        ],
        input=blob,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(f"ffmpeg transcode failed: {err or 'no output'}")
    return proc.stdout


async def _invoke_tool(runtime: "CameraAgentRuntime", call: dict[str, Any]) -> tuple[str, str]:
    """Run one tool call; return (name, result_string). Never raises."""
    func = call.get("function") or {}
    name = str(func.get("name") or "").strip()
    args_raw = func.get("arguments")
    try:
        if isinstance(args_raw, str):
            args = json.loads(args_raw) if args_raw.strip() else {}
        elif isinstance(args_raw, dict):
            args = dict(args_raw)
        else:
            args = {}
    except (json.JSONDecodeError, ValueError):
        return name or "<unknown>", f"ERROR: tool '{name}' received malformed arguments."
    handler = runtime.tool_handlers.get(name)
    if handler is None:
        return name, f"ERROR: tool '{name}' is not registered."
    try:
        result = await handler(args)
    except Exception:
        logger.exception("Tool %s raised", name)
        return name, f"ERROR: tool '{name}' failed unexpectedly."
    result = str(result)
    if len(result) > 1200:
        result = result[:1200] + " …(truncated)"
    return name, result


# Words that mean "this is a question about a camera / the scene". If the
# model answers an utterance containing any of these WITHOUT calling a tool,
# we force a grounding detection (see _run_conversation_turn). Positive
# matching (vs a chit-chat blocklist) avoids force-grounding closings like
# "thanks, that's all" while still catching "is anyone there?".
_CAMERA_WORDS: tuple[str, ...] = (
    "see", "look", "watch", "watching", "camera", "cam",
    "anyone", "anybody", "someone", "somebody", "nobody",
    "person", "people", "man", "woman", "kid", "child", "face",
    "door", "porch", "outside", "yard", "driveway", "garage", "street",
    "happening", "detect", "count", "package", "parcel", "delivery",
    "dog", "cat", "animal", "car", "cars", "truck", "vehicle", "bike",
    "visible", "present", "moving", "movement", "motion",
)
_CAMERA_RE = re.compile(r"\b(" + "|".join(_CAMERA_WORDS) + r")\b", re.IGNORECASE)


def _looks_like_camera_question(text: str) -> bool:
    """True if the utterance is about a camera / the scene. Used only to
    decide whether to force a grounding detection when the model failed to
    call a tool itself — so a weak model can't fabricate "I see a dog"."""
    return bool(_CAMERA_RE.search(text or ""))


def _pick_camera(text: str, cameras: list[str]) -> str:
    """Best-effort: which camera did the user mean? Falls back to the first
    configured camera when unspecified."""
    t = text.lower().replace("-", " ")
    compact = t.replace(" ", "")
    for cam in cameras:
        if cam.lower() in compact:  # "cam1", "camera1"
            return cam
    words = {"one": "1", "two": "2", "three": "3", "four": "4",
             "first": "1", "second": "2", "third": "3"}
    for word, n in words.items():
        if re.search(rf"\b{word}\b", t) and f"cam{n}" in cameras:
            return f"cam{n}"
    for n in ("1", "2", "3", "4"):
        if re.search(rf"\b{n}\b", t) and f"cam{n}" in cameras:
            return f"cam{n}"
    return cameras[0]


async def _run_conversation_turn(
    runtime: "CameraAgentRuntime",
    history: list[dict[str, str]],
    user_text: str,
    *,
    max_iterations: int = 4,
) -> str:
    """Run the tool-calling LLM loop for one user utterance and return the
    final spoken reply. ``history`` holds prior user/assistant text turns
    (tool internals are kept turn-local, not persisted).

    Anti-fabrication guard: small CPU models sometimes answer a camera
    question straight from the prompt ("I see a dog") without calling a
    tool. If that happens we FORCE a real detection on the target camera
    and make the model answer from that result — so a reply about a camera
    is always grounded in an actual frame, never imagined.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": runtime.build_system_prompt()}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    cameras = [cam.camera_id for cam in runtime.cfg.cameras]
    final = ""
    grounded = False   # did any tool actually run this turn?
    forced = False     # have we already injected a forced detection?
    for iteration in range(max_iterations):
        response = await runtime.ollama.chat(
            messages=messages,
            tools=runtime.tool_definitions,
            temperature=runtime.cfg.llm_temperature,
            max_tokens=runtime.cfg.llm_max_tokens,
        )
        message = response.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()
        logger.info(
            "converse: LLM iter %d content=%r tool_calls=%d",
            iteration, content[:120], len(tool_calls),
        )

        if tool_calls:
            grounded = True
            messages.append({
                "role": "assistant", "content": content, "tool_calls": tool_calls,
            })
            for call in tool_calls:
                name, result = await _invoke_tool(runtime, call)
                logger.info("converse: tool %s -> %s", name, result[:120])
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": result,
                })
            continue

        # No tool call. If the model tried to answer a camera question
        # without looking — judged from EITHER the user's question or the
        # model's own reply mentioning a camera/scene — force a grounding
        # detection and re-ask. Checking the reply too catches cases where
        # STT garbled the camera word (e.g. "what's on hammer 2") but the
        # model still fabricated "camera 2 is ...".
        if (
            not grounded and not forced and cameras
            and (
                _looks_like_camera_question(user_text)
                or _looks_like_camera_question(content)
            )
        ):
            forced = True
            grounded = True
            cam = _pick_camera(user_text, cameras)
            call = {
                "id": "forced-0", "type": "function",
                "function": {"name": "detect_objects",
                             "arguments": {"camera_id": cam}},
            }
            name, result = await _invoke_tool(runtime, call)
            logger.info("converse: FORCED grounding on %s -> %s", cam, result[:120])
            messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            messages.append({
                "role": "tool", "tool_call_id": "forced-0",
                "name": name, "content": result,
            })
            continue

        # Accept the reply (genuine chit-chat, or already grounded).
        final = content
        break
    else:
        logger.warning("converse: tool loop exhausted")

    return final or "Sorry, I'm having trouble answering that right now."


def _load_demo_html() -> str:
    """Read the static demo page off disk. Kept as a separate file
    so designers can iterate on the HTML without restarting Python."""
    path = Path(__file__).parent / "demo" / "index.html"
    if not path.is_file():
        return "<h1>demo/index.html missing</h1>"
    return path.read_text(encoding="utf-8")


# ── CLI entry point ────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "OpenNVR camera-agent — voice agent grounded in live cameras."
        )
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument("--log-level", default="INFO", help="Python log level")
    return parser.parse_args(argv)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.log_level)
    cfg = load_config(args.config)
    runtime = CameraAgentRuntime(cfg)
    app = build_app(runtime)

    import uvicorn
    config = uvicorn.Config(
        app, host=cfg.host, port=cfg.port, log_level=args.log_level.lower()
    )
    server = uvicorn.Server(config)

    def _sig(signum: int, frame: Any) -> None:
        logger.info("received signal %d; stopping", signum)
        server.should_exit = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

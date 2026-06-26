# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
HTTP clients for the OpenNVR adapters the camera-agent uses.

Two posture choices baked into v0.1:

* The **streaming voice path** (Whisper STT, Ollama LLM, Piper TTS)
  talks to the adapters directly with bearer-token auth. KAI-C v0.1
  doesn't proxy streaming yet (its `/api/v1/infer` is JSON-only and
  blocking), so until the streaming proxy lands these three calls
  bypass the central audit chain. Each adapter still records the
  call in its own audit log.

* The **tool calls** (BLIP scene captions, YOLOv8 object detection,
  InsightFace recognition) flow THROUGH KAI-C with the
  X-Internal-Api-Key header — same shape as smart-doorbell / LPR /
  package-delivery. These are event-driven not streaming so the
  JSON-only proxy is fine.

This split is documented in the README's "Audit chain" section and
in config.example.yml so operators understand what's auditable and
what isn't until v0.2.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


CORRELATION_ID_HEADER = "X-Correlation-Id"


# ── KAI-C client (audit-chain tools) ───────────────────────────────


class _ReusableClientMixin:
    """One ``httpx.AsyncClient`` per service instance — avoids paying
    TCP + TLS setup for every adapter call inside a tool-heavy LLM
    turn. The client is lazily constructed because that lets the
    instance be created at config-load time and only spin up the
    underlying connection pool when the first call actually fires."""

    _timeout: float
    _http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


class KaicAdapterClient(_ReusableClientMixin):
    """JSON+base64 POST to KAI-C's ``/api/v1/infer/{adapter_name}``.

    Mirrors the wire shape used by smart-doorbell / LPR / package-
    delivery: ``frame_b64`` for image bytes plus any extra params
    the adapter expects on its top-level payload (task, threshold,
    etc.). The SDK body parser unwraps these into the service.
    """

    def __init__(
        self,
        *,
        kaic_url: str,
        api_key: str,
        adapter_name: str,
        timeout_seconds: float = 120.0,
        retries: int = 2,
        retry_backoff_s: float = 1.5,
    ) -> None:
        self._url = f"{kaic_url.rstrip('/')}/api/v1/infer/{adapter_name}"
        self._api_key = api_key
        self._timeout = timeout_seconds
        # Brief retry bridges the adapter cold-start window: right after
        # boot the model may still be loading / its weights still
        # downloading, so the first inference can 5xx or drop the
        # connection. Retrying a couple of times avoids surfacing that to
        # the user as "camera offline" until someone restarts the adapter.
        self._retries = max(0, retries)
        self._retry_backoff_s = retry_backoff_s

    async def infer(
        self,
        *,
        frame_jpeg: bytes,
        extra: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-Internal-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        body: dict[str, Any] = {
            "frame_b64": base64.b64encode(frame_jpeg).decode("ascii"),
        }
        if extra:
            body.update(extra)

        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client().post(self._url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._retries:
                    logger.warning(
                        "KAI-C infer attempt %d/%d failed (%s); retrying in %.1fs "
                        "(adapter may still be warming up)",
                        attempt + 1, self._retries + 1, exc, self._retry_backoff_s,
                    )
                    await asyncio.sleep(self._retry_backoff_s)
        assert last_exc is not None
        raise last_exc


class SyntheticDetectionClient:
    """Demo detector: instead of calling KAI-C/YOLOv8, it reads the ground-truth
    scene a ``SyntheticFrameSource`` embedded in the frame and returns matching
    detections. Lets the whole agent run with no cameras/adapters so the demo is
    deterministic and recordable. Clearly a DEMO path — not real inference."""

    async def infer(
        self,
        *,
        frame_jpeg: bytes,
        extra: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        from frame_sources import synth_detections_from_frame
        return {"result": {"detections": synth_detections_from_frame(frame_jpeg)}}

    async def aclose(self) -> None:  # parity with KaicAdapterClient
        return None


# ── Adapter-direct clients (streaming voice path) ──────────────────


class WhisperClient(_ReusableClientMixin):
    """Direct call to the Whisper adapter's ``/infer`` endpoint with
    a base64-encoded audio chunk. Returns the transcribed text.

    The Whisper adapter accepts WAV / Opus / MP3 / FLAC. We POST raw
    bytes and let the adapter handle format detection."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._url = url.rstrip("/") + "/infer"
        self._token = token
        self._timeout = timeout_seconds

    async def transcribe(self, audio_bytes: bytes) -> str:
        # Only send Authorization when a token is configured. Native
        # Ollama needs no auth and ``ollama_token`` is empty; an empty
        # ``Bearer `` value (trailing space, no token) is rejected by
        # httpx as an illegal header (LocalProtocolError) before the
        # request is even sent.
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # FLAT body — the deployed standalone SDK whisper-adapter parses
        # ``audio_b64`` and the params (task/language/vad_filter) at the
        # TOP LEVEL of the JSON object (opennvr_adapter_sdk AUDIO body
        # shape). The legacy combined ai-adapter took a {task, input:{...}}
        # envelope; nesting under ``input`` here makes the SDK parser fail
        # to find ``audio_b64`` and return 400 "JSON body must include
        # 'audio_b64'".
        body = {
            "task": "audio_transcription",
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "language": "en",
            # vad_filter OFF: the agent already VAD-segments each
            # utterance (Pipecat Silero + the force-stop logic in
            # services.py) before it reaches here, so the clip is
            # ALREADY trimmed speech. Running Whisper's own Silero VAD
            # again was re-classifying the short, mic-quiet clips as
            # non-speech and discarding the whole utterance ("returned
            # no segments"). The hallucination-token filter in
            # services.py handles the 'You'/'Thank you' noise case.
            "vad_filter": False,
        }
        resp = await self._client().post(self._url, json=body, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        # Log full diagnostic info so we can see no_speech_prob per segment.
        segments = result.get("segments") or []
        if segments:
            seg_info = [(s.get("text", ""), s.get("no_speech_prob")) for s in segments]
            logger.info("Whisper segments: %r", seg_info)
        else:
            logger.info("Whisper returned no segments (vad_filter likely removed all audio)")
        text = (
            result.get("text")
            or result.get("transcript")
            or result.get("transcription")
            or ""
        )
        text = str(text).strip()
        # no_speech_prob gate: Whisper flags hallucinated noise ('You',
        # 'Thank you', …) with a high no_speech_prob. Real speech sits low
        # (~0.1); noise sits high (~0.75+). When EVERY segment is above the
        # gate, treat the whole clip as non-speech and drop it. This is more
        # robust than matching a fixed token list because it also catches
        # hallucinations we didn't enumerate. Segments with no probability
        # reported are treated as speech (fail-open) so we never silently
        # eat a real transcript.
        if text and segments:
            probs = [
                s.get("no_speech_prob")
                for s in segments
                if isinstance(s.get("no_speech_prob"), (int, float))
            ]
            if probs and all(p > 0.6 for p in probs):
                logger.info(
                    "Whisper transcript %r dropped: all segments non-speech "
                    "(no_speech_prob=%r)",
                    text, probs,
                )
                return ""
        return text


class OllamaClient(_ReusableClientMixin):
    """Direct call to the Ollama adapter's ``/infer`` endpoint. Uses
    the OpenAI-style tool-calling shape that landed in S5-prereq —
    sends ``messages`` + ``tools`` and reads back either
    ``message.content`` or ``message.tool_calls``."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        model: str,
        # First CPU inference cold-loads the model + processes a tool-heavy
        # prompt; keep this comfortably above the adapter-side timeout so the
        # first turn completes instead of being cut off.
        timeout_seconds: float = 300.0,
        # Limited-hardware knobs. num_thread caps CPU cores Ollama uses (None =
        # all cores; set e.g. 2 to keep the rest of the machine responsive).
        # num_ctx sizes the context window: 4096 holds the full tool prompt;
        # lower it (e.g. 2048, when enabled_tools keeps the prompt short) to
        # save RAM and speed up prefill on weak boxes.
        num_thread: int | None = None,
        num_ctx: int = 4096,
    ) -> None:
        # Talk to Ollama's NATIVE chat API. The deployment points
        # ``ollama_url`` at the raw ollama runtime (http://ollama:11434),
        # which exposes /api/chat — NOT the SDK /infer envelope endpoint.
        # The response/tool-call consumer (_run_conversation_turn /
        # _invoke_tool) already expects the native shape: top-level
        # ``message`` with ``content`` + ``tool_calls[].function.arguments``.
        self._url = url.rstrip("/") + "/api/chat"
        self._token = token
        self._model = model
        self._timeout = timeout_seconds
        self._num_thread = num_thread
        self._num_ctx = num_ctx

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        # Only send Authorization when a token is configured. Native
        # Ollama needs no auth and ``ollama_token`` is empty; an empty
        # ``Bearer `` value (trailing space, no token) is rejected by
        # httpx as an illegal header (LocalProtocolError) before the
        # request is even sent.
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # Native Ollama /api/chat body. ``stream: false`` so we get one
        # complete JSON object back (the response carries ``message`` with
        # ``content`` and ``tool_calls`` at the top level). temperature /
        # max_tokens live under ``options`` (num_predict) per the native
        # API — they are NOT top-level chat fields.
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                # num_ctx MUST exceed the full system+tools+history prompt.
                # Ollama's default is 2048, but the tool-calling prompt runs
                # ~2200 tokens, so it was being silently TRUNCATED every call
                # (logged as "truncating input prompt"). Truncation (a) drops
                # context and (b) shifts the prefix window so the prewarmed
                # KV cache no longer matches — forcing a full ~2k-token CPU
                # re-prefill (~110s) on EVERY call instead of reusing cache.
                # 4096 holds the whole prompt so the prefix stays stable and
                # the prewarm + iter0→iter1 cache reuse actually kick in.
                "num_ctx": self._num_ctx,
            },
        }
        if self._num_thread:
            # Cap CPU cores so the LLM doesn't peg a limited machine.
            body["options"]["num_thread"] = self._num_thread
        if tools:
            # Native /api/chat decides tool use automatically; there is no
            # ``tool_choice`` field (it would be ignored). Just advertise
            # the tools.
            body["tools"] = tools
        resp = await self._client().post(self._url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


class OpenAILLMClient(_ReusableClientMixin):
    """Cloud/hybrid LLM brain via any OpenAI-compatible chat API — OpenAI,
    Groq, Together, OpenRouter, or a local OpenAI-API server (vLLM, llama.cpp,
    LM Studio, Ollama's /v1). Used when ``llm_provider: openai`` so the agent
    gets stronger, lower-latency tool-calling without loading a local LLM
    (issue #82). Normalises the response to the same ``{"message": {...}}``
    shape the turn loop expects."""

    def __init__(self, *, base_url: str, api_key: str | None, model: str,
                 timeout_seconds: float = 120.0) -> None:
        url = base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            self._url = url
        elif url.endswith("/v1"):
            self._url = url + "/chat/completions"
        else:
            self._url = url + "/v1/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body: dict[str, Any] = {
            "model": self._model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        resp = await self._client().post(self._url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {
            "role": "assistant", "content": ""
        }
        # OpenAI omits content when only tool_calls are returned; normalise to "".
        if message.get("content") is None:
            message["content"] = ""
        return {"message": message}


class PiperClient(_ReusableClientMixin):
    """Direct call to the Piper adapter's ``/infer`` endpoint. Returns
    raw audio bytes (WAV format from Piper by default)."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._url = url.rstrip("/") + "/infer"
        self._token = token
        self._timeout = timeout_seconds

    async def synthesize(self, text: str) -> bytes:
        # Only send Authorization when a token is configured. Native
        # Ollama needs no auth and ``ollama_token`` is empty; an empty
        # ``Bearer `` value (trailing space, no token) is rejected by
        # httpx as an illegal header (LocalProtocolError) before the
        # request is even sent.
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # task name varies across adapter generations — legacy used
        # ``speech_synthesis``; the contract §5.4 spec is ``text_to_speech``.
        # Send the modern name; legacy operators upgrade the adapter.
        #
        # ``inline: true`` asks the SDK Piper service to base64-encode
        # the generated WAV into ``result.audio_b64`` alongside the
        # default ``result.audio_uri``. We don't have a shared
        # filesystem mount to dereference the opennvr://audio/...
        # URI, so the inline body is the only way we get the audio
        # bytes back over plain HTTP.
        # FLAT body — the deployed standalone SDK piper-adapter reads
        # ``text`` (and the ``inline`` flag) at the TOP LEVEL of the JSON
        # body, not nested under an ``input`` envelope. The adapter task is
        # ``speech_synthesis`` (not ``text_to_speech``); ``inline: true``
        # asks it to return the WAV bytes base64-encoded since we have no
        # shared audio mount. Nesting under ``input`` made the service see
        # no top-level ``text`` and return 400 "Field 'text' is required".
        body = {
            "task": "speech_synthesis",
            "text": text,
            "inline": True,
        }
        client = self._client()
        resp = await client.post(self._url, json=body, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        # Combined /infer returns SpeechSynthesisResponse at the top level;
        # per-adapter shapes nest it under "result". Accept both.
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        # Two response shapes in flight: inline base64 audio for
        # streaming-friendly clients, and a server-side URI for
        # bandwidth-conscious deployments. Prefer inline; fall back
        # to fetching the URI if the adapter only emitted one.
        audio_b64 = result.get("audio_b64") or result.get("audio")
        if audio_b64:
            return base64.b64decode(audio_b64)
        audio_uri = result.get("audio_uri") or result.get("uri")
        if audio_uri:
            # Adapter-controlled URI — could point anywhere. Two
            # safety layers:
            #
            #  1. Resolve relative URIs (e.g. ``/audio/abc.wav``)
            #     against the adapter base URL so httpx has an
            #     absolute target. Without this, an empty scheme /
            #     netloc would short-circuit the same-origin check
            #     AND then crash the GET because the shared client
            #     has no ``base_url`` set.
            #  2. Only forward the bearer token when the resolved
            #     URI is same-origin with the adapter we trust.
            #     An attacker-tampered response pointing at
            #     evil.example.com MUST NOT leak the token.
            from urllib.parse import urljoin, urlparse
            resolved_uri = urljoin(self._url, audio_uri)
            adapter_origin = urlparse(self._url)
            uri_origin = urlparse(resolved_uri)
            same_origin = (
                uri_origin.scheme == adapter_origin.scheme
                and uri_origin.netloc.lower() == adapter_origin.netloc.lower()
            )
            secondary_headers: dict[str, str] = {}
            if same_origin:
                secondary_headers["Authorization"] = headers["Authorization"]
            try:
                audio_resp = await client.get(resolved_uri, headers=secondary_headers)
                audio_resp.raise_for_status()
                return audio_resp.content
            except Exception:
                logger.exception(
                    "Piper adapter returned audio_uri %s (resolved %s) but fetch failed",
                    audio_uri, resolved_uri,
                )
                return b""
        # Common cause: adapter received the inline=true flag but
        # the underlying audio_uri couldn't be resolved (Piper's
        # _read_audio_inline swallows resolve_audio_uri failures and
        # falls back to None, so we end up here with neither field
        # set). Check the adapter's logs for "could not resolve
        # audio_uri" or "could not read audio file" entries.
        logger.warning(
            "Piper adapter response contained no audio_b64 nor audio_uri; "
            "if inline=true was sent, check adapter logs for resolve "
            "or read failures"
        )
        return b""

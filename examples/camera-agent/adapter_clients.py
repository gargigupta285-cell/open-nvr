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
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        body = {
            "task": "audio_transcription",
            "input": {
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
            },
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
    ) -> None:
        self._url = url.rstrip("/") + "/infer"
        self._token = token
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
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        # Combined /infer takes the SDK envelope {task, input:{...}}; the
        # chat fields go inside ``input``. The response carries
        # ``message`` (incl. ``tool_calls``) at the top level.
        inner: dict[str, Any] = {
            "messages": messages,
            "model": self._model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            inner["tools"] = tools
            # Explicit ``auto`` since 3B-class models otherwise
            # sometimes ignore the tools list when uncertain.
            inner["tool_choice"] = "auto"
        body = {"task": "chat_completion", "input": inner}
        resp = await self._client().post(self._url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


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
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
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
        # Combined /infer envelope. The adapter task is ``speech_synthesis``
        # (not ``text_to_speech``); ``inline: true`` asks it to return the
        # WAV bytes base64-encoded since we have no shared audio mount.
        body = {
            "task": "speech_synthesis",
            "input": {"text": text, "inline": True},
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

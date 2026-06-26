# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Minimal raw-PCM WebSocket serializer for Pipecat.

The default ``ProtobufFrameSerializer`` Pipecat ships expects
length-prefixed protobuf-encoded frames on the wire. That's the right
shape for production deployments using the official
``@pipecat-ai/client-js`` library, which knows the wire format.

For the camera-agent's self-contained ``/demo`` HTML page we don't
want to bundle the Pipecat JS client (npm dep, build step, larger
demo footprint). Instead we run a tiny raw-audio protocol:

  * **Inbound** (browser → server): bare PCM int16 little-endian
    samples at 16 kHz mono. The browser captures via the Web Audio
    API and ships raw ``Int16Array`` chunks over the WebSocket.

  * **Outbound** (server → browser): bare PCM int16 little-endian
    samples at whatever sample rate the TTS service produces
    (typically 22050). The browser plays it back through
    ``AudioContext.decodeAudioData`` after wrapping with a synthetic
    WAV header — or, in the demo here, by feeding samples directly
    into an ``AudioBufferSourceNode``.

Frames that aren't audio (transcripts, system frames, control
frames) are dropped on the wire — the demo doesn't render them. A
production UI would either upgrade to ``ProtobufFrameSerializer``
plus the Pipecat JS client OR sidecar a JSON message channel here.

This file is camera-agent-local; the same shape would fit any
Pipecat-driven adapter that wants a self-contained demo without
the JS client.
"""
from __future__ import annotations

import logging
from typing import Optional

# Wrapped in try so the module remains importable in test environments
# that don't have Pipecat installed.
try:  # pragma: no cover — import-time only
    from pipecat.frames.frames import (
        Frame,
        InputAudioRawFrame,
        OutputAudioRawFrame,
        StartFrame,
        TTSAudioRawFrame,
    )
    from pipecat.serializers.base_serializer import (
        FrameSerializer,
        FrameSerializerType,
    )
except Exception:  # pragma: no cover
    # Test envs that haven't installed Pipecat get a no-op stub.
    Frame = object  # type: ignore
    InputAudioRawFrame = object  # type: ignore
    OutputAudioRawFrame = object  # type: ignore
    StartFrame = object  # type: ignore
    TTSAudioRawFrame = object  # type: ignore

    class FrameSerializer:  # type: ignore
        pass

    class FrameSerializerType:  # type: ignore
        BINARY = "binary"
        TEXT = "text"


logger = logging.getLogger(__name__)


class RawPcmSerializer(FrameSerializer):
    """Inbound: raw int16 PCM bytes → ``InputAudioRawFrame``.
    Outbound: ``TTSAudioRawFrame`` → raw int16 PCM bytes.
    Everything else is dropped (returns None).
    """

    def __init__(
        self,
        *,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 22050,
        num_channels: int = 1,
    ) -> None:
        self._input_sample_rate = input_sample_rate
        self._output_sample_rate = output_sample_rate
        self._num_channels = num_channels

    @property
    def type(self):  # type: ignore[override]
        return FrameSerializerType.BINARY

    async def setup(self, frame: "StartFrame") -> None:  # type: ignore[override]
        # Honour the StartFrame's negotiated sample rates if Pipecat
        # set them — keeps the serializer in sync with what the
        # transport actually plumbed through.
        #
        # Field names pinned to pipecat-ai 0.0.5x — if upstream
        # renames ``audio_in_sample_rate`` / ``audio_out_sample_rate``
        # in a future release, the getattr() silently falls back to
        # the constructor defaults and audio still flows at the
        # original rates. Bump this when you bump Pipecat.
        rate_in = getattr(frame, "audio_in_sample_rate", None)
        rate_out = getattr(frame, "audio_out_sample_rate", None)
        if isinstance(rate_in, int) and rate_in > 0:
            self._input_sample_rate = rate_in
        if isinstance(rate_out, int) and rate_out > 0:
            self._output_sample_rate = rate_out

    async def serialize(self, frame: "Frame") -> Optional[bytes]:  # type: ignore[override]
        # Only audio gets serialised onto the wire. Everything else
        # (system frames, LLM text frames, control frames) is
        # consumed entirely server-side — the demo browser doesn't
        # render transcripts. A production UI would extend this.
        if isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame)):
            audio = getattr(frame, "audio", None)
            if not isinstance(audio, (bytes, bytearray)) or not audio:
                return None
            return bytes(audio)
        return None

    async def deserialize(self, data) -> Optional["Frame"]:  # type: ignore[override]
        if not isinstance(data, (bytes, bytearray)) or not data:
            return None
        # int16 PCM is 2 bytes per sample. Odd-length payloads can't
        # be a whole number of samples — usually means the browser
        # split a sample across two WebSocket messages and the
        # halves arrived out of order, or the client is sending
        # garbage. Drop rather than feed Silero VAD a half-sample
        # that yields random noise.
        if len(data) % 2 != 0:
            return None
        # Pipecat's InputAudioRawFrame expects raw bytes + sample
        # rate + channel count. The browser is responsible for
        # delivering int16 little-endian at the agreed rate; we
        # don't re-validate here because Silero VAD downstream will
        # reject anything that doesn't decode as audio.
        return InputAudioRawFrame(
            audio=bytes(data),
            sample_rate=self._input_sample_rate,
            num_channels=self._num_channels,
        )

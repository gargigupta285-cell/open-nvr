# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared per-camera state for the camera-agent.

Two things live here:

* **Frame cache** — short-TTL cache of the most recent JPEG fetched
  from each camera. Inside one LLM turn, the model often calls
  several tools on the same camera (describe + detect + recognise);
  the cache means we hit the camera once, not three times.

* **Event ring** — bounded in-memory deque of recent inference
  events received from NATS. The ``recent_events`` tool answers
  "did anyone come to the door in the last 30 minutes?" out of this
  buffer. Without NATS configured the ring is just always empty;
  the tool degrades gracefully.

Both are async-safe so multiple tool invocations in flight on one
LLM turn don't trip each other up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from frame_sources import FrameSource, FrameSourceError

logger = logging.getLogger(__name__)


@dataclass
class CameraSpec:
    """Operator-supplied camera metadata. Lifted out of config.yml
    by ``camera_agent.load_config``."""
    camera_id: str
    frame_url: str
    role: str  # natural-language role description for the LLM


@dataclass
class _CachedFrame:
    bytes_: bytes
    fetched_at: float


@dataclass
class EventRecord:
    """One inference event off the NATS bus, trimmed to the bits the
    LLM cares about. Full envelopes are too big for prompt context."""
    received_at: float
    camera_id: str
    adapter: str
    summary: str  # short human-readable summary the tool emits to the LLM
    raw: dict[str, Any] = field(default_factory=dict)
    seq: int = 0  # insertion order, stamped by CameraContext.record_event


class CameraContext:
    """One instance shared across all tool calls. Owns the cache, the
    ring buffer, and the frame sources."""

    def __init__(
        self,
        *,
        cameras: list[CameraSpec],
        frame_cache_ttl_seconds: float = 2.0,
        event_ring_size: int = 256,
    ) -> None:
        self._cameras: dict[str, CameraSpec] = {
            cam.camera_id: cam for cam in cameras
        }
        self._frame_sources: dict[str, FrameSource] = {}
        self._frame_cache: dict[str, _CachedFrame] = {}
        self._cache_ttl = float(frame_cache_ttl_seconds)
        self._cache_lock = asyncio.Lock()

        # One ring per camera + a global ring for events that don't
        # carry a camera_id (rare). bounded so a chatty bus doesn't
        # grow memory unboundedly.
        self._event_ring_size = max(1, int(event_ring_size))
        self._events: dict[str, deque[EventRecord]] = {}
        # Monotonic insertion counter — a stable tiebreak for newest-first
        # ordering when events share a received_at (same-tick bursts, or a
        # clock with coarse resolution). Without it, equal timestamps sort
        # nondeterministically and the LLM sees a scrambled recent-events list.
        self._event_seq = 0

    # ── Cameras ────────────────────────────────────────────────────

    @property
    def cameras(self) -> list[CameraSpec]:
        return list(self._cameras.values())

    def add_camera(self, spec: CameraSpec) -> None:
        """Register a camera at runtime (e.g. a local device discovered via
        the demo's 'use this machine's camera' button)."""
        self._cameras[spec.camera_id] = spec

    def known_camera(self, camera_id: str) -> bool:
        return camera_id in self._cameras

    def get_camera(self, camera_id: str) -> CameraSpec | None:
        return self._cameras.get(camera_id)

    def register_frame_source(self, camera_id: str, source: FrameSource) -> None:
        """Called by camera_agent.py during startup so tests can
        inject fakes without standing up real HTTP."""
        self._frame_sources[camera_id] = source

    # ── Frame fetch + cache ────────────────────────────────────────

    async def get_frame(self, camera_id: str) -> bytes:
        """Return the most recent JPEG for ``camera_id``, fetching if
        the cache is empty or stale. Raises ``LookupError`` if the
        camera isn't configured; ``FrameSourceError`` if the fetch
        itself fails."""
        if camera_id not in self._cameras:
            raise LookupError(
                f"camera_id {camera_id!r} is not configured; "
                f"available: {sorted(self._cameras.keys())}"
            )
        source = self._frame_sources.get(camera_id)
        if source is None:
            raise LookupError(
                f"no frame source registered for camera {camera_id!r}"
            )

        # Cache check under the lock so two concurrent tool calls on
        # the same camera don't race-fetch twice.
        async with self._cache_lock:
            cached = self._frame_cache.get(camera_id)
            now = time.monotonic()
            if cached is not None and (now - cached.fetched_at) < self._cache_ttl:
                return cached.bytes_

            # Fetch outside the lock would be nicer for parallelism,
            # but frame_sources are sync and the lock is held only
            # briefly. Trade-off acceptable for v0.1 single-host use.
            try:
                frame = await asyncio.to_thread(source.fetch)
            except FrameSourceError:
                raise
            self._frame_cache[camera_id] = _CachedFrame(
                bytes_=frame, fetched_at=now
            )
            return frame

    def get_cached_frame(self, camera_id: str) -> bytes | None:
        """Return the JPEG bytes most recently fetched for ``camera_id`` this
        session, or None if nothing is cached. Best-effort, no fetch — used to
        show the operator the exact frame a tool looked at, in the chat."""
        cached = self._frame_cache.get(camera_id)
        return cached.bytes_ if cached is not None else None

    def invalidate_frame_cache(self, camera_id: str | None = None) -> None:
        """Drop cached frames. Without an arg, drops all — useful for
        tests."""
        if camera_id is None:
            self._frame_cache.clear()
        else:
            self._frame_cache.pop(camera_id, None)

    # ── Event ring ─────────────────────────────────────────────────

    def record_event(self, event: EventRecord) -> None:
        ring = self._events.setdefault(
            event.camera_id, deque(maxlen=self._event_ring_size)
        )
        event.seq = self._event_seq
        self._event_seq += 1
        ring.append(event)

    def recent_events(
        self,
        *,
        camera_id: str | None,
        window_seconds: float,
    ) -> list[EventRecord]:
        """Return events from the last ``window_seconds``. Filter by
        camera if given; otherwise across all cameras."""
        cutoff = time.time() - max(0.0, float(window_seconds))
        rings: list[deque[EventRecord]]
        if camera_id is None:
            rings = list(self._events.values())
        else:
            ring = self._events.get(camera_id)
            rings = [ring] if ring else []
        out: list[EventRecord] = []
        for ring in rings:
            for ev in ring:
                if ev.received_at >= cutoff:
                    out.append(ev)
        # Newest-first so the LLM sees the most relevant context before the
        # prompt-token budget runs out. The seq tiebreak keeps ordering
        # deterministic when events share a received_at.
        out.sort(key=lambda e: (e.received_at, e.seq), reverse=True)
        return out


# ── NATS subscriber ────────────────────────────────────────────────


async def run_event_subscriber(
    *,
    context: CameraContext,
    nats_url: str,
    nats_token: str | None,
    stop_event: asyncio.Event,
) -> None:
    """Subscribe to ``opennvr.inference.>`` and feed parsed events
    into the camera context's ring buffer.

    Runs forever until ``stop_event`` fires. Connection errors are
    logged + back off; we don't crash the agent if NATS is offline.
    """
    try:
        import nats  # type: ignore
    except ImportError:
        logger.warning("nats-py not installed; recent_events tool will be empty")
        await stop_event.wait()
        return

    while not stop_event.is_set():
        try:
            options: dict[str, Any] = {"servers": [nats_url]}
            if nats_token:
                options["token"] = nats_token
            nc = await nats.connect(**options)
        except Exception as exc:
            logger.warning(
                "nats subscriber: connect failed (%s); retrying in 5s", exc
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            return

        try:
            async def _handler(msg) -> None:  # noqa: ANN001 — nats msg type
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                except Exception:
                    logger.debug("nats subscriber: dropping non-JSON payload")
                    return
                record = _parse_inference_event(msg.subject, payload)
                if record is not None:
                    context.record_event(record)

            sub = await nc.subscribe("opennvr.inference.>", cb=_handler)
            logger.info("nats subscriber: connected to %s", nats_url)
            await stop_event.wait()
            await sub.unsubscribe()
        except Exception:
            logger.exception("nats subscriber: error in run loop; reconnecting")
        finally:
            try:
                await nc.drain()
            except Exception:
                logger.exception("nats subscriber: drain failed")


def _parse_inference_event(
    subject: str, payload: dict[str, Any]
) -> EventRecord | None:
    """Coerce an opennvr.inference.* envelope into a compact
    EventRecord. Returns None if the message isn't shaped like an
    inference event."""
    if not isinstance(payload, dict):
        return None
    camera_id = payload.get("camera_id") or payload.get("source", {}).get("camera_id")
    if not camera_id:
        return None
    adapter = payload.get("adapter_name") or _adapter_from_subject(subject)
    summary = _summarise_event(payload)
    return EventRecord(
        received_at=time.time(),
        camera_id=str(camera_id),
        adapter=adapter,
        summary=summary,
        raw=payload,
    )


def _adapter_from_subject(subject: str) -> str:
    # ``opennvr.inference.<adapter>.<camera_id>.completed`` → adapter
    parts = subject.split(".")
    if len(parts) >= 3 and parts[0] == "opennvr" and parts[1] == "inference":
        return parts[2]
    return "unknown"


def _summarise_event(payload: dict[str, Any]) -> str:
    """One-line human-readable summary the LLM can read directly.
    Falls back to a generic phrase when the payload shape isn't
    one we recognise."""
    result = payload.get("result") or {}
    task = result.get("task") or payload.get("task") or ""
    if task == "face_recognition":
        if result.get("recognized"):
            who = result.get("name") or result.get("person_id") or "someone"
            sim = result.get("similarity")
            extra = f" (similarity {sim:.2f})" if isinstance(sim, (int, float)) else ""
            return f"face recognised: {who}{extra}"
        return "face detected but not recognised"
    if task == "face_detection":
        n = result.get("face_count", 0)
        return f"{n} face(s) detected"
    if task == "object_detection":
        dets = result.get("detections") or []
        if not dets:
            return "no objects detected"
        labels = sorted({(d.get("label") or "?") for d in dets[:8]})
        return f"objects detected: {', '.join(labels)}"
    if task == "scene_caption":
        return f"scene: {result.get('caption', '')}"[:140]
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return f"event from adapter ({task or 'unknown task'})"

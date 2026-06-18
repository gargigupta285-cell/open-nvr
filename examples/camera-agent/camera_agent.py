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
from dataclasses import dataclass, field
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

    # Optional emergency contacts for alarms, keyed by alarm target/name
    # (e.g. {"fire": "+1-555-0100"}). The actual call-out is a documented
    # future integration (see ALARMS.md); for now an armed alarm with a
    # matching contact records "would alert <number>" when it fires.
    emergency_contacts: dict[str, str] | None = None

    # Persona voice: "female" → Shailaja, "male" → Sidhu. Selects the agent's
    # name + pronouns; the actual spoken voice is the Piper voice configured in
    # the ai-adapter (see README/ALARMS notes).
    voice_gender: str = "female"

    # External notifications: webhook URLs alarms/watches fan out to so alerts
    # reach you when the browser tab is closed (Slack/Discord/n8n/Home
    # Assistant/custom all accept a JSON POST). ``notify_events`` selects which
    # categories are sent (default alarms + watch notifications). See
    # NOTIFICATIONS.md.
    notify_webhooks: list[str] | None = None
    notify_events: list[str] | None = None

    # Optional path to a JSON file that persists alarms, watches, and report
    # schedules so they survive a restart. Unset → in-memory only.
    state_path: str | None = None

    # Optional face-management API (enroll/list/forget known people) for the
    # watchlist. Points at the recognition adapter's faces REST API. Unset →
    # the enroll/list/forget tools report face management isn't configured.
    # See FACES.md for the expected contract.
    faces_url: str | None = None
    faces_token: str | None = None

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

# The agent's persona name follows the configured voice gender. The actual
# spoken voice is whichever Piper voice the ai-adapter serves; ``voice_gender``
# here selects the matching persona NAME (and pronouns) the agent uses.
AGENT_NAMES = {"female": "Shailaja", "male": "Sidhu"}
DEFAULT_VOICE_GENDER = "female"


def agent_name_for(voice_gender: str | None) -> str:
    return AGENT_NAMES.get((voice_gender or DEFAULT_VOICE_GENDER).strip().lower(),
                           AGENT_NAMES[DEFAULT_VOICE_GENDER])


def greeting_for(name: str) -> str:
    return (
        f"Hi, I'm {name}, your OpenNVR camera agent. I keep an eye on all your "
        f"cameras and run the checks you ask for. I can tell you what's "
        f"happening right now, look back at what happened earlier, set up "
        f"alarms and watches, and take on longer searches in the background "
        f"while we keep talking. Ask me anything about your cameras."
    )


# ── Background task system (in-memory) ─────────────────────────────────


@dataclass
class AgentTask:
    """A long-running request Ram is working on in the background."""

    id: int
    query: str
    status: str = "queued"  # queued | running | done | error
    result: str | None = None
    created_at: float = 0.0
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class TaskManager:
    """Runs Ram's longer jobs as background asyncio tasks so the
    conversation never blocks. In-memory only — tasks reset on restart.

    Each task runs a full tool-calling turn for its query (with the
    create_background_task tool removed, so a task can't spawn more
    tasks), and stores the final answer. The UI polls ``list()`` and
    surfaces completions back into the chat."""

    def __init__(self, runtime: "CameraAgentRuntime", *, max_tasks: int = 50) -> None:
        self._runtime = runtime
        self._tasks: dict[int, AgentTask] = {}
        self._order: list[int] = []
        self._next_id = 1
        self._max = max_tasks

    def create(self, query: str) -> AgentTask:
        import time

        task = AgentTask(id=self._next_id, query=query.strip(), created_at=time.time())
        self._next_id += 1
        self._tasks[task.id] = task
        self._order.append(task.id)
        # Trim oldest beyond the cap.
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._tasks.pop(old, None)
        asyncio.create_task(self._run(task), name=f"agent-task-{task.id}")
        logger.info("task #%d queued: %r", task.id, task.query)
        return task

    async def _run(self, task: AgentTask) -> None:
        import time

        task.status = "running"
        try:
            task.result = await _run_conversation_turn(
                self._runtime, [], task.query,
                tool_definitions=self._runtime.background_tool_definitions,
            )
            task.status = "done"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("task #%d failed", task.id)
            task.result = f"I hit an error working on that: {exc}"
            task.status = "error"
        finally:
            task.finished_at = time.time()
            logger.info("task #%d %s", task.id, task.status)
            self._runtime.notifier.fire({
                "type": "task", "title": f"Task #{task.id} {task.status}",
                "text": task.result or "", "severity": "info", "ts": task.finished_at,
            })

    def get(self, task_id: int) -> AgentTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[dict[str, Any]]:
        return [self._tasks[i].to_dict() for i in self._order if i in self._tasks]


# ── Standing monitors (watch / count) ─────────────────────────────────


@dataclass
class Monitor:
    """A standing watch Ram keeps on one or more cameras.

    kind="notify": alert when ``target`` appears.
    kind="count":  keep a live + peak count of ``target`` per camera
                   (periodic snapshot counts — not turnstile/line-crossing).
    """

    id: int
    kind: str            # "notify" | "count" | "crossing"
    camera_ids: list[str]
    target: str
    description: str
    interval_s: float
    active: bool = True
    created_at: float = 0.0
    line: list[float] | None = None   # crossing: [x1,y1,x2,y2] normalized
    current: dict[str, int] = field(default_factory=dict)
    peak: dict[str, int] = field(default_factory=dict)
    counters: dict = field(default_factory=dict)  # per-camera LineCounter (crossing)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id, "kind": self.kind, "camera_ids": self.camera_ids,
            "target": self.target, "description": self.description,
            "interval_s": self.interval_s, "active": self.active,
            "created_at": self.created_at,
            "current": self.current, "peak": self.peak,
        }
        if self.line:
            d["line"] = self.line
        return d


def _line_side(line: tuple, x: float, y: float) -> float:
    """Signed side of point (x,y) relative to the directed line
    (x1,y1)->(x2,y2). >0 one side, <0 the other, 0 on the line."""
    (x1, y1), (x2, y2) = line
    return (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)


class LineCounter:
    """Counts unique tracks crossing a virtual line, with direction.

    Fed per-poll lists of tracked detections ({id, x, y} with x,y as
    normalized [0,1] frame coordinates of the box center). When a track
    moves from one side of the line to the other, it's counted as ``in``
    (crossing to the positive side) or ``out``. Requires a tracker that
    yields stable per-object ids across frames (e.g. the ByteTrack
    adapter) and a poll rate high enough to see objects on both sides —
    see COUNTING.md.
    """

    def __init__(self, line: tuple) -> None:
        self._line = line
        self._last_side: dict[Any, float] = {}
        self.in_count = 0
        self.out_count = 0

    def update(self, tracks: list[dict[str, Any]]) -> None:
        for t in tracks:
            tid = t.get("id")
            if tid is None:
                continue
            s = _line_side(self._line, float(t.get("x", 0.0)), float(t.get("y", 0.0)))
            prev = self._last_side.get(tid)
            if prev is not None and prev != 0 and s != 0 and (prev > 0) != (s > 0):
                if s > 0:
                    self.in_count += 1
                else:
                    self.out_count += 1
            self._last_side[tid] = s

    def totals(self) -> dict[str, int]:
        return {"in": self.in_count, "out": self.out_count, "net": self.in_count - self.out_count}


class MonitorManager:
    """Runs Ram's standing watches: every ``interval_s`` it grabs a frame
    from each watched camera, runs detection, and counts the target. Notify
    monitors raise a (cooldown-limited) notification when the target is
    present; count monitors track live + peak counts. In-memory only."""

    def __init__(self, runtime: "CameraAgentRuntime", *, default_interval: float = 8.0,
                 notify_cooldown: float = 30.0, max_monitors: int = 20) -> None:
        self._runtime = runtime
        self._monitors: dict[int, Monitor] = {}
        self._order: list[int] = []
        self._tasks: dict[int, asyncio.Task] = {}
        self._next_id = 1
        self._default_interval = default_interval
        self._cooldown = notify_cooldown
        self._max = max_monitors
        self._last_notified: dict[tuple[int, str], float] = {}
        self._notifications: list[dict[str, Any]] = []
        self._next_note_id = 1

    def create(self, *, kind: str, camera_ids: list[str], target: str,
               description: str = "", interval_s: float | None = None,
               line: list[float] | None = None) -> Monitor:
        import time

        mon = Monitor(
            id=self._next_id, kind=kind, camera_ids=list(camera_ids),
            target=target.strip().lower(), description=description.strip(),
            interval_s=float(interval_s or self._default_interval),
            created_at=time.time(), line=line,
        )
        if kind == "crossing" and line and len(line) == 4:
            ln = ((line[0], line[1]), (line[2], line[3]))
            mon.counters = {cam: LineCounter(ln) for cam in camera_ids}
        self._next_id += 1
        self._monitors[mon.id] = mon
        self._order.append(mon.id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self.stop(old)
            self._monitors.pop(old, None)
        self._tasks[mon.id] = asyncio.create_task(self._loop(mon), name=f"monitor-{mon.id}")
        logger.info("monitor #%d (%s %r on %s) started", mon.id, kind, target, camera_ids)
        return mon

    def stop(self, monitor_id: int) -> bool:
        mon = self._monitors.get(monitor_id)
        if not mon:
            return False
        mon.active = False
        t = self._tasks.pop(monitor_id, None)
        if t:
            t.cancel()
        return True

    def stop_all(self) -> None:
        for mid in list(self._monitors):
            self.stop(mid)

    def export(self) -> list[dict[str, Any]]:
        return [{"kind": m.kind, "camera_ids": m.camera_ids, "target": m.target,
                 "interval_s": m.interval_s, "line": m.line}
                for m in self._monitors.values() if m.active]

    def restore(self, specs: list[dict[str, Any]]) -> None:
        for s in specs or []:
            try:
                self.create(kind=s["kind"], camera_ids=s["camera_ids"],
                            target=s["target"], interval_s=s.get("interval_s"),
                            line=s.get("line"))
            except Exception:  # pragma: no cover
                logger.exception("monitor restore failed for %r", s)

    def list(self) -> list[dict[str, Any]]:
        return [self._monitors[i].to_dict() for i in self._order if i in self._monitors]

    def notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications[-50:])

    def _count_target(self, detections: list[dict[str, Any]], target: str) -> int:
        deduped = self._runtime.tools._dedup_detections(detections[:64])
        return sum(
            1 for d in deduped
            if str(d.get("label") or d.get("class") or "").strip().lower() == target
        )

    @staticmethod
    def _extract_tracks(result: dict[str, Any], target: str) -> list[dict[str, Any]]:
        """Pull tracked detections ({id, x, y} centers) out of an adapter
        result, filtered to ``target``. Coordinates are passed through in
        whatever space the tracker emits (assumed same as the line)."""
        raw = result.get("tracks") or result.get("detections") or []
        out: list[dict[str, Any]] = []
        for tr in raw:
            if not isinstance(tr, dict):
                continue
            label = str(tr.get("label") or tr.get("class") or "").strip().lower()
            if target and label and label != target:
                continue
            tid = tr.get("track_id", tr.get("id"))
            if tid is None:
                continue
            center = tr.get("center")
            if isinstance(center, (list, tuple)) and len(center) >= 2:
                x, y = float(center[0]), float(center[1])
            else:
                bb = tr.get("bbox") or {}
                if isinstance(bb, dict):
                    x = float(bb.get("x", 0)) + float(bb.get("w", 0)) / 2
                    y = float(bb.get("y", 0)) + float(bb.get("h", 0)) / 2
                elif isinstance(bb, (list, tuple)) and len(bb) >= 4:
                    x = float(bb[0]) + float(bb[2]) / 2
                    y = float(bb[1]) + float(bb[3]) / 2
                else:
                    continue
            out.append({"id": tid, "x": x, "y": y})
        return out

    async def _loop(self, mon: Monitor) -> None:
        try:
            while mon.active and not self._runtime._stop_event.is_set():
                for cam in mon.camera_ids:
                    if not mon.active:
                        break
                    await self._poll(mon, cam)
                await asyncio.sleep(mon.interval_s)
        except asyncio.CancelledError:  # pragma: no cover
            pass
        except Exception:  # pragma: no cover - defensive
            logger.exception("monitor #%d loop crashed", mon.id)

    async def _poll(self, mon: Monitor, cam: str) -> None:
        import time

        try:
            frame = await self._runtime.context.get_frame(cam)
            extra = {"task": "track"} if mon.kind == "crossing" else None
            resp = await self._runtime.detection_client.infer(frame_jpeg=frame, extra=extra)
        except Exception as exc:
            logger.info("monitor #%d: poll of %s failed (%s)", mon.id, cam, exc)
            return
        result = resp.get("result") or {}

        if mon.kind == "crossing":
            counter = mon.counters.get(cam)
            if counter is not None:
                tracks = self._extract_tracks(result, mon.target)
                counter.update(tracks)
                t = counter.totals()
                mon.current[cam] = t["net"]
                mon.peak[cam] = max(mon.peak.get(cam, 0), t["in"])
            return

        detections = result.get("detections") or []
        count = self._count_target(detections, mon.target)
        mon.current[cam] = count
        mon.peak[cam] = max(mon.peak.get(cam, 0), count)
        if mon.kind == "notify" and count > 0:
            key = (mon.id, cam)
            now = time.time()
            if now - self._last_notified.get(key, 0.0) >= self._cooldown:
                self._last_notified[key] = now
                noun = mon.target if count == 1 else f"{count} {mon.target}s"
                self._notifications.append({
                    "id": self._next_note_id,
                    "monitor_id": mon.id,
                    "text": f"Heads up — I see {noun} on {cam}.",
                    "ts": now,
                })
                self._next_note_id += 1
                logger.info("monitor #%d notified: %s on %s", mon.id, mon.target, cam)
                self._runtime.notifier.fire({
                    "type": "notify", "title": "Camera watch",
                    "text": f"{noun} on {cam}", "camera": cam,
                    "severity": "info", "ts": now,
                })


def _create_monitor_tool(camera_enum_all: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_monitor",
            "description": (
                "Set up a STANDING watch on one or more cameras (or 'all'). "
                "kind='notify' alerts when the target appears; kind='count' "
                "keeps a live snapshot count; kind='crossing' counts people/"
                "objects crossing a line (needs a 'line'). Use for ongoing "
                "requests like 'notify me when you see a person on cam1', "
                "'count people on gate 2', 'count people entering at the door'. "
                "NOT for one-off 'what do you see right now' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["notify", "count", "crossing"]},
                    "target": {"type": "string",
                               "description": "What to watch for, e.g. 'person', 'car', 'dog'."},
                    "camera_id": {"type": "string", "enum": camera_enum_all,
                                  "description": "A camera id, or 'all'."},
                    "camera_ids": {"type": "array", "items": {"type": "string", "enum": camera_enum_all},
                                   "description": "Optional: several cameras at once."},
                    "line": {"type": "array", "items": {"type": "number"},
                             "description": "For kind='crossing': [x1,y1,x2,y2] of the counting line in normalized 0-1 frame coords."},
                },
                "required": ["kind", "target", "camera_id"],
            },
        },
    }


def _stop_monitor_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "stop_monitor",
            "description": "Stop a standing monitor by its numeric id.",
            "parameters": {
                "type": "object",
                "properties": {"monitor_id": {"type": "integer"}},
                "required": ["monitor_id"],
            },
        },
    }


# ── Alarms (condition → ring) ──────────────────────────────────────────


def _parse_hhmm(value: Any) -> int | None:
    """Parse 'HH:MM' (24h) to minutes-since-midnight, else None."""
    if not value:
        return None
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return None


@dataclass
class Alarm:
    """A high-severity rule: when ``target`` appears on a watched camera
    (optionally only within a time window), the alarm RINGS until a human
    acknowledges it. Optionally tied to an emergency contact (the actual
    call-out is a documented future integration — see ALARMS.md)."""

    id: int
    name: str
    target: str
    camera_ids: list[str]
    after_min: int | None = None
    before_min: int | None = None
    emergency_contact: str | None = None
    active: bool = True
    triggered: bool = False
    created_at: float = 0.0
    last_triggered: float | None = None
    trigger_count: int = 0
    last_ack: float = 0.0

    def window_label(self) -> str:
        def fmt(m):
            return f"{m // 60:02d}:{m % 60:02d}"
        if self.after_min is not None and self.before_min is not None:
            return f"between {fmt(self.after_min)} and {fmt(self.before_min)}"
        if self.after_min is not None:
            return f"after {fmt(self.after_min)}"
        if self.before_min is not None:
            return f"before {fmt(self.before_min)}"
        return "any time"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "target": self.target,
            "camera_ids": self.camera_ids, "window": self.window_label(),
            "active": self.active, "triggered": self.triggered,
            "trigger_count": self.trigger_count,
            "emergency_contact_configured": bool(self.emergency_contact),
            "created_at": self.created_at, "last_triggered": self.last_triggered,
        }


class AlarmManager:
    """Polls watched cameras and rings when an alarm's condition is met.
    Mirrors MonitorManager but raises sticky, acknowledgeable alarm events
    (the UI plays a siren while any alarm is ``triggered``). In-memory only."""

    def __init__(self, runtime: "CameraAgentRuntime", *, interval: float = 5.0,
                 rearm_cooldown: float = 20.0, max_alarms: int = 20) -> None:
        self._runtime = runtime
        self._alarms: dict[int, Alarm] = {}
        self._order: list[int] = []
        self._tasks: dict[int, asyncio.Task] = {}
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        self._next_event_id = 1
        self._interval = interval
        self._rearm = rearm_cooldown
        self._max = max_alarms

    def create(self, *, name: str, target: str, camera_ids: list[str],
               after_min: int | None = None, before_min: int | None = None,
               emergency_contact: str | None = None) -> Alarm:
        import time

        alarm = Alarm(
            id=self._next_id, name=name.strip() or "Alarm",
            target=target.strip().lower(), camera_ids=list(camera_ids),
            after_min=after_min, before_min=before_min,
            emergency_contact=emergency_contact, created_at=time.time(),
        )
        self._next_id += 1
        self._alarms[alarm.id] = alarm
        self._order.append(alarm.id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self.stop(old)
            self._alarms.pop(old, None)
        self._tasks[alarm.id] = asyncio.create_task(self._loop(alarm), name=f"alarm-{alarm.id}")
        logger.info("alarm #%d %r (%s on %s, %s) armed", alarm.id, alarm.name,
                    alarm.target, camera_ids, alarm.window_label())
        return alarm

    def stop(self, alarm_id: int) -> bool:
        alarm = self._alarms.get(alarm_id)
        if not alarm:
            return False
        alarm.active = False
        alarm.triggered = False
        t = self._tasks.pop(alarm_id, None)
        if t:
            t.cancel()
        return True

    def acknowledge(self, alarm_id: int | None = None) -> int:
        """Silence one alarm (or all when id is None). Returns how many."""
        import time

        n = 0
        for aid, alarm in self._alarms.items():
            if alarm_id in (None, aid) and alarm.triggered:
                alarm.triggered = False
                alarm.last_ack = time.time()
                n += 1
        return n

    def stop_all(self) -> None:
        for aid in list(self._alarms):
            self.stop(aid)

    def export(self) -> list[dict[str, Any]]:
        return [{"name": a.name, "target": a.target, "camera_ids": a.camera_ids,
                 "after_min": a.after_min, "before_min": a.before_min}
                for a in self._alarms.values() if a.active]

    def restore(self, specs: list[dict[str, Any]], *, contact_for=None) -> None:
        for s in specs or []:
            try:
                self.create(name=s["name"], target=s["target"], camera_ids=s["camera_ids"],
                            after_min=s.get("after_min"), before_min=s.get("before_min"),
                            emergency_contact=(contact_for(s["name"], s["target"]) if contact_for else None))
            except Exception:  # pragma: no cover
                logger.exception("alarm restore failed for %r", s)

    def list(self) -> list[dict[str, Any]]:
        return [self._alarms[i].to_dict() for i in self._order if i in self._alarms]

    def events(self) -> list[dict[str, Any]]:
        return list(self._events[-50:])

    def _in_window(self, alarm: Alarm) -> bool:
        if alarm.after_min is None and alarm.before_min is None:
            return True
        import datetime

        now = datetime.datetime.now().time()
        mins = now.hour * 60 + now.minute
        a, b = alarm.after_min, alarm.before_min
        if a is not None and b is not None:
            return (a <= mins < b) if a <= b else (mins >= a or mins < b)
        if a is not None:
            return mins >= a
        return mins < b

    async def _loop(self, alarm: Alarm) -> None:
        try:
            while alarm.active and not self._runtime._stop_event.is_set():
                if self._in_window(alarm):
                    for cam in alarm.camera_ids:
                        if not alarm.active:
                            break
                        await self._poll(alarm, cam)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:  # pragma: no cover
            pass
        except Exception:  # pragma: no cover
            logger.exception("alarm #%d loop crashed", alarm.id)

    async def _poll(self, alarm: Alarm, cam: str) -> None:
        import time

        try:
            frame = await self._runtime.context.get_frame(cam)
            resp = await self._runtime.detection_client.infer(frame_jpeg=frame)
        except Exception as exc:
            logger.info("alarm #%d: poll of %s failed (%s)", alarm.id, cam, exc)
            return
        detections = (resp.get("result") or {}).get("detections") or []
        present = self._runtime.monitors._count_target(detections, alarm.target) > 0
        now = time.time()
        if present and not alarm.triggered and (now - alarm.last_ack) >= self._rearm:
            alarm.triggered = True
            alarm.last_triggered = now
            alarm.trigger_count += 1
            text = f"{alarm.name}: {alarm.target} detected on {cam}"
            if alarm.emergency_contact:
                text += f" (would alert {alarm.emergency_contact})"
            self._events.append({
                "id": self._next_event_id, "alarm_id": alarm.id, "name": alarm.name,
                "text": text, "camera": cam, "ts": now,
                "emergency_contact": alarm.emergency_contact,
            })
            self._next_event_id += 1
            logger.warning("ALARM #%d TRIGGERED: %s", alarm.id, text)
            self._runtime.notifier.fire({
                "type": "alarm", "title": alarm.name, "text": text,
                "camera": cam, "severity": "critical", "ts": now,
            })


def _create_alarm_tool(camera_enum_all: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_alarm",
            "description": (
                "Arm a high-severity ALARM that RINGS when something appears, "
                "optionally only within a time window. Use for 'sound a fire "
                "alarm if you see fire', 'alarm if a person is seen after 6pm', "
                "'alert me loudly if a car enters at night'. Different from a "
                "monitor: an alarm rings until acknowledged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short alarm name, e.g. 'Fire', 'After-hours intrusion'."},
                    "target": {"type": "string", "description": "What triggers it, e.g. 'fire', 'person', 'smoke'."},
                    "camera_id": {"type": "string", "enum": camera_enum_all, "description": "A camera id, or 'all'."},
                    "camera_ids": {"type": "array", "items": {"type": "string", "enum": camera_enum_all}},
                    "after": {"type": "string", "description": "Only active after this 24h time 'HH:MM' (e.g. '18:00' for after 6pm)."},
                    "before": {"type": "string", "description": "Only active before this 24h time 'HH:MM'."},
                },
                "required": ["name", "target", "camera_id"],
            },
        },
    }


def _stop_alarm_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "stop_alarm",
            "description": "Disarm an alarm by its numeric id, or acknowledge/silence a ringing one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alarm_id": {"type": "integer"},
                    "action": {"type": "string", "enum": ["disarm", "silence"],
                               "description": "'silence' stops the current ring but keeps the alarm armed; 'disarm' removes it."},
                },
                "required": ["alarm_id"],
            },
        },
    }


# ── External notifications (webhook fan-out) ──────────────────────────


class Notifier:
    """Fans alarm/watch events out to configured webhook URLs so alerts
    reach the operator when the browser tab is closed. Best-effort and
    non-blocking — failures are logged, never raised into the poll loops.

    The payload includes both ``text`` (Slack) and ``content`` (Discord)
    plus structured fields, so it works with Slack/Discord/Teams incoming
    webhooks, n8n/Home Assistant, and custom JSON consumers unchanged."""

    def __init__(self, runtime: "CameraAgentRuntime", *, webhooks=None, events=None) -> None:
        self._runtime = runtime
        self._webhooks = list(webhooks or [])
        self._events = set(events or ("alarm", "notify"))
        self._client = None
        self._deliveries: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(self._webhooks)

    def _http(self):
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=8.0, trust_env=False)
        return self._client

    def _format(self, event: dict[str, Any]) -> dict[str, Any]:
        title = str(event.get("title") or "OpenNVR alert")
        detail = str(event.get("text") or "")
        body = f"{title}: {detail}" if detail else title
        return {
            "text": body, "content": body,  # Slack / Discord
            "type": event.get("type"), "title": title, "detail": detail,
            "camera": event.get("camera"), "severity": event.get("severity"),
            "ts": event.get("ts"), "agent": self._runtime.agent_name,
        }

    async def send(self, event: dict[str, Any]) -> int:
        kind = event.get("type")
        if kind != "test" and kind not in self._events:
            return 0
        payload = self._format(event)
        ok = 0
        for url in self._webhooks:
            try:
                resp = await self._http().post(url, json=payload)
                if resp.status_code < 400:
                    ok += 1
                else:
                    logger.warning("notify: %s returned HTTP %s", url, resp.status_code)
            except Exception as exc:
                logger.warning("notify: delivery to %s failed: %s", url, exc)
        import time as _t

        self._deliveries.append({"type": kind, "title": payload["title"],
                                 "ok": ok, "channels": len(self._webhooks), "ts": _t.time()})
        del self._deliveries[:-50]
        return ok

    def fire(self, event: dict[str, Any]) -> None:
        """Fire-and-forget from sync/poll contexts (never blocks)."""
        if not self._webhooks:
            return
        try:
            asyncio.create_task(self.send(event))
        except RuntimeError:  # pragma: no cover - no running loop
            pass

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "channels": len(self._webhooks),
                "events": sorted(self._events), "recent": self._deliveries[-20:]}


# ── Scheduled recurring reports ────────────────────────────────────────


@dataclass
class ReportSchedule:
    """A recurring summary: at a daily time or every N minutes, the agent
    runs ``query`` (a normal tool-grounded turn) and delivers the result."""

    id: int
    name: str
    query: str
    at_min: int | None = None       # daily, minutes since midnight
    every_minutes: int | None = None
    active: bool = True
    created_at: float = 0.0
    last_run: float | None = None
    last_result: str | None = None
    run_count: int = 0

    def schedule_label(self) -> str:
        if self.every_minutes:
            return f"every {self.every_minutes} min"
        if self.at_min is not None:
            return f"daily at {self.at_min // 60:02d}:{self.at_min % 60:02d}"
        return "manual"

    def due(self, now=None) -> bool:
        import datetime

        now = now or datetime.datetime.now()
        nowts = now.timestamp()
        if self.every_minutes:
            return self.last_run is None or (nowts - self.last_run) >= self.every_minutes * 60
        if self.at_min is not None:
            sched = now.replace(hour=self.at_min // 60, minute=self.at_min % 60,
                                second=0, microsecond=0)
            if now < sched:
                return False
            return self.last_run is None or self.last_run < sched.timestamp()
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "query": self.query,
            "schedule": self.schedule_label(), "active": self.active,
            "last_run": self.last_run, "last_result": self.last_result,
            "run_count": self.run_count, "created_at": self.created_at,
        }


class ReportScheduler:
    """Runs recurring reports. One background loop ticks every
    ``check_interval`` and runs any schedule that's due. Each report is a
    tool-grounded turn over ``query`` (with the agent-control tools removed),
    delivered to chat, the notifier, and the recent-reports list."""

    def __init__(self, runtime: "CameraAgentRuntime", *, check_interval: float = 30.0,
                 max_schedules: int = 20) -> None:
        self._runtime = runtime
        self._schedules: dict[int, ReportSchedule] = {}
        self._order: list[int] = []
        self._reports: list[dict[str, Any]] = []
        self._next_id = 1
        self._next_report_id = 1
        self._check_interval = check_interval
        self._max = max_schedules
        self._task: asyncio.Task | None = None

    def create(self, *, name: str, query: str, at_min: int | None = None,
               every_minutes: int | None = None) -> ReportSchedule:
        import time

        if at_min is None and not every_minutes:
            at_min = 8 * 60  # sensible default: 08:00 daily
        sched = ReportSchedule(id=self._next_id, name=name.strip() or "Report",
                               query=query.strip(), at_min=at_min,
                               every_minutes=every_minutes, created_at=time.time())
        self._next_id += 1
        self._schedules[sched.id] = sched
        self._order.append(sched.id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._schedules.pop(old, None)
        logger.info("report #%d %r scheduled (%s)", sched.id, sched.name, sched.schedule_label())
        return sched

    def stop(self, report_id: int) -> bool:
        sched = self._schedules.get(report_id)
        if not sched:
            return False
        sched.active = False
        return True

    def list(self) -> list[dict[str, Any]]:
        return [self._schedules[i].to_dict() for i in self._order if i in self._schedules]

    def export(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "query": s.query, "at_min": s.at_min,
                 "every_minutes": s.every_minutes}
                for s in self._schedules.values() if s.active]

    def restore(self, specs: list[dict[str, Any]]) -> None:
        for s in specs or []:
            try:
                self.create(name=s["name"], query=s["query"],
                            at_min=s.get("at_min"), every_minutes=s.get("every_minutes"))
            except Exception:  # pragma: no cover
                logger.exception("report restore failed for %r", s)

    def reports(self) -> list[dict[str, Any]]:
        return list(self._reports[-20:])

    def get(self, report_id: int) -> ReportSchedule | None:
        return self._schedules.get(report_id)

    async def run_now(self, report_id: int) -> str | None:
        sched = self._schedules.get(report_id)
        if not sched:
            return None
        return await self._run(sched)

    async def _run(self, sched: ReportSchedule) -> str:
        import time

        sched.last_run = time.time()
        sched.run_count += 1
        try:
            result = await _run_conversation_turn(
                self._runtime, [], sched.query,
                tool_definitions=self._runtime.background_tool_definitions,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("report #%d failed", sched.id)
            result = f"(report failed: {exc})"
        sched.last_result = result
        self._reports.append({"id": self._next_report_id, "schedule_id": sched.id,
                              "name": sched.name, "text": result, "ts": sched.last_run})
        self._next_report_id += 1
        del self._reports[:-50]
        self._runtime.notifier.fire({"type": "report", "title": sched.name,
                                     "text": result, "severity": "info", "ts": sched.last_run})
        logger.info("report #%d ran: %.80s", sched.id, result)
        return result

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="report-scheduler")

    def stop_all(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while not self._runtime._stop_event.is_set():
                for sid in list(self._schedules):
                    s = self._schedules.get(sid)
                    if s and s.active and s.due():
                        await self._run(s)
                await asyncio.sleep(self._check_interval)
        except asyncio.CancelledError:  # pragma: no cover
            pass
        except Exception:  # pragma: no cover
            logger.exception("report scheduler loop crashed")


def _create_report_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_report",
            "description": (
                "Schedule a RECURRING summary report. Use for 'every morning "
                "summarize overnight activity', 'give me a daily 7am rundown', "
                "'every hour tell me how many people came by'. Provide a short "
                "name and the query to summarize; set 'at' (HH:MM, daily) or "
                "'every_minutes'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short report name, e.g. 'Morning rundown'."},
                    "query": {"type": "string", "description": "What to summarize, e.g. 'what the cameras saw overnight'."},
                    "at": {"type": "string", "description": "Daily run time, 24h 'HH:MM' (e.g. '07:00')."},
                    "every_minutes": {"type": "integer", "description": "Or run every N minutes instead of a daily time."},
                },
                "required": ["name", "query"],
            },
        },
    }


def _stop_report_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "stop_report",
            "description": "Cancel a scheduled report by its numeric id.",
            "parameters": {
                "type": "object",
                "properties": {"report_id": {"type": "integer"}},
                "required": ["report_id"],
            },
        },
    }


# ── Watchlist / face management ────────────────────────────────────────


class FaceClient:
    """Thin client for the recognition adapter's faces REST API
    (enroll / list / forget known people). The exact wire shape is
    documented in FACES.md; adjust if your adapter version differs."""

    def __init__(self, *, url: str, token: str | None = None, timeout_seconds: float = 30.0) -> None:
        self._base = url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._http = None

    def _client(self):
        import httpx

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._http

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["X-Internal-Api-Key"] = self._token
        return h

    async def enroll(self, *, name: str, frame_jpeg: bytes, category: str = "known") -> dict[str, Any]:
        body = {"name": name, "category": category,
                "frame_b64": base64.b64encode(frame_jpeg).decode("ascii")}
        resp = await self._client().post(f"{self._base}/faces", json=body, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def list_people(self) -> list[dict[str, Any]]:
        resp = await self._client().get(f"{self._base}/faces", headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        return data.get("people") or data.get("faces") or (data if isinstance(data, list) else [])

    async def forget(self, name: str) -> dict[str, Any]:
        from urllib.parse import quote

        resp = await self._client().delete(f"{self._base}/faces/{quote(name)}", headers=self._headers())
        resp.raise_for_status()
        return resp.json() if resp.content else {"ok": True}

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


def _enroll_face_tool(camera_enum_all: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "enroll_face",
            "description": (
                "Add a person to the watchlist by capturing their face from a "
                "camera right now. Use for 'remember this person as Mom', "
                "'enroll the person at the door as Alex'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The person's name."},
                    "camera_id": {"type": "string", "enum": camera_enum_all,
                                  "description": "Camera to capture the face from."},
                    "category": {"type": "string",
                                 "description": "Optional group, e.g. 'family', 'staff', 'blocked'."},
                },
                "required": ["name", "camera_id"],
            },
        },
    }


def _list_people_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_people",
            "description": "List the people currently enrolled in the watchlist.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _forget_face_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "forget_face",
            "description": "Remove a person from the watchlist by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }


def _create_background_task_tool() -> dict[str, Any]:
    """OpenAI/Ollama function schema for Ram's background-task tool."""
    return {
        "type": "function",
        "function": {
            "name": "create_background_task",
            "description": (
                "Queue a long-running investigation that may take a while, "
                "such as searching recorded footage for a past event. Use this "
                "for questions about the RECORDED PAST (earlier, yesterday, a "
                "specific past time). Returns immediately; the answer is "
                "delivered to the user when the task finishes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Short natural-language description of what to look "
                            "for, e.g. 'a person in a red shirt on the porch "
                            "around 3am two days ago'."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }


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
        emergency_contacts=(
            dict(raw["emergency_contacts"])
            if isinstance(raw.get("emergency_contacts"), dict) else None
        ),
        voice_gender=str(raw.get("voice_gender") or "female"),
        state_path=raw.get("state_path"),
        faces_url=raw.get("faces_url"),
        faces_token=raw.get("faces_token"),
        notify_webhooks=(
            list(raw["notify_webhooks"])
            if isinstance(raw.get("notify_webhooks"), list) else None
        ),
        notify_events=(
            list(raw["notify_events"])
            if isinstance(raw.get("notify_events"), list) else None
        ),
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
        # Tools the background task runner uses — the live camera/footage
        # tools WITHOUT create_background_task (a task must not spawn tasks).
        self.background_tool_definitions = build_tool_definitions(
            [cam.camera_id for cam in cfg.cameras],
            enabled=cfg.enabled_tools,
        )
        # Foreground tools = background tools + the agent-control tools
        # (offload long jobs, set up standing monitors) — none of which a
        # background task is allowed to call.
        _camera_enum_all = [cam.camera_id for cam in cfg.cameras] + ["all"]
        self.tool_definitions = list(self.background_tool_definitions) + [
            _create_background_task_tool(),
            _create_monitor_tool(_camera_enum_all),
            _stop_monitor_tool(),
            _create_alarm_tool(_camera_enum_all),
            _stop_alarm_tool(),
            _create_report_tool(),
            _stop_report_tool(),
            _enroll_face_tool(_camera_enum_all),
            _list_people_tool(),
            _forget_face_tool(),
        ]
        self.tool_handlers = {
            "describe_camera": self.tools.describe_camera,
            "detect_objects": self.tools.detect_objects,
            "recognize_faces": self.tools.recognize_faces,
            "search_footage": self.tools.search_footage,
            "recent_events": self.tools.recent_events,
            "create_background_task": self._handle_create_task,
            "create_monitor": self._handle_create_monitor,
            "stop_monitor": self._handle_stop_monitor,
            "create_alarm": self._handle_create_alarm,
            "stop_alarm": self._handle_stop_alarm,
            "create_report": self._handle_create_report,
            "stop_report": self._handle_stop_report,
            "enroll_face": self._handle_enroll_face,
            "list_people": self._handle_list_people,
            "forget_face": self._handle_forget_face,
        }

        self.agent_name = agent_name_for(cfg.voice_gender)
        self.faces = FaceClient(url=cfg.faces_url, token=cfg.faces_token) if cfg.faces_url else None
        self.notifier = Notifier(self, webhooks=cfg.notify_webhooks, events=cfg.notify_events)
        self.tasks = TaskManager(self)
        self.monitors = MonitorManager(self)
        self.alarms = AlarmManager(self)
        self.reports = ReportScheduler(self)
        self._stop_event = asyncio.Event()
        self._subscriber_task: asyncio.Task | None = None

    def _emergency_contact_for(self, name: str, target: str) -> str | None:
        contacts = self.cfg.emergency_contacts or {}
        for key in (target.lower(), name.lower()):
            if key in contacts:
                return str(contacts[key])
        return None

    async def _handle_create_alarm(self, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip() or "Alarm"
        target = str(args.get("target") or "").strip().lower()
        if not target:
            return "What should set off the alarm (e.g. fire, a person)?"
        cams = self.tools._resolve_cameras(args)
        if isinstance(cams, str):  # ERROR:
            return cams
        after_min = _parse_hhmm(args.get("after"))
        before_min = _parse_hhmm(args.get("before"))
        contact = self._emergency_contact_for(name, target)
        alarm = self.alarms.create(
            name=name, target=target, camera_ids=cams,
            after_min=after_min, before_min=before_min, emergency_contact=contact,
        )
        self.persist()
        where = "all cameras" if set(cams) == {c.camera_id for c in self.cfg.cameras} else ", ".join(cams)
        window = alarm.window_label()
        when = "" if window == "any time" else f" ({window})"
        extra = f" If it fires I'll flag the emergency contact ({contact})." if contact else ""
        return (f"Armed alarm #{alarm.id} '{name}' — I'll sound it if I see "
                f"{target} on {where}{when}.{extra}")

    async def _handle_stop_alarm(self, args: dict[str, Any]) -> str:
        try:
            aid = int(args.get("alarm_id"))
        except (TypeError, ValueError):
            return "Which alarm? Tell me its number."
        action = str(args.get("action") or "disarm").strip().lower()
        if action == "silence":
            return ("Silenced." if self.alarms.acknowledge(aid)
                    else f"Alarm #{aid} isn't currently ringing.")
        ok = self.alarms.stop(aid)
        self.persist()
        return (f"Disarmed alarm #{aid}." if ok else f"I don't have an alarm #{aid}.")

    async def _handle_create_report(self, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip() or "Report"
        query = str(args.get("query") or "").strip()
        if not query:
            return "What should the report summarize?"
        at_min = _parse_hhmm(args.get("at"))
        every = args.get("every_minutes")
        try:
            every = int(every) if every else None
        except (TypeError, ValueError):
            every = None
        sched = self.reports.create(name=name, query=query, at_min=at_min, every_minutes=every)
        self.persist()
        return (f"Scheduled report #{sched.id} '{name}' — {sched.schedule_label()}. "
                f"I'll deliver it here (and to your alert channels) each time it runs.")

    async def _handle_stop_report(self, args: dict[str, Any]) -> str:
        try:
            rid = int(args.get("report_id"))
        except (TypeError, ValueError):
            return "Which report? Tell me its number."
        ok = self.reports.stop(rid)
        self.persist()
        return (f"Cancelled report #{rid}." if ok else f"I don't have a report #{rid}.")

    async def _handle_enroll_face(self, args: dict[str, Any]) -> str:
        if not self.faces:
            return "Face management isn't configured on this system."
        name = str(args.get("name") or "").strip()
        if not name:
            return "What's the person's name?"
        cam = str(args.get("camera_id") or "").strip()
        if not self.context.known_camera(cam):
            cams = [c.camera_id for c in self.cfg.cameras]
            return f"Which camera should I capture from? Available: {cams}."
        try:
            frame = await self.context.get_frame(cam)
        except Exception:
            return f"I couldn't get a clear image from {cam} to enroll {name}."
        try:
            await self.faces.enroll(name=name, frame_jpeg=frame,
                                    category=str(args.get("category") or "known"))
        except Exception:
            logger.exception("enroll_face failed")
            return f"I couldn't enroll {name} — the recognizer didn't accept the face."
        return f"Got it — I'll recognise {name} from now on."

    async def _handle_list_people(self, args: dict[str, Any]) -> str:
        if not self.faces:
            return "Face management isn't configured on this system."
        try:
            people = await self.faces.list_people()
        except Exception:
            logger.exception("list_people failed")
            return "I couldn't reach the recognizer to list people."
        names = [str(p.get("name") if isinstance(p, dict) else p) for p in people]
        return "I know: " + ", ".join(names) + "." if names else "No one is enrolled yet."

    async def _handle_forget_face(self, args: dict[str, Any]) -> str:
        if not self.faces:
            return "Face management isn't configured on this system."
        name = str(args.get("name") or "").strip()
        if not name:
            return "Whose face should I forget?"
        try:
            await self.faces.forget(name)
        except Exception:
            logger.exception("forget_face failed")
            return f"I couldn't remove {name}."
        return f"Removed {name} from the watchlist."

    # ── Persistence ───────────────────────────────────────────────
    def persist(self) -> None:
        """Write alarms/watches/reports to the state file (best-effort)."""
        if not self.cfg.state_path:
            return
        import json
        import os
        import tempfile

        data = {
            "monitors": self.monitors.export(),
            "alarms": self.alarms.export(),
            "reports": self.reports.export(),
        }
        try:
            d = os.path.dirname(self.cfg.state_path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.cfg.state_path)  # atomic
        except Exception:  # pragma: no cover - best effort
            logger.exception("persist failed")

    def load_state(self) -> None:
        """Re-arm persisted alarms/watches/reports on startup (needs a loop)."""
        import json
        import os

        if not self.cfg.state_path or not os.path.exists(self.cfg.state_path):
            return

        try:
            data = json.loads(open(self.cfg.state_path).read())
        except Exception:
            logger.exception("could not read state file %s", self.cfg.state_path)
            return
        self.monitors.restore(data.get("monitors") or [])
        self.alarms.restore(data.get("alarms") or [], contact_for=self._emergency_contact_for)
        self.reports.restore(data.get("reports") or [])
        logger.info("restored %d watches, %d alarms, %d reports from %s",
                    len(data.get("monitors") or []), len(data.get("alarms") or []),
                    len(data.get("reports") or []), self.cfg.state_path)

    async def _handle_create_monitor(self, args: dict[str, Any]) -> str:
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in ("notify", "count", "crossing"):
            return "I can 'notify' you, keep a 'count', or count line 'crossing's — which would you like?"
        target = str(args.get("target") or "").strip().lower()
        if not target:
            return "What should I watch for (e.g. a person, a car)?"
        cams = self.tools._resolve_cameras(args)
        if isinstance(cams, str):  # ERROR:
            return cams
        line = args.get("line")
        if kind == "crossing":
            if not (isinstance(line, (list, tuple)) and len(line) == 4):
                return ("For crossing counts I need a line as [x1,y1,x2,y2] in "
                        "0-1 frame coordinates — where should the line go?")
            line = [float(v) for v in line]
        mon = self.monitors.create(
            kind=kind, camera_ids=cams, target=target,
            description=str(args.get("description") or "").strip(),
            line=line if kind == "crossing" else None,
        )
        self.persist()
        where = "all cameras" if set(cams) == {c.camera_id for c in self.cfg.cameras} else ", ".join(cams)
        if kind == "notify":
            return (f"Done — watch #{mon.id} is live. I'll let you know whenever "
                    f"I see a {target} on {where}.")
        if kind == "crossing":
            return (f"Done — watch #{mon.id} is counting {target}s crossing the line "
                    f"on {where} (needs the tracking adapter for accurate counts).")
        return (f"Done — I'm now counting {target}s on {where} (watch #{mon.id}); "
                f"you'll see a live tally in the panel.")

    async def _handle_stop_monitor(self, args: dict[str, Any]) -> str:
        try:
            mid = int(args.get("monitor_id"))
        except (TypeError, ValueError):
            return "Which watch should I stop? Tell me its number."
        ok = self.monitors.stop(mid)
        self.persist()
        return (f"Stopped watch #{mid}." if ok else f"I don't have a watch #{mid} running.")

    async def _handle_create_task(self, args: dict[str, Any]) -> str:
        """Tool handler: queue a long-running job and acknowledge so the
        conversation continues while it runs in the background."""
        query = str(args.get("query") or args.get("description") or "").strip()
        if not query:
            return "I need a bit more detail about what to look for."
        task = self.tasks.create(query)
        return (
            f"That one needs a closer look, so I've started it as background "
            f"task #{task.id}. I'll tell you as soon as I have the answer — "
            f"ask me anything else meanwhile."
        )

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

        # Pre-warm the vision detector so its model is loaded (and its
        # weights downloaded) BEFORE the first real question. This is the
        # fix for "camera offline at startup": the ai-adapter reports
        # healthy before its model weights finish downloading, so the
        # first detect would otherwise fail. We send a tiny synthetic
        # frame and retry with backoff until the adapter answers.
        # Best-effort: never blocks startup.
        self._vision_warmup_task = asyncio.create_task(
            self._prewarm_vision(), name="camera-agent-vision-prewarm"
        )

        # Re-arm anything persisted from a previous run, then start the
        # recurring report scheduler.
        self.load_state()
        self.reports.start()

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

    # A small valid white JPEG (32x32) — just enough to make the detector
    # load its model; YOLOv8 returns no detections on it.
    _WARMUP_JPEG = base64.b64decode(
        "/9j/4AAQSkZJRgABAgAAAQABAAD//gARTGF2YzU4LjEzNC4xMDAA/9sAQwAIBAQEBAQFBQUF"
        "BQUGBgYGBgYGBgYGBgYGBwcHCAgIBwcHBgYHBwgICAgJCQkICAgICQkKCgoMDAsLDg4OEREU"
        "/8QASwABAQAAAAAAAAAAAAAAAAAAAAcBAQAAAAAAAAAAAAAAAAAAAAAQAQAAAAAAAAAAAAAA"
        "AAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAAgACADASIAAhEAAxEA/9oADAMBAAIRAxEA"
        "PwC/gAAAAAAA/9k="
    )

    async def _prewarm_vision(self, *, attempts: int = 10, backoff_s: float = 6.0) -> None:
        """Force the detection adapter to load its model before the first
        real question. Retries over a window so it bridges the background
        weight download (the ai-adapter is 'healthy' before weights land)."""
        for attempt in range(1, attempts + 1):
            try:
                await self.detection_client.infer(frame_jpeg=self._WARMUP_JPEG)
                logger.info("camera-agent: vision detector warm (attempt %d).", attempt)
                return
            except Exception as exc:
                logger.info(
                    "camera-agent: vision pre-warm attempt %d/%d not ready (%s)",
                    attempt, attempts, exc,
                )
                await asyncio.sleep(backoff_s)
        logger.warning(
            "camera-agent: vision detector did not warm up after %d attempts; "
            "the first camera question may report the camera offline until the "
            "adapter finishes loading its model.",
            attempts,
        )

    async def shutdown(self) -> None:
        self._stop_event.set()
        self.monitors.stop_all()
        self.alarms.stop_all()
        self.reports.stop_all()
        if self._subscriber_task is not None:
            try:
                await asyncio.wait_for(self._subscriber_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._subscriber_task.cancel()
            except Exception:
                logger.exception("subscriber shutdown raised")
        # Close all reusable HTTP clients so pytest / uvicorn don't
        # log warnings about unclosed AsyncClient instances on exit.
        closers = [
            self.whisper.aclose(), self.ollama.aclose(), self.piper.aclose(),
            self.caption_client.aclose(), self.detection_client.aclose(),
            self.recognition_client.aclose(),
        ]
        if self.faces:
            closers.append(self.faces.aclose())
        await asyncio.gather(*closers, return_exceptions=True)

    # ── System prompt construction ────────────────────────────────

    def build_system_prompt(self) -> str:
        """Compose the system prompt the LLM sees: Ram's identity + the
        operator's base prompt + a per-camera roster + task guidance."""
        roster = "\n".join(
            f"- {cam.camera_id}: {cam.role}" for cam in self.cfg.cameras
        )
        return (
            f"Your name is {self.agent_name}. You are the OpenNVR camera agent. "
            f"When you introduce yourself, use the name {self.agent_name}.\n\n"
            f"{self.cfg.system_prompt.strip()}\n\n"
            f"Cameras available to you:\n{roster}\n\n"
            f"Always pass one of the camera_id values exactly as listed "
            f"when calling a tool.\n\n"
            f"You can look at ONE camera, SEVERAL, or 'all' of them — pass "
            f"camera_id='all' (or a camera_ids list) when the user asks about "
            f"every camera or more than one.\n\n"
            f"For STANDING requests — 'notify me when you see…', 'let me know "
            f"if…', 'keep counting…', 'count people entering…', 'watch cam X "
            f"for…' — call create_monitor (kind 'notify', 'count', or "
            f"'crossing' for line entry counts) instead of a one-off detection, "
            f"and confirm what you'll watch. Use stop_monitor to cancel one.\n\n"
            f"For URGENT, ringing requests — 'sound an alarm if…', 'fire alarm', "
            f"'alert me loudly if…', 'alarm if a person is seen after 6pm' — call "
            f"create_alarm (not create_monitor). Extract any time window into "
            f"'after'/'before' as 24h 'HH:MM' (e.g. 6pm → after '18:00'). An "
            f"alarm rings until acknowledged; use stop_alarm to silence or "
            f"disarm it.\n\n"
            f"For RECURRING summaries — 'every morning summarize overnight "
            f"activity', 'daily 7am rundown', 'every hour tell me the count' — "
            f"call create_report (set 'at' HH:MM for a daily time, or "
            f"'every_minutes'). Use stop_report to cancel one.\n\n"
            f"For questions about the RECORDED PAST (e.g. 'earlier today', "
            f"'two days ago at 3am', 'last week') searching footage can take "
            f"a while. For those, call create_background_task with a short "
            f"description instead of answering immediately, tell the user you'll "
            f"look into it and get back to them, and keep the conversation going. "
            f"You'll deliver the result when the task finishes."
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

    @app.get("/cameras")
    async def _cameras() -> dict[str, Any]:
        """Camera roster for the demo dropdown (id + role description)."""
        return {
            "cameras": [
                {"camera_id": cam.camera_id, "role": cam.role}
                for cam in runtime.cfg.cameras
            ]
        }

    @app.get("/agent")
    async def _agent() -> dict[str, Any]:
        """Lightweight identity for the UI (name + voice) — no TTS."""
        return {"name": runtime.agent_name, "voice_gender": runtime.cfg.voice_gender}

    @app.get("/notify")
    async def _notify_status() -> dict[str, Any]:
        """External-notification status (channel count + recent deliveries)."""
        return runtime.notifier.status()

    @app.post("/notify/test")
    async def _notify_test() -> JSONResponse:
        """Send a test notification to the configured webhooks."""
        if not runtime.notifier.enabled:
            return JSONResponse({"error": "no webhooks configured"}, status_code=400)
        ok = await runtime.notifier.send({
            "type": "test", "title": "Test notification",
            "text": f"This is a test from {runtime.agent_name}.", "severity": "info",
        })
        return JSONResponse({"delivered": ok, "channels": runtime.notifier.status()["channels"]})

    @app.get("/intro")
    async def _intro() -> JSONResponse:
        """The agent's greeting — text always; audio when Piper is reachable."""
        greeting = greeting_for(runtime.agent_name)
        audio_b64 = None
        try:
            audio = await runtime.piper.synthesize(greeting)
            if audio:
                audio_b64 = base64.b64encode(audio).decode("ascii")
        except Exception:
            logger.info("intro: TTS unavailable; returning text only")
        return JSONResponse({"name": runtime.agent_name, "text": greeting, "audio_b64": audio_b64})

    @app.get("/tasks")
    async def _tasks() -> dict[str, Any]:
        """Ram's background tasks (the UI polls this to surface results)."""
        return {"tasks": runtime.tasks.list()}

    @app.post("/tasks")
    async def _create_task(request: Request) -> JSONResponse:
        """Manually queue a background task (UI 'register a task' action)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        query = str((body or {}).get("query") or "").strip()
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)
        task = runtime.tasks.create(query)
        return JSONResponse(task.to_dict(), status_code=202)

    @app.post("/say")
    async def _say(request: Request) -> JSONResponse:
        """Synthesize arbitrary text so the UI can speak things Ram produces
        outside a /converse turn — e.g. announcing a finished background task
        aloud. Text-only fallback when Piper is unreachable."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str((body or {}).get("text") or "").strip()[:600]
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        audio_b64 = None
        try:
            audio = await runtime.piper.synthesize(text)
            if audio:
                audio_b64 = base64.b64encode(audio).decode("ascii")
        except Exception:
            logger.info("say: TTS unavailable; returning text-only")
        return JSONResponse({"audio_b64": audio_b64})

    @app.get("/monitors")
    async def _monitors() -> dict[str, Any]:
        """Standing monitors + any new notifications (UI polls this)."""
        return {
            "monitors": runtime.monitors.list(),
            "notifications": runtime.monitors.notifications(),
        }

    @app.post("/monitors")
    async def _create_monitor(request: Request) -> JSONResponse:
        """Manually register a monitor (UI 'add watch' action)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        msg = await runtime._handle_create_monitor(body or {})
        if msg.startswith("ERROR:") or msg.endswith("?"):
            return JSONResponse({"error": msg}, status_code=400)
        return JSONResponse({"message": msg, "monitors": runtime.monitors.list()}, status_code=202)

    @app.delete("/monitors/{monitor_id}")
    async def _stop_monitor(monitor_id: int) -> JSONResponse:
        ok = runtime.monitors.stop(monitor_id)
        runtime.persist()
        return JSONResponse({"stopped": ok}, status_code=200 if ok else 404)

    @app.get("/alarms")
    async def _alarms() -> dict[str, Any]:
        """Armed alarms + recent trigger events (UI polls; rings while any
        alarm is triggered)."""
        alarms = runtime.alarms.list()
        return {
            "alarms": alarms,
            "events": runtime.alarms.events(),
            "ringing": any(a["triggered"] for a in alarms),
        }

    @app.post("/alarms")
    async def _create_alarm(request: Request) -> JSONResponse:
        """Arm an alarm (UI presets + 'add alarm' use this)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        msg = await runtime._handle_create_alarm(body or {})
        if msg.startswith("ERROR:") or msg.endswith("?"):
            return JSONResponse({"error": msg}, status_code=400)
        return JSONResponse({"message": msg, "alarms": runtime.alarms.list()}, status_code=202)

    @app.post("/alarms/ack")
    async def _ack_alarms(request: Request) -> JSONResponse:
        """Acknowledge/silence a ringing alarm (or all). Keeps it armed."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        aid = body.get("alarm_id")
        n = runtime.alarms.acknowledge(int(aid) if aid is not None else None)
        return JSONResponse({"silenced": n})

    @app.delete("/alarms/{alarm_id}")
    async def _delete_alarm(alarm_id: int) -> JSONResponse:
        ok = runtime.alarms.stop(alarm_id)
        runtime.persist()
        return JSONResponse({"stopped": ok}, status_code=200 if ok else 404)

    @app.get("/reports")
    async def _reports() -> dict[str, Any]:
        """Scheduled reports + recently generated report results."""
        return {"schedules": runtime.reports.list(), "reports": runtime.reports.reports()}

    @app.post("/reports")
    async def _create_report(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        msg = await runtime._handle_create_report(body or {})
        if msg.endswith("?"):
            return JSONResponse({"error": msg}, status_code=400)
        return JSONResponse({"message": msg, "schedules": runtime.reports.list()}, status_code=202)

    @app.post("/reports/{report_id}/run")
    async def _run_report(report_id: int) -> JSONResponse:
        result = await runtime.reports.run_now(report_id)
        if result is None:
            return JSONResponse({"error": "no such report"}, status_code=404)
        return JSONResponse({"result": result})

    @app.delete("/reports/{report_id}")
    async def _delete_report(report_id: int) -> JSONResponse:
        ok = runtime.reports.stop(report_id)
        return JSONResponse({"stopped": ok}, status_code=200 if ok else 404)

    @app.get("/people")
    async def _people() -> JSONResponse:
        """Enrolled watchlist people (empty list when faces aren't configured)."""
        if not runtime.faces:
            return JSONResponse({"configured": False, "people": []})
        try:
            people = await runtime.faces.list_people()
        except Exception:
            return JSONResponse({"configured": True, "people": [], "error": "recognizer unreachable"})
        return JSONResponse({"configured": True, "people": people})

    @app.post("/people")
    async def _enroll(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        msg = await runtime._handle_enroll_face(body or {})
        ok = msg.startswith("Got it")
        return JSONResponse({"message": msg}, status_code=202 if ok else 400)

    @app.delete("/people/{name}")
    async def _forget(name: str) -> JSONResponse:
        msg = await runtime._handle_forget_face({"name": name})
        return JSONResponse({"message": msg})

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
        """Voice turn: audio blob in → {transcript, reply, audio_b64} out.

        Optional ``?camera=<id>`` query param is the UI-selected camera; it
        becomes the default when the user doesn't name a camera out loud."""
        blob = await request.body()
        if not blob:
            return JSONResponse(
                {"error": "empty audio"}, status_code=400
            )

        # UI-selected camera hint: one id, a comma list, or "all". Use the
        # first concrete configured camera as the grounding default; "all"
        # or empty leaves it to Ram.
        configured = {cam.camera_id for cam in runtime.cfg.cameras}
        raw_hint = request.query_params.get("camera") or ""
        camera_hint = None
        for part in raw_hint.split(","):
            part = part.strip()
            if part in configured:
                camera_hint = part
                break

        import time as _t

        t0 = _t.perf_counter()
        timings: dict[str, int] = {}

        def _mark(key: str, since: float) -> float:
            now = _t.perf_counter()
            timings[key] = int((now - since) * 1000)
            return now

        # 1) Normalise the recording to 16 kHz mono WAV (ffmpeg handles
        #    whatever container MediaRecorder produced).
        try:
            wav = await asyncio.to_thread(_transcode_to_wav16k, blob)
        except Exception as exc:
            logger.warning("converse: transcode failed: %s", exc)
            return JSONResponse({"error": "could not decode audio"}, status_code=400)
        t1 = _mark("transcode", t0)

        # 2) Transcribe.
        try:
            transcript = (await runtime.whisper.transcribe(wav)).strip()
        except Exception:
            logger.exception("converse: STT failed")
            return JSONResponse({"error": "transcription failed"}, status_code=502)
        t2 = _mark("stt", t1)
        logger.info("converse: transcript=%r", transcript)
        if not transcript:
            # Nothing intelligible — tell the UI so it can prompt a retry
            # instead of sending the LLM an empty turn.
            timings["total"] = int((_t.perf_counter() - t0) * 1000)
            return JSONResponse({"transcript": "", "reply": "", "audio_b64": None,
                                 "timings_ms": timings})

        # 3) LLM tool-calling loop.
        runtime.tools.last_cameras_used = []
        try:
            reply = await _run_conversation_turn(
                runtime, demo_history, transcript, preferred_camera=camera_hint
            )
        except Exception:
            logger.exception("converse: LLM turn failed")
            return JSONResponse({"error": "assistant failed"}, status_code=502)
        t3 = _mark("llm", t2)
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
        _mark("tts", t3)
        timings["total"] = int((_t.perf_counter() - t0) * 1000)

        return JSONResponse({
            "transcript": transcript, "reply": reply, "audio_b64": audio_b64,
            "cameras_used": list(runtime.tools.last_cameras_used),
            "timings_ms": timings,
        })

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


def _pick_camera(text: str, cameras: list[str], preferred: str | None = None) -> str:
    """Best-effort: which camera did the user mean? An explicit name in the
    utterance wins; otherwise fall back to the UI-selected ``preferred``
    camera, then to the first configured camera."""
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
    if preferred and preferred in cameras:
        return preferred
    return cameras[0]


async def _run_conversation_turn(
    runtime: "CameraAgentRuntime",
    history: list[dict[str, str]],
    user_text: str,
    *,
    max_iterations: int = 4,
    preferred_camera: str | None = None,
    tool_definitions: list[dict[str, Any]] | None = None,
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
    tools = tool_definitions if tool_definitions is not None else runtime.tool_definitions
    cameras = [cam.camera_id for cam in runtime.cfg.cameras]
    # UI-selected camera: when the user doesn't name one, bias the model
    # toward the camera the operator picked in the dropdown.
    if preferred_camera and preferred_camera in cameras:
        messages.append({
            "role": "system",
            "content": (
                f"The user is currently viewing camera '{preferred_camera}'. "
                f"If they ask about a camera without naming one, assume they "
                f"mean '{preferred_camera}'."
            ),
        })
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    final = ""
    grounded = False   # did any tool actually run this turn?
    forced = False     # have we already injected a forced detection?
    for iteration in range(max_iterations):
        response = await runtime.ollama.chat(
            messages=messages,
            tools=tools,
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
            cam = _pick_camera(user_text, cameras, preferred_camera)
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

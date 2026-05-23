# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Inference-listener example app.

The canonical subscriber-side template for KAI-C's NATS event bus.
Connects to NATS, subscribes to a configurable
``opennvr.inference.*`` subject pattern, and prints each
``InferenceCompletedEvent`` to stdout. Every alert in the published
stream is correlation-id-traceable back through KAI-C's audit log.

This is the simplest possible consumer — community contributors copy
it as a template for monitoring apps that prefer "subscribe to
inference results" over "drive inference via /infer". The key
difference from intrusion-detection: this app does NOT need its own
camera or its own KAI-C call. It receives results that some OTHER
component (intrusion-detection, a dashboard, etc.) already drove.
One adapter inference fans out to N subscribers.

Run:
    python inference_listener.py --config config.yml          # daemon
    python inference_listener.py --config config.yml --once   # one event then exit (testing)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("inference-listener")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class AppConfig:
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    once: bool = False


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed AppConfig. Raises
    ``ValueError`` on malformed input — caller's job to surface a
    useful operator message and exit non-zero."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")
    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required")
    # Distinguish "absent" (use default wildcard) from "present but
    # empty" (explicit misconfig — refuse to start).
    if "subject_pattern" in raw:
        subject = str(raw.get("subject_pattern") or "").strip()
        if not subject:
            raise ValueError("config: 'subject_pattern' must not be empty")
    else:
        subject = "opennvr.inference.>"
    return AppConfig(
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
    )


# ── Subscriber ─────────────────────────────────────────────────────


class InferenceListener:
    """Holds the NATS connection + the subject subscription. The
    main loop runs in an asyncio event loop; ``stop()`` (or SIGINT /
    SIGTERM) cleanly drains and exits.

    Override ``handle_event(event_dict)`` in a subclass to plug your
    own logic in — that's the extension point for community apps
    (route to Slack, count detections, update a dashboard, etc.).
    The default implementation prints the event to stdout in a
    one-line-per-event format suitable for ``tail -f``.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._stop_event = asyncio.Event()
        self._nc: Any = None
        self._received_count: int = 0

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        """Connect, subscribe, loop until stop_event. Always cleans
        up the NATS connection."""
        # Lazy import — operators on the disabled path don't pay it.
        import nats

        connect_kwargs: dict[str, Any] = {
            "servers": [self._config.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            "max_reconnect_attempts": -1,  # keep retrying forever
        }
        if self._config.nats_token:
            connect_kwargs["token"] = self._config.nats_token
        self._nc = await nats.connect(**connect_kwargs)
        logger.info(
            "connected to %s, subscribing to %r",
            self._config.nats_url, self._config.subject_pattern,
        )
        try:
            sub = await self._nc.subscribe(self._config.subject_pattern)
            async for msg in sub.messages:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "skipping non-JSON message on %r: %s",
                        msg.subject, exc,
                    )
                    continue
                self._received_count += 1
                try:
                    self.handle_event(msg.subject, payload)
                except Exception:
                    # No single event handler failure should kill the
                    # subscriber. Operators will see the traceback in
                    # the log and the next event is still processed.
                    logger.exception("handler failed for subject=%s", msg.subject)
                if self._config.once:
                    self.stop()
                if self._stop_event.is_set():
                    break
        finally:
            try:
                await self._nc.drain()
            except Exception:
                try:
                    await self._nc.close()
                except Exception:
                    pass

    # ── Extension point ───────────────────────────────────────────

    def handle_event(self, subject: str, payload: dict[str, Any]) -> None:
        """Default handler: print a one-line summary. Override in
        subclasses for real processing.

        ``payload`` is the JSON-decoded ``InferenceCompletedEvent``
        body — see ``kai_c/events.py`` in the KAI-C source for the
        schema. Key fields:

        * ``correlation_id`` — joins back to KAI-C's audit log
        * ``adapter`` / ``adapter_version``
        * ``camera_id`` (or ``"unknown"`` for events without one)
        * ``model_name`` / ``model_version`` / ``model_fingerprint``
        * ``inference_ms``
        * ``result`` — the §5.x task-specific result body
        """
        detections = (payload.get("result") or {}).get("detections")
        det_summary = ""
        if isinstance(detections, list):
            det_summary = f" detections={len(detections)}"
            if detections:
                labels = [str(d.get("label", "?")) for d in detections[:3]]
                det_summary += f" [{', '.join(labels)}{', …' if len(detections) > 3 else ''}]"
        print(
            f"INFERENCE [{payload.get('adapter', '?')}/{payload.get('camera_id', '?')}] "
            f"correlation_id={payload.get('correlation_id', '?')} "
            f"inference_ms={payload.get('inference_ms', 0)}{det_summary}",
            flush=True,
        )


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="inference-listener",
        description="Subscribe to KAI-C's NATS inference broadcast surface.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one event and exit (smoke testing).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    config.once = args.once

    listener = InferenceListener(config)

    # SIGINT / SIGTERM → graceful drain.
    loop = asyncio.new_event_loop()

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(listener.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(listener.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

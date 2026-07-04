# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Inference-listener example app — the "hello world" of the
``opennvr-app-sdk``.

Connects to NATS, subscribes to a configurable ``opennvr.inference.*``
subject pattern, and prints each ``InferenceCompletedEvent`` to stdout.
Every event in the stream is correlation-id-traceable back through
KAI-C's audit log.

This is the simplest possible :class:`~opennvr_app_sdk.Detector`
(App SDK spec §02): the SDK base owns the NATS connect / subscribe /
drain loop, per-message JSON decoding + exception isolation, the §03
contract endpoints, and the CLI / signal lifecycle behind
``app(InferenceListener).run()``. What's left here is the sink —
``handle_event`` — plus config parsing and the MANIFEST.

One deliberate twist: a stock Detector filters events down to
``on_detections(camera_id, detections, event)``, dropping anything
without a camera or a detections list. This listener prints EVERY
event on the bus — ASR transcripts, captions, whatever — so it plugs
in one hook lower, overriding ``_handle_raw`` and keeping its
historical ``handle_event(subject, payload)`` extension point.
Copy-as-template rule of thumb: if your app reacts to detections,
implement ``on_detections``; if it wants the raw firehose, do this.

Run:
    python inference_listener.py --config config.yml          # daemon
    python inference_listener.py --config config.yml --once   # one event then exit (testing)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from opennvr_app_sdk import AppManifest, Detector, Param, app
from opennvr_app_sdk.config import load_yaml

logger = logging.getLogger("inference-listener")


MANIFEST = AppManifest(
    id="inference-listener",
    name="Inference Listener",
    version="1.0.0",
    category="observability",
    summary=(
        "Prints every InferenceCompletedEvent on the opennvr.inference.* "
        "bus — the copy-as-template subscriber example."
    ),
    requires_tasks=[],  # listens to whatever is already flowing
    subscribes="opennvr.inference.>",
    params=[
        Param("subject_pattern", str, default="opennvr.inference.>"),
    ],
    emits=[],  # prints to stdout; fires no alerts
)


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
    raw = load_yaml(path)
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


class InferenceListener(Detector):
    """The SDK base owns the NATS loop; ``stop()`` (or SIGINT /
    SIGTERM via the runner) cleanly drains and exits.

    Override ``handle_event(subject, payload)`` in a subclass to plug
    your own logic in — that's the extension point for community apps
    (route to Slack, count detections, update a dashboard, etc.).
    The default implementation prints the event to stdout in a
    one-line-per-event format suitable for ``tail -f``.
    """

    manifest = MANIFEST

    def __init__(self, config: AppConfig, dispatcher: Any = None) -> None:
        # ``dispatcher`` is unused (this app fires no alerts) but the
        # SDK runner passes one; tests construct with config alone.
        super().__init__(config, dispatcher)
        self._received_count: int = 0

    def _handle_raw(self, data: bytes, *, subject: str = "") -> list:
        """Raw-firehose hook (see module docstring): decode + isolate,
        then print — no camera_id / detections filtering."""
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("skipping non-JSON message on %r: %s", subject, exc)
            return []
        self._contract_note_event()
        self._received_count += 1
        try:
            self.handle_event(subject, payload)
        except Exception:
            # No single event handler failure should kill the
            # subscriber. Operators will see the traceback in
            # the log and the next event is still processed.
            logger.exception("handler failed for subject=%s", subject)
        return []

    def state_snapshot(self) -> dict[str, Any]:
        """``GET /state`` — the running receive counter."""
        return {"received": self._received_count}

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
    """Console-script entry point (``[project.scripts]``). The SDK
    runner owns argparse, logging, signals, and the loop lifecycle."""
    return app(InferenceListener, load_config=load_config).run(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

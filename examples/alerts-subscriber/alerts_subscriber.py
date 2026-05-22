# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Alerts-subscriber example app.

The canonical subscriber-side template for OpenNVR's alert fan-out
(NATS alert fan-out). Connects to NATS, subscribes to a configurable
``opennvr.alerts.*`` subject pattern, prints each §11.5 Alert envelope
to stdout, and optionally forwards via webhook.

This is the alert-side companion to ``inference-listener``: that one
subscribes to ``opennvr.inference.*``; this one subscribes to
``opennvr.alerts.*``. Same library, same template shape, different
subject family.

What this is for
----------------

* **Operator-UI alerts inbox**: subscribe to ``opennvr.alerts.>`` and
  upsert into the database / push to the websocket the UI is listening
  on.
* **SIEM bridge**: subscribe to ``opennvr.alerts.>``, forward JSON to
  Splunk / Elastic / Datadog ingestion. Source field tells you which
  app emitted the alert.
* **Slack / PagerDuty bridge**: subscribe to one severity
  (``opennvr.alerts.*.*.cam-X`` filtered server-side, or filter on
  ``severity`` in code) and forward to a chat/incident tool.
* **Audit forwarder**: subscribe + write to durable storage so a
  broker outage doesn't lose history. (B2 follow-up adds JetStream
  durable consumers; for v1 this app is fire-and-forget.)

Why this is a separate example
------------------------------

Community contributors will write app-specific consumers (their own
PagerDuty rules, their own Splunk schemas). This subscriber is the
template: copy this folder, override ``handle_alert(subject, alert)``,
swap the AlertConsumer class for your own.

Run:
    python alerts_subscriber.py --config config.yml          # daemon
    python alerts_subscriber.py --config config.yml --once   # one alert then exit (testing)
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

import httpx
import yaml

logger = logging.getLogger("alerts-subscriber")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class AppConfig:
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    webhook_url: str | None = None
    webhook_timeout_seconds: float = 5.0
    once: bool = False


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed AppConfig.

    Raises ``ValueError`` on malformed input — caller's job to surface
    a useful operator message and exit non-zero.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")
    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required")
    # Distinguish "absent" (use default wildcard) from "present but
    # empty" (operator misconfig — refuse to start, same shape as
    # the inference-listener example).
    if "subject_pattern" in raw:
        subject = str(raw.get("subject_pattern") or "").strip()
        if not subject:
            raise ValueError("config: 'subject_pattern' must not be empty")
    else:
        subject = "opennvr.alerts.>"
    return AppConfig(
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        webhook_timeout_seconds=float(raw.get("webhook_timeout_seconds", 5.0)),
    )


# ── Subscriber ─────────────────────────────────────────────────────


class AlertConsumer:
    """Holds the NATS connection + the subscription. The main loop
    runs in an asyncio event loop; ``stop()`` (or SIGINT / SIGTERM)
    cleanly drains and exits.

    Override ``handle_alert(subject, alert_dict)`` in a subclass to
    plug your own logic in — that's the extension point for
    operator-UI inbox writers, SIEM bridges, Slack bots, etc. The
    default implementation prints the alert to stdout in a one-line-
    per-alert format and, if ``webhook_url`` is set, forwards the
    raw JSON via HTTP POST.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._stop_event = asyncio.Event()
        self._nc: Any = None
        self._received_count: int = 0
        self._forwarded_count: int = 0
        self._forward_failed_count: int = 0

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        """Connect, subscribe, loop until stop_event. Always cleans
        up the NATS connection on exit."""
        # Lazy import — operators on the disabled path don't pay it.
        import nats

        connect_kwargs: dict[str, Any] = {
            "servers": [self._config.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            # Subscribers keep retrying forever — a transient broker
            # restart shouldn't kill the consumer. Operators stop it
            # explicitly via SIGINT/SIGTERM.
            "max_reconnect_attempts": -1,
        }
        if self._config.nats_token:
            connect_kwargs["token"] = self._config.nats_token
        self._nc = await nats.connect(**connect_kwargs)
        logger.info(
            "connected to %s, subscribing to %r (webhook=%s)",
            self._config.nats_url,
            self._config.subject_pattern,
            self._config.webhook_url or "none",
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
                    self.handle_alert(msg.subject, payload)
                except Exception:
                    # No single alert-handler failure should kill the
                    # subscriber. The operator sees the traceback and
                    # the next alert is still processed.
                    logger.exception(
                        "handler failed for subject=%s", msg.subject,
                    )
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
            logger.info(
                "alerts-subscriber shutting down — received=%d "
                "webhook_forwarded=%d webhook_failed=%d",
                self._received_count,
                self._forwarded_count,
                self._forward_failed_count,
            )

    # ── Extension point ───────────────────────────────────────────

    def handle_alert(self, subject: str, alert: dict[str, Any]) -> None:
        """Default handler: print a one-line summary + optional
        webhook forward.

        ``alert`` is the JSON-decoded §11.5 Alert envelope —
        ``alert_id``, ``fired_at``, ``title``, ``description``,
        ``severity``, ``source``, ``camera_id``, ``correlation_id``,
        ``evidence``, ``tags``.

        Override in a subclass for real processing (DB insert, SIEM
        forward, Slack notification, etc.). The default behavior is
        deliberately operator-grep-friendly.
        """
        line = (
            f"ALERT [{str(alert.get('severity', '?')).upper()}] "
            f"{alert.get('fired_at', '?')} "
            f"subject={subject} "
            f"camera={alert.get('camera_id', '?')} "
            f"title={alert.get('title', '?')!r} "
            f"source={(alert.get('source') or {}).get('name', '?')} "
            f"correlation_id={alert.get('correlation_id') or '-'} "
            f"alert_id={alert.get('alert_id', '?')}"
        )
        print(line, flush=True)

        if self._config.webhook_url:
            ok = self._forward_to_webhook(alert)
            if ok:
                self._forwarded_count += 1
            else:
                self._forward_failed_count += 1

    def _forward_to_webhook(self, alert: dict[str, Any]) -> bool:
        """POST the raw §11.5 JSON to ``webhook_url``. Failures are
        LOGGED but never raise — same shape as intrusion-detection's
        WebhookChannel.

        We use a fresh ``httpx.post`` per alert (no shared session)
        because: (a) the alert rate is low compared to inference;
        (b) keeping this fully stateless makes ``handle_alert``
        trivially overridable from a subclass without worrying about
        client lifecycle.
        """
        try:
            response = httpx.post(
                self._config.webhook_url,
                json=alert,
                timeout=self._config.webhook_timeout_seconds,
                # trust_env=False to ignore operator-side HTTP_PROXY
                # — alert forwarding should go direct, not through
                # whatever proxy might be configured for general http.
                trust_env=False,
            )
            if response.status_code >= 400:
                logger.warning(
                    "webhook %s returned %d: %s",
                    self._config.webhook_url,
                    response.status_code,
                    response.text[:200],
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "webhook %s failed: %s", self._config.webhook_url, exc,
            )
            return False


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alerts-subscriber",
        description="Subscribe to OpenNVR's alert fan-out NATS surface.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one alert and exit (smoke testing).",
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

    consumer = AlertConsumer(config)

    # SIGINT / SIGTERM → graceful drain.
    loop = asyncio.new_event_loop()

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(consumer.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        loop.run_until_complete(consumer.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

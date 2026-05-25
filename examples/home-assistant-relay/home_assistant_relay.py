# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
home-assistant-relay — bridge OpenNVR alerts into Home Assistant.

Subscribes to ``opennvr.alerts.>`` on NATS, runs each alert through
the HA mapper (alert envelope → HA entity definition + state),
publishes via either MQTT discovery or HA's REST API, and schedules
an auto-off flip for binary_sensors after a configurable window so
the dashboard reads like an event log, not a sticky alarm.

Why MQTT discovery is the default
----------------------------------
HA's MQTT integration discovers entities on first publish to a magic
topic (default ``homeassistant/<component>/<unique_id>/config``). HA
auto-creates the entity with the right device_class + friendly name
+ device card. Subsequent state publishes flow on a separate
``state`` topic. Zero HA UI clicks required — the entity appears
the first time the alert fires.

REST API is offered as a fallback for users who can't run MQTT.
Entities still surface, just without the device card.

Run:
    python home_assistant_relay.py --config config.yml
    python home_assistant_relay.py --config config.yml --once   # one alert then exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ha_mapper import HaEntity, HaMapper, MappingOverride, parse_overrides
from publishers import (
    MqttConfig,
    MqttPublisher,
    Publisher,
    RestConfig,
    RestPublisher,
)

logger = logging.getLogger("home-assistant-relay")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class AppConfig:
    """Operator-tunable settings. Validated in ``load_config``."""

    # NATS — where we read alerts from.
    nats_url: str
    nats_token: str | None = None
    subject_pattern: str = "opennvr.alerts.>"

    # Backend selection — "mqtt" or "rest". Validated in load_config.
    backend: str = "mqtt"

    # Either mqtt_config or rest_config is populated; the other is
    # None depending on the backend choice.
    mqtt_config: MqttConfig | None = None
    rest_config: RestConfig | None = None

    # Per-(source, camera_id) overrides for the mapper.
    overrides: list[MappingOverride] = field(default_factory=list)

    # Default auto-off window when an override doesn't specify one.
    default_auto_off_seconds: int = 30


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"config file {path} did not parse to a dict")

    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise SystemExit("config: nats_url is required")

    backend = str(raw.get("backend") or "mqtt").strip().lower()
    if backend not in ("mqtt", "rest"):
        raise SystemExit(
            f"config: backend must be 'mqtt' or 'rest'; got {backend!r}"
        )

    mqtt_config: MqttConfig | None = None
    rest_config: RestConfig | None = None
    default_auto_off = 30

    if backend == "mqtt":
        mqtt_raw = raw.get("mqtt") or {}
        if not isinstance(mqtt_raw, dict):
            raise SystemExit("config: mqtt block must be a mapping")
        host = str(mqtt_raw.get("host") or "127.0.0.1").strip()
        if not host:
            raise SystemExit("config: mqtt.host must not be empty")
        try:
            port = int(mqtt_raw.get("port", 1883))
            qos = int(mqtt_raw.get("qos", 1))
            auto_off = int(mqtt_raw.get("auto_off_seconds", 30))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"config: mqtt numeric field invalid: {exc}")
        if qos not in (0, 1, 2):
            raise SystemExit(f"config: mqtt.qos must be 0, 1 or 2; got {qos}")
        if auto_off < 0:
            raise SystemExit(
                f"config: mqtt.auto_off_seconds must be >= 0; got {auto_off}"
            )
        mqtt_config = MqttConfig(
            host=host,
            port=port,
            username=(str(mqtt_raw["username"]) if mqtt_raw.get("username") else None),
            password=(str(mqtt_raw["password"]) if mqtt_raw.get("password") else None),
            discovery_prefix=str(
                mqtt_raw.get("discovery_prefix") or "homeassistant"
            ).strip() or "homeassistant",
            auto_off_seconds=auto_off,
            qos=qos,
            retain=bool(mqtt_raw.get("retain", True)),
        )
        default_auto_off = auto_off

    else:  # rest
        rest_raw = raw.get("rest") or {}
        if not isinstance(rest_raw, dict):
            raise SystemExit("config: rest block must be a mapping when backend=rest")
        url = str(rest_raw.get("url") or "").strip()
        token = str(rest_raw.get("token") or "").strip()
        if not url:
            raise SystemExit("config: rest.url is required when backend=rest")
        if not token:
            raise SystemExit("config: rest.token is required when backend=rest")
        try:
            timeout = float(rest_raw.get("timeout_seconds", 5.0))
            auto_off = int(rest_raw.get("auto_off_seconds", 30))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"config: rest numeric field invalid: {exc}")
        if auto_off < 0:
            raise SystemExit(
                f"config: rest.auto_off_seconds must be >= 0; got {auto_off}"
            )
        rest_config = RestConfig(
            url=url,
            token=token,
            timeout_seconds=timeout,
            auto_off_seconds=auto_off,
        )
        default_auto_off = auto_off

    overrides_raw = raw.get("mappings") or []
    if overrides_raw and not isinstance(overrides_raw, list):
        raise SystemExit("config: mappings must be a list")
    overrides = parse_overrides(overrides_raw)

    subject = str(raw.get("subject_pattern") or "opennvr.alerts.>").strip()
    if not subject:
        raise SystemExit("config: subject_pattern must not be empty")

    nats_token_raw = raw.get("nats_token")
    # Strip trailing whitespace / newline — a common foot-gun when
    # the operator's token came from ``cat token | yq``.
    nats_token = str(nats_token_raw).strip() if nats_token_raw else None
    if nats_token == "":
        nats_token = None

    return AppConfig(
        nats_url=nats_url,
        nats_token=nats_token,
        subject_pattern=subject,
        backend=backend,
        mqtt_config=mqtt_config,
        rest_config=rest_config,
        overrides=overrides,
        default_auto_off_seconds=default_auto_off,
    )


# ── Relay daemon ───────────────────────────────────────────────────


class HomeAssistantRelay:
    """The daemon. One instance per process.

    Lifecycle:
      ``await relay.run()`` — connects to NATS, subscribes, dispatches
      every alert through the mapper into the publisher. Blocks until
      ``stop()`` (or SIGINT / SIGTERM) fires.

    Auto-off:
      For each binary_sensor we publish, schedule a task that flips
      it back to OFF after ``entity.auto_off_seconds``. If a fresh
      alert arrives for the same entity while a timer is pending,
      we cancel the old timer and start a new one — keeps the
      entity "ON" as long as alerts keep firing.
    """

    def __init__(
        self,
        config: AppConfig,
        mapper: HaMapper,
        publisher: Publisher,
    ) -> None:
        self._config = config
        self._mapper = mapper
        self._publisher = publisher
        self._stop_event = asyncio.Event()
        self._auto_off_tasks: dict[str, asyncio.Task] = {}
        # Per-entity monotonic generation counter. Each fresh alert
        # bumps the entity's generation; the auto-off task captures
        # its generation at schedule-time and re-checks at fire-time.
        # If a newer generation has appeared in between (operator
        # alert refired between sleep-return and publish), the late
        # OFF is suppressed so it can't override the fresh ON.
        self._entity_generation: dict[str, int] = {}
        # Stop one-off mode after the first published alert.
        self._once_mode = False
        # Counters for the shutdown summary line — useful for
        # operators tailing the log.
        self._received_count = 0
        self._published_count = 0
        self._failed_count = 0
        self._skipped_count = 0

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        # Lazy import — keeps the module importable in test envs
        # that don't have nats-py.
        import nats  # type: ignore

        connect_kwargs: dict[str, Any] = {
            "servers": [self._config.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            "max_reconnect_attempts": -1,
        }
        if self._config.nats_token:
            connect_kwargs["token"] = self._config.nats_token
        nc = await nats.connect(**connect_kwargs)
        logger.info(
            "connected to %s, subscribing to %r (backend=%s)",
            self._config.nats_url,
            self._config.subject_pattern,
            self._config.backend,
        )
        try:
            sub = await nc.subscribe(self._config.subject_pattern)
            async for msg in sub.messages:
                if self._stop_event.is_set():
                    break
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "skipping non-JSON message on %r: %s",
                        msg.subject, exc,
                    )
                    continue
                await self._handle_alert(payload)
                if self._stop_event.is_set():
                    break
        finally:
            # Cancel any pending auto-off tasks so we don't block on
            # them — operator wants a fast exit.
            for task in self._auto_off_tasks.values():
                task.cancel()
            self._auto_off_tasks.clear()
            try:
                await nc.drain()
            except Exception:
                try:
                    await nc.close()
                except Exception:
                    logger.exception("nats close failed")
            try:
                await self._publisher.aclose()
            except Exception:
                logger.exception("publisher close failed")
            logger.info(
                "home-assistant-relay shutting down — received=%d "
                "published=%d failed=%d skipped=%d",
                self._received_count, self._published_count,
                self._failed_count, self._skipped_count,
            )

    async def step(self, alert: dict[str, Any]) -> None:
        """Process one alert. Public so tests can drive without NATS."""
        await self._handle_alert(alert)

    async def _handle_alert(self, alert: dict[str, Any]) -> None:
        self._received_count += 1
        entity = self._mapper.map(alert)
        if entity is None:
            self._skipped_count += 1
            return
        ok = await self._publisher.publish_state(entity)
        if not ok:
            self._failed_count += 1
            return
        self._published_count += 1
        self._maybe_schedule_auto_off(entity)
        if self._once_mode:
            self.stop()

    def _maybe_schedule_auto_off(self, entity: HaEntity) -> None:
        # sensor entities don't get an auto-off; they hold their
        # last value until something changes it.
        if entity.component != "binary_sensor":
            return
        if entity.auto_off_seconds <= 0:
            return

        key = entity.full_entity_id
        # Bump the generation for this entity. Any in-flight auto-off
        # task captured the prior generation and will short-circuit
        # before touching the publisher.
        generation = self._entity_generation.get(key, 0) + 1
        self._entity_generation[key] = generation

        # Cancel any pending timer so a rapid succession of alerts
        # keeps the sensor ON for the full window from the last one.
        # The generation check below is a belt-and-braces guard for
        # the case where the cancel races a timer that just woke up.
        existing = self._auto_off_tasks.pop(key, None)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _flip_off_later() -> None:
            try:
                await asyncio.sleep(entity.auto_off_seconds)
            except asyncio.CancelledError:
                return
            # Generation re-check: if a newer alert bumped the
            # counter while we were sleeping (or while we were about
            # to call publish_off), suppress this OFF — it would
            # land *after* the fresh ON otherwise.
            if self._entity_generation.get(key) != generation:
                return
            try:
                await self._publisher.publish_off(entity)
            except Exception:
                logger.exception(
                    "auto-off publish failed for %s", entity.full_entity_id,
                )
            finally:
                # Self-cleanup — don't leave finished tasks in the
                # dict, otherwise memory creeps over a long run.
                self._auto_off_tasks.pop(key, None)

        self._auto_off_tasks[key] = asyncio.create_task(
            _flip_off_later(), name=f"ha-auto-off-{key}",
        )


# ── Publisher factory ──────────────────────────────────────────────


def build_publisher(config: AppConfig) -> Publisher:
    if config.backend == "mqtt":
        assert config.mqtt_config is not None
        return MqttPublisher(config.mqtt_config)
    if config.backend == "rest":
        assert config.rest_config is not None
        return RestPublisher(config.rest_config)
    raise SystemExit(f"config: unknown backend {config.backend!r}")


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="home-assistant-relay",
        description=(
            "Bridge OpenNVR NATS alerts into Home Assistant entities."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--once", action="store_true",
        help="Process one alert and exit (smoke testing).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except OSError as exc:
        # File-not-found / permission-denied → friendly stderr line
        # + exit 2 (same shape as the other gallery examples).
        # ``load_config`` itself raises SystemExit on schema errors,
        # which propagates naturally with its own message + code.
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    mapper = HaMapper(
        overrides=config.overrides,
        default_auto_off_seconds=config.default_auto_off_seconds,
    )
    publisher = build_publisher(config)
    relay = HomeAssistantRelay(config, mapper, publisher)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(_signum, _frame):
        logger.info("signal received, stopping…")
        loop.call_soon_threadsafe(relay.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        relay._once_mode = True  # type: ignore[attr-defined]

    try:
        loop.run_until_complete(relay.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

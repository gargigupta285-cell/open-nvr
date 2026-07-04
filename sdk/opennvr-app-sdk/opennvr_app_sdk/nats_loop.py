# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
The shared NATS subscribe loop — connect / subscribe / decode / drain.

Both NATS-riding archetypes (:class:`~.detector.Detector` on
``opennvr.inference.*``, :class:`~.alert_subscriber.AlertSubscriber` on
``opennvr.alerts.*``) run byte-identical loop machinery: connect with
infinite reconnects, subscribe to ``cfg.subject_pattern``, hand every
raw message to ``_handle_raw`` (per-message isolation lives there, in
the archetype), honor ``--once`` and ``stop()``, and drain gracefully
on the way out. The loop was born in the loitering-detection example's
``main()`` and lived on ``Detector`` until the AlertSubscriber
archetype landed; it is factored here so the two bases can't drift.

Host requirements (duck-typed, set by the archetype ``__init__``):

* ``cfg`` with ``nats_url`` / ``subject_pattern`` (+ optional
  ``nats_token``);
* ``_stop_event`` — an ``asyncio.Event``;
* ``_handle_raw(data, *, subject)`` — the per-message entry point;
* ``manifest`` — optional, used only for the startup log line.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


class NatsSubscriberMixin:
    """The connect / subscribe / dispatch / drain loop shared by the
    NATS-subscribing archetype bases. See module docstring for the
    attributes the host class must provide."""

    cfg: Any
    manifest: Any
    _stop_event: asyncio.Event
    _nc: Any

    def stop(self) -> None:
        self._stop_event.set()

    def _handle_raw(self, data: bytes, *, subject: str = "") -> Any:
        """Per-message entry point — implemented by the archetype
        (decode + isolate + dispatch)."""
        raise NotImplementedError  # pragma: no cover — archetype hook

    async def _run_nats_loop(self, *, once: bool) -> None:
        import nats

        connect_kwargs: dict[str, Any] = {
            "servers": [self.cfg.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            "max_reconnect_attempts": -1,
        }
        token = getattr(self.cfg, "nats_token", None)
        if token:
            connect_kwargs["token"] = token
        self._nc = await nats.connect(**connect_kwargs)
        logger.info(
            "%s started: subject=%r",
            self.manifest.id if self.manifest else type(self).__name__,
            self.cfg.subject_pattern,
        )
        try:
            sub = await self._nc.subscribe(self.cfg.subject_pattern)
            async for msg in sub.messages:
                # ``_handle_raw`` is sync on the stock archetypes, but
                # apps with async sinks (the home-assistant-relay's
                # publishers are awaitable) may override it as a
                # coroutine — await it so per-message ordering and
                # backpressure are preserved either way.
                result = self._handle_raw(msg.data, subject=msg.subject)
                if inspect.isawaitable(result):
                    await result
                if once:
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


__all__ = ["NatsSubscriberMixin"]

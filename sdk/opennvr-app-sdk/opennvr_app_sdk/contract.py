# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
The app contract surface — spec §03, mirroring the adapter contract.

Adapters self-register with KAI-C and expose ``/health`` +
``/capabilities``; SDK apps do the identical thing against the OpenNVR
app registry (``server/routers/apps.py``). Two pieces live here:

* :class:`ContractServer` — a stdlib-only (``http.server``) HTTP
  server on a daemon thread serving the three contract endpoints:

  - ``GET /health``   → ``{"ready", "uptime_s", "events_seen",
    "alerts_fired", "last_event_age_s"}`` — powers the catalog status
    dot and stall detection;
  - ``GET /manifest`` → the static ``AppManifest.to_dict()`` — powers
    the catalog card + auto-generated config form;
  - ``GET /state``    → the app-provided live snapshot
    (:meth:`ContractMixin.state_snapshot`, default ``{}``) — powers
    the per-app dashboard.

* :class:`ContractMixin` — the counters + lifecycle glue the
  ``Detector`` / ``FrameApp`` bases inherit. Everything is **off by
  default**: the server only starts when the app config carries a
  ``contract_port`` (int), and self-registration only fires when it
  carries an ``opennvr_url``.

Config keys read (all via ``getattr``, all optional):

``contract_port``
    Port for the contract server. ``0`` binds an ephemeral port (the
    actual port is advertised at registration time). Absent ⇒ no
    server, no registration.
``contract_bind_host``
    Interface to bind (default ``0.0.0.0``).
``contract_host``
    Hostname advertised in the registration URL (default: this
    machine's ``socket.gethostname()`` — in Docker that is the
    container/service name the registry can reach).
``opennvr_url``
    Base URL of the OpenNVR backend. When set, ``run()`` POSTs
    ``{opennvr_url}/api/v1/apps/register`` on boot — best-effort: a
    down/refusing registry logs a warning and the app keeps running.
``opennvr_token``
    Optional token for the registration call (the registry routes are
    auth-gated). Sent BOTH as a bearer ``Authorization`` header (for
    user-JWT tokens) and as ``X-Internal-Api-Key`` (the deployment's
    ``INTERNAL_API_KEY``, which the register route accepts as a
    service credential). Falls back to the
    ``OPENNVR_INTERNAL_API_KEY`` environment variable when the config
    key is absent — the natural fit for compose deployments.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

# Budget for the one-shot registration POST at boot. Tight on purpose:
# a slow registry must not stall app startup for long.
REGISTER_TIMEOUT_SECONDS = 5.0

# Captured at import so the contract counters keep reading the REAL
# clock even when an app's tests monkeypatch ``time.monotonic`` to
# drive their domain state machines (package-delivery's linger test
# feeds a finite fake-clock iterator — operational bookkeeping must
# never consume ticks from the app's timeline).
_monotonic = time.monotonic


# ── The HTTP server ────────────────────────────────────────────────


class _ContractRequestHandler(BaseHTTPRequestHandler):
    """Routes GETs to the callables installed on the server instance.

    Anything that isn't one of the three contract paths is a JSON 404;
    a raising snapshot callable is a JSON 500 — the server never takes
    the app down."""

    server: "_ContractHTTPServer"  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        route = self.server.routes.get(path)
        if route is None:
            self._send_json(404, {"error": f"unknown path {path!r}"})
            return
        try:
            body = route()
        except Exception:
            logger.exception("contract endpoint %s failed", path)
            self._send_json(500, {"error": "internal error"})
            return
        self._send_json(200, body)

    def _send_json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Health polls arrive every few seconds; route them to DEBUG
        # instead of BaseHTTPRequestHandler's stderr spam.
        logger.debug("contract server: " + format, *args)


class _ContractHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    routes: dict[str, Callable[[], Any]]


class ContractServer:
    """Serve the §03 contract endpoints on a background daemon thread.

    stdlib-only by design (``http.server``) — the contract surface must
    not drag a web framework into every 60-line detector. Three GETs a
    few times a second is comfortably inside ``ThreadingHTTPServer``
    territory.
    """

    def __init__(
        self,
        *,
        health: Callable[[], dict[str, Any]],
        manifest: Callable[[], dict[str, Any]],
        state: Callable[[], dict[str, Any]],
        host: str = "0.0.0.0",
        port: int = 0,
    ) -> None:
        self._host = host
        self._requested_port = int(port)
        self._routes = {"/health": health, "/manifest": manifest, "/state": state}
        self._server: _ContractHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """The actually-bound port (resolves ``port=0`` ephemerals)."""
        if self._server is None:
            return self._requested_port
        return self._server.server_address[1]

    def start(self) -> "ContractServer":
        if self._server is not None:
            return self
        server = _ContractHTTPServer(
            (self._host, self._requested_port), _ContractRequestHandler
        )
        server.routes = self._routes
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="opennvr-app-contract",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3.0)


# ── The base-class glue ────────────────────────────────────────────


class ContractMixin:
    """Counters + contract lifecycle shared by ``Detector`` and
    ``FrameApp``. The bases call :meth:`_contract_init` at
    construction, :meth:`_contract_note_event` /
    :meth:`_contract_note_alerts` from their loops, and bracket their
    ``run()`` with :meth:`start_contract_server` /
    :meth:`register_with_opennvr` / :meth:`stop_contract_server`.
    """

    # Set by the concrete bases.
    cfg: Any
    manifest: Any

    def _contract_init(self) -> None:
        self._started_monotonic = _monotonic()
        self._events_seen = 0
        self._alerts_fired = 0
        self._last_event_monotonic: float | None = None
        self._contract_server: ContractServer | None = None

    def _contract_note_event(self) -> None:
        self._events_seen += 1
        self._last_event_monotonic = _monotonic()

    def _contract_note_alerts(self, count: int) -> None:
        if count > 0:
            self._alerts_fired += count

    # ── App surface ────────────────────────────────────────────────

    def state_snapshot(self) -> dict[str, Any]:
        """Override to expose live standing state (active tracks, zone
        levels, running counters) via ``GET /state``. Must return a
        JSON-serializable dict; called from the contract server's
        thread, so keep it a cheap read of existing state."""
        return {}

    def health_snapshot(self) -> dict[str, Any]:
        """The ``GET /health`` payload (spec §03)."""
        now = _monotonic()
        last_age = (
            None
            if self._last_event_monotonic is None
            else round(now - self._last_event_monotonic, 3)
        )
        return {
            "ready": True,
            "uptime_s": round(now - self._started_monotonic, 3),
            "events_seen": self._events_seen,
            "alerts_fired": self._alerts_fired,
            "last_event_age_s": last_age,
        }

    def manifest_snapshot(self) -> dict[str, Any]:
        """The ``GET /manifest`` payload — ``{}`` for manifest-less
        apps (mid-migration) rather than a 500."""
        return self.manifest.to_dict() if self.manifest is not None else {}

    # ── Lifecycle ──────────────────────────────────────────────────

    def start_contract_server(self) -> ContractServer | None:
        """Start the contract server iff ``cfg.contract_port`` is set.
        Idempotent; returns the running server (or ``None`` when the
        contract surface is not configured)."""
        if self._contract_server is not None:
            return self._contract_server
        port = getattr(self.cfg, "contract_port", None)
        if port is None:
            return None
        bind_host = getattr(self.cfg, "contract_bind_host", None) or "0.0.0.0"
        server = ContractServer(
            health=self.health_snapshot,
            manifest=self.manifest_snapshot,
            state=self.state_snapshot,
            host=bind_host,
            port=int(port),
        )
        server.start()
        self._contract_server = server
        logger.info(
            "contract server listening on %s:%d (/health /manifest /state)",
            bind_host,
            server.port,
        )
        return server

    def stop_contract_server(self) -> None:
        server = self._contract_server
        self._contract_server = None
        if server is not None:
            server.stop()

    def register_with_opennvr(self) -> bool:
        """POST this app's URL + manifest to the OpenNVR app registry.

        Best-effort by contract: every failure path (no registry
        configured, registry down, 4xx/5xx) logs and returns ``False``
        — self-registration must never take the app down. Returns
        ``True`` only on a 2xx from the registry."""
        opennvr_url = getattr(self.cfg, "opennvr_url", None)
        if not opennvr_url:
            return False

        port = getattr(self.cfg, "contract_port", None)
        if self._contract_server is not None:
            # Resolves contract_port=0 to the actually-bound port.
            port = self._contract_server.port
        if port is None:
            logger.warning(
                "self-registration skipped: 'opennvr_url' is set but "
                "'contract_port' is not — the registry needs a contract "
                "endpoint to poll"
            )
            return False

        host = getattr(self.cfg, "contract_host", None) or socket.gethostname()
        payload = {
            "url": f"http://{host}:{int(port)}",
            "manifest": self.manifest_snapshot(),
        }
        headers: dict[str, str] = {}
        token = getattr(self.cfg, "opennvr_token", None) or os.environ.get(
            "OPENNVR_INTERNAL_API_KEY"
        )
        if token:
            # Both header shapes: the registry's register route accepts
            # a user JWT (Authorization: Bearer) or the deployment's
            # INTERNAL_API_KEY (X-Internal-Api-Key) — sending both lets
            # one config key work against either credential kind.
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Internal-Api-Key"] = str(token)
        endpoint = f"{str(opennvr_url).rstrip('/')}/api/v1/apps/register"
        try:
            response = httpx.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=REGISTER_TIMEOUT_SECONDS,
                trust_env=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "app self-registration failed (%s): %s — continuing "
                "without the registry",
                endpoint,
                exc,
            )
            return False
        if response.status_code >= 400:
            logger.warning(
                "app self-registration rejected (%s): HTTP %d: %s",
                endpoint,
                response.status_code,
                response.text[:200],
            )
            return False
        logger.info(
            "registered with OpenNVR app registry: %s as %s",
            endpoint,
            payload["url"],
        )
        return True


__all__ = ["ContractServer", "ContractMixin", "REGISTER_TIMEOUT_SECONDS"]

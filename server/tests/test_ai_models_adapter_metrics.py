# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""
Tests for ``GET /ai-models/adapters/{adapter_name}/metrics`` — the
backend proxy onto KAI-C's per-adapter metrics rollup (observability
spec §05).

Run with:

    cd server && pytest tests/test_ai_models_adapter_metrics.py -v

Coverage:

* Happy path: the KAI-C rollup JSON is proxied through verbatim.
* Unknown adapter: KAI-C's 404 maps to a backend 404.
* KAI-C down (connect error) or misbehaving (5xx): graceful 502, never
  a raw exception / 500.
* The service method sends the ``X-Internal-Api-Key`` header KAI-C's
  governed surface requires.
"""

from __future__ import annotations

# Python 3.10 sandbox polyfill — pyproject requires 3.11+ where
# datetime.UTC exists. No-op on 3.11+.
import datetime as _dt

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # noqa: UP017 — only runs where UTC is absent

import os
import secrets
import sys
import types as _types
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

# Settings need to be valid for the modules under test to import.
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("MEDIAMTX_SECRET", secrets.token_hex(32))
os.environ.setdefault("INTERNAL_API_KEY", secrets.token_urlsafe(48))
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode()
)

# Stub ``core.logging_config`` — same pattern as test_apps_registry.py.
# The real module wants a writable ``logs/`` directory; the router
# under test only needs no-op loggers.
_lm = _types.ModuleType("core.logging_config")


class _L:
    def info(self, *a, **kw):
        pass

    def warning(self, *a, **kw):
        pass

    def error(self, *a, **kw):
        pass

    def debug(self, *a, **kw):
        pass

    def critical(self, *a, **kw):
        pass


for _name in (
    "main_logger",
    "auth_logger",
    "camera_logger",
    "recording_logger",
    "cloud_logger",
    "ai_logger",
):
    setattr(_lm, _name, _L())
sys.modules["core.logging_config"] = _lm


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.auth import get_current_active_user  # noqa: E402
from core.database import get_db  # noqa: E402
from routers import ai_models as ai_models_router  # noqa: E402

ROLLUP = {
    "adapter": "yolov8",
    "window_s": 3600,
    "latency_ms": {"p50": 18.0, "p95": 47.0, "p99": 71.0},
    "outcomes": {"ok": 96, "model_error": 3, "transport_error": 1},
    "inflight": 7,
    "max_inflight": 8,
    "queue_depth": 0,
    "fingerprint_changes": ["2026-06-30T09:00:00+00:00"],
    "samples": 60,
}


class _StubUser:
    id = 1
    username = "tester"


class _StubKaiCService:
    """Stands in for KaiCService — only get_adapter_metrics is used."""

    def __init__(self, *, response: dict | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.requested: list[str] = []

    async def get_adapter_metrics(self, adapter_name: str) -> dict:
        self.requested.append(adapter_name)
        if self._error is not None:
            raise self._error
        return self._response or {}


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET", f"http://localhost:8100/api/v1/adapters/yolov8/metrics"
    )
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"KAI-C returned {status_code}", request=request, response=response
    )


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient over the real ai-models router with the
    KAI-C service stubbed. Auth + DB are overridden — RBAC is
    exercised by the shared auth tests, not re-proven per router."""

    def _make(service: _StubKaiCService) -> TestClient:
        monkeypatch.setattr(
            ai_models_router, "get_kai_c_service", lambda: service
        )
        app = FastAPI()
        app.include_router(ai_models_router.router)
        app.dependency_overrides[get_db] = lambda: iter([None])
        app.dependency_overrides[get_current_active_user] = lambda: _StubUser()
        return TestClient(app)

    return _make


# ─── GET /ai-models/adapters/{adapter_name}/metrics ─────────────────────


def test_metrics_proxied_through_verbatim(make_client):
    service = _StubKaiCService(response=ROLLUP)
    client = make_client(service)
    resp = client.get("/ai-models/adapters/yolov8/metrics")
    assert resp.status_code == 200
    assert resp.json() == ROLLUP
    assert service.requested == ["yolov8"]


def test_unknown_adapter_maps_kai_c_404_to_404(make_client):
    client = make_client(_StubKaiCService(error=_http_status_error(404)))
    resp = client.get("/ai-models/adapters/ghost/metrics")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_kai_c_down_is_graceful_502(make_client):
    client = make_client(
        _StubKaiCService(error=httpx.ConnectError("connection refused"))
    )
    resp = client.get("/ai-models/adapters/yolov8/metrics")
    assert resp.status_code == 502
    assert "KAI-C" in resp.json()["detail"]


def test_kai_c_5xx_is_graceful_502(make_client):
    client = make_client(_StubKaiCService(error=_http_status_error(500)))
    resp = client.get("/ai-models/adapters/yolov8/metrics")
    assert resp.status_code == 502


# ─── KaiCService.get_adapter_metrics (the HTTP half) ────────────────────


@pytest.mark.anyio
async def test_service_sends_internal_api_key_header(monkeypatch):
    """The governed /api/v1 surface requires X-Internal-Api-Key — the
    service must attach it (same secret the /infer path uses)."""
    from services.kai_c_service import KaiCService

    seen: dict = {}

    async def _respond(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("x-internal-api-key")
        return httpx.Response(200, json=ROLLUP)

    service = KaiCService.__new__(KaiCService)  # skip heavyweight __init__
    service.kai_c_url = "http://localhost:8100"
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_respond)
    )
    monkeypatch.setattr(
        KaiCService, "_internal_api_key", lambda self: "secret-key"
    )

    body = await service.get_adapter_metrics("yolov8")
    await service.http_client.aclose()

    assert body == ROLLUP
    assert seen["path"] == "/api/v1/adapters/yolov8/metrics"
    assert seen["key"] == "secret-key"


@pytest.fixture
def anyio_backend():
    return "asyncio"

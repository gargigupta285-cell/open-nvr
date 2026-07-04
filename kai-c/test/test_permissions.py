# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adapter permission-approval tests (§8 / §11 — A2.4b operator-UI flow).

Covers:
  * ``permission_keys`` helper derivation.
  * pending-on-register (with declared perms) vs auto-approve (with none).
  * serving refused while pending; allowed after approve.
  * grant / revoke / approve-all transitions, audit emission + grant_id.
  * drift → moves to pending (not de-registered).
  * HTTP endpoint auth (401 without key) + the GET permissions JSON shape.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

from kai_c.audit import AuditStore
from kai_c.contract_types import Permissions
from kai_c.registry import AdapterRegistry, permission_keys


# ── permission_keys helper ─────────────────────────────────────────


def test_permission_keys_empty():
    assert permission_keys(Permissions()) == []


def test_permission_keys_flags_and_lists():
    perms = Permissions(
        gpu=True,
        host_metadata=True,
        network_egress=["b.example.com", "a.example.com"],
        host_filesystem=["/var/data"],
        shared_memory_paths=["/dev/shm/frames"],
    )
    keys = permission_keys(perms)
    # gpu + host_metadata first, then sorted per-host / per-path keys.
    assert keys == [
        "gpu",
        "host_metadata",
        "network_egress:a.example.com",
        "network_egress:b.example.com",
        "host_filesystem:/var/data",
        "shared_memory_paths:/dev/shm/frames",
    ]


def test_permission_keys_dedupes_hosts():
    perms = Permissions(network_egress=["a.example.com", "a.example.com"])
    assert permission_keys(perms) == ["network_egress:a.example.com"]


# ── Registry fixtures (mirror test_registry.py) ─────────────────────


def _base_caps(*, gpu: bool = False, egress: list[str] | None = None) -> dict:
    return {
        "adapter": {
            "name": "test-adapter", "version": "1.0.0", "vendor": "open-nvr",
            "license": "AGPL-3.0", "supported_contract_versions": ["1"],
        },
        "model": {"name": "m1", "version": "v1", "framework": "f", "fingerprint": "sha256:aaa"},
        "endpoints": {
            "infer": {"supported": True, "input_content_types": ["application/json"]},
            "infer_stream": {"supported": False},
        },
        "tasks_advertised": ["echo"],
        "permissions": {
            "gpu": gpu, "network_egress": egress or [],
            "host_filesystem": [], "shared_memory_paths": [], "host_metadata": False,
        },
        "scheduling": {"max_inflight": 1, "preferred_batch_size": 1, "fair_queuing": "none"},
        "cost": {"currency": "USD", "estimated_per_call": 0.0, "estimated_per_hour": 0.0,
                 "rate_limit_per_minute": None, "is_metered": False},
    }


class _StubAdapter:
    def __init__(self, url: str, capabilities: dict) -> None:
        self.url = url
        self._caps = capabilities

    def update_capabilities(self, capabilities: dict) -> None:
        self._caps = capabilities

    async def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/capabilities":
            return httpx.Response(200, json=self._caps)
        if path == "/health":
            return httpx.Response(200, json={
                "status": "ok", "adapter_name": "test-adapter",
                "adapter_version": "1.0.0", "model_name": "m1",
                "model_version": "v1", "started_at": "2026-05-19T00:00:00Z",
                "uptime_seconds": 1,
            })
        if path == "/infer":
            return httpx.Response(200, json={"status": "ok", "result": {"ok": True}})
        return httpx.Response(404)


@pytest.fixture
def audit(tmp_path: Path) -> AuditStore:
    return AuditStore(path=str(tmp_path / "audit.jsonl"))


def _make_registry(audit, stub):
    transport = httpx.MockTransport(stub.respond)
    client = httpx.AsyncClient(transport=transport)
    return AdapterRegistry(
        sovereignty_mode="local_only", audit=audit, http_client=client,
        poll_interval_seconds=999,
    )


# ── pending-on-register / auto-approve ──────────────────────────────


@pytest.mark.asyncio
async def test_register_with_perms_starts_pending(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        adapter = await reg.register("yolo", stub.url)
        assert adapter.approval_status == "pending"
        assert adapter.granted_permissions == set()
        assert adapter.pending_keys() == ["gpu"]
        assert not adapter.is_serving_allowed
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_register_without_perms_auto_approved(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps())
    reg = _make_registry(audit, stub)
    try:
        adapter = await reg.register("yolo", stub.url)
        assert adapter.approval_status == "approved"
        assert adapter.is_serving_allowed
    finally:
        await reg.aclose()


# ── serving gate ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serving_refused_while_pending(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        with pytest.raises(PermissionError):
            await reg.proxy_infer("yolo", {"x": 1}, "corr-1")
        rows = audit.read_all()
        refused = [r for r in rows if r["type"] == "inference.refused_permission"]
        assert refused and refused[-1]["pending_permissions"] == ["gpu"]
        assert refused[-1]["correlation_id"] == "corr-1"
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_serving_allowed_after_approve(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        reg.approve_all("yolo", actor="alice")
        status_code, body = await reg.proxy_infer("yolo", {"x": 1}, "corr-2")
        assert status_code == 200
        assert body["result"]["ok"] is True
    finally:
        await reg.aclose()


# ── grant / revoke / approve-all transitions ────────────────────────


@pytest.mark.asyncio
async def test_grant_partial_then_full(audit):
    caps = _base_caps(gpu=True)
    caps["permissions"]["host_metadata"] = True
    stub = _StubAdapter("http://127.0.0.1:9100", caps)
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        adapter, grant_id = reg.grant_permissions("yolo", ["gpu"], actor="alice")
        assert grant_id  # uuid hex
        assert adapter.approval_status == "pending"  # host_metadata still pending
        assert adapter.pending_keys() == ["host_metadata"]

        adapter, _ = reg.grant_permissions("yolo", ["host_metadata"], actor="alice")
        assert adapter.approval_status == "approved"

        rows = audit.read_all()
        grants = [r for r in rows if r["type"] == "adapter.permission_granted"]
        assert len(grants) == 2
        assert all("adapter_grant_id" in r and r["actor"] == "alice" for r in grants)
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_grant_ignores_undeclared_keys(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        # host_metadata was never declared → granting it is a no-op.
        adapter, _ = reg.grant_permissions("yolo", ["host_metadata"], actor="alice")
        assert adapter.approval_status == "pending"
        assert "host_metadata" not in adapter.granted_permissions
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_revoke_flips_back_to_pending(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        reg.approve_all("yolo", actor="alice")
        assert reg.get("yolo").approval_status == "approved"

        adapter, grant_id = reg.revoke_permissions("yolo", ["gpu"], actor="bob")
        assert grant_id
        assert adapter.approval_status == "pending"
        assert not adapter.is_serving_allowed
        # Serving now refused again.
        with pytest.raises(PermissionError):
            await reg.proxy_infer("yolo", {"x": 1}, "corr-3")

        rows = audit.read_all()
        revokes = [r for r in rows if r["type"] == "adapter.permission_revoked"]
        assert revokes and revokes[-1]["actor"] == "bob"
        assert "adapter_grant_id" in revokes[-1]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_grant_revoke_approve_unknown_adapter_raises(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps())
    reg = _make_registry(audit, stub)
    try:
        with pytest.raises(KeyError):
            reg.grant_permissions("ghost", ["gpu"], actor="alice")
        with pytest.raises(KeyError):
            reg.revoke_permissions("ghost", ["gpu"], actor="alice")
        with pytest.raises(KeyError):
            reg.approve_all("ghost", actor="alice")
        assert reg.permissions_view("ghost") is None
    finally:
        await reg.aclose()


# ── drift → pending (not de-registered) ─────────────────────────────


@pytest.mark.asyncio
async def test_drift_new_permission_moves_to_pending(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        reg.approve_all("yolo", actor="alice")
        assert reg.get("yolo").approval_status == "approved"

        # Adapter re-declares an ADDITIONAL scope (host_metadata) on the
        # next poll. It must stay registered but flip to pending, keeping
        # the already-granted gpu grant.
        caps2 = _base_caps(gpu=True)
        caps2["permissions"]["host_metadata"] = True
        stub.update_capabilities(caps2)
        await reg.refresh("yolo")

        adapter = reg.get("yolo")
        assert adapter is not None
        assert adapter.approval_status == "pending"
        assert adapter.pending_keys() == ["host_metadata"]
        assert "gpu" in adapter.granted_permissions  # prior grant preserved
    finally:
        await reg.aclose()


# ── permissions_view shape ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_view_shape(audit):
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("yolo", stub.url)
        reg.grant_permissions("yolo", ["gpu"], actor="alice")
        view = reg.permissions_view("yolo")
        assert view["adapter"] == "yolo"
        assert view["approval_status"] == "approved"
        assert view["granted"] == ["gpu"]
        assert view["pending"] == []
        assert view["declared"] == [
            {"key": "gpu", "label": "GPU access", "kind": "gpu",
             "sovereignty_conflict": False},
        ]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_permissions_view_sovereignty_conflict(audit):
    # An adapter with egress can't be REGISTERED under local_only
    # (sovereignty refuses), so use federated mode to exercise the
    # per-key sovereignty_conflict flag being False under a non-local
    # mode, and local_only flagging network_egress keys as a conflict.
    from kai_c.registry import permission_sovereignty_conflict

    assert permission_sovereignty_conflict("network_egress:x.com", "local_only") is True
    assert permission_sovereignty_conflict("network_egress:x.com", "federated") is False
    assert permission_sovereignty_conflict("gpu", "local_only") is False


# ── HTTP endpoints (auth + JSON shape) ──────────────────────────────


@pytest.fixture
def kaic_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """FastAPI app with a stub adapter registered and INTERNAL_API_KEY
    set, so we can prove the endpoints 401 without the header."""
    monkeypatch.setenv("AI_SOVEREIGNTY", "local_only")
    monkeypatch.setenv("ADAPTER_URL", "http://127.0.0.1:9100")
    monkeypatch.setenv("KAI_C_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("INTERNAL_API_KEY", "sekret")

    if "main" in sys.modules:
        importlib.reload(sys.modules["main"])
    import main as kaic_main

    caps = _base_caps(gpu=True)
    stub = _StubAdapter("http://127.0.0.1:9100", caps)
    transport = httpx.MockTransport(stub.respond)

    from fastapi.testclient import TestClient

    original_init = kaic_main.AdapterRegistry.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(kaic_main.AdapterRegistry, "__init__", patched_init)

    with TestClient(kaic_main.app) as client:
        # startup registers "default" → 127.0.0.1:9100 (mocked) as pending.
        yield client


_AUTH = {"X-Internal-Api-Key": "sekret"}


def test_endpoints_401_without_key(kaic_client):
    assert kaic_client.get(
        "/api/v1/adapters/default/permissions"
    ).status_code == 401
    for url in [
        "/api/v1/adapters/default/permissions/grant",
        "/api/v1/adapters/default/permissions/revoke",
        "/api/v1/adapters/default/permissions/approve-all",
    ]:
        resp = kaic_client.post(url, json={"keys": []})
        assert resp.status_code == 401, (url, resp.text)


def test_get_permissions_json_shape(kaic_client):
    resp = kaic_client.get("/api/v1/adapters/default/permissions", headers=_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "adapter": "default",
        "approval_status": "pending",
        "declared": [
            {"key": "gpu", "label": "GPU access", "kind": "gpu",
             "sovereignty_conflict": False},
        ],
        "granted": [],
        "pending": ["gpu"],
    }


def test_grant_then_approve_all_via_http(kaic_client):
    grant = kaic_client.post(
        "/api/v1/adapters/default/permissions/grant",
        json={"keys": ["gpu"]}, headers=_AUTH,
    )
    assert grant.status_code == 200, grant.text
    body = grant.json()
    assert body["approval_status"] == "approved"
    assert body["granted"] == ["gpu"]
    assert "grant_id" in body

    approve = kaic_client.post(
        "/api/v1/adapters/default/permissions/approve-all", headers=_AUTH,
    )
    assert approve.status_code == 200
    assert approve.json()["approval_status"] == "approved"


def test_permissions_unknown_adapter_404(kaic_client):
    resp = kaic_client.get("/api/v1/adapters/ghost/permissions", headers=_AUTH)
    assert resp.status_code == 404


def test_serving_refused_via_http_while_pending(kaic_client):
    # default is pending (declares gpu, nothing granted) → /infer 403.
    resp = kaic_client.post(
        "/api/v1/infer/default", json={"camera_id": "cam-1"}, headers=_AUTH,
    )
    assert resp.status_code == 403, resp.text
    # After approve-all, serving is allowed.
    kaic_client.post("/api/v1/adapters/default/permissions/approve-all", headers=_AUTH)
    resp2 = kaic_client.post(
        "/api/v1/infer/default", json={"camera_id": "cam-1"}, headers=_AUTH,
    )
    assert resp2.status_code == 200, resp2.text


# ── WS streaming gate (§8 — fail closed on the stream path too) ─────


def test_stream_refused_while_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A pending adapter must not stream either: the WS upgrade is
    closed with policy_refused (4001) and the refusal is audited with
    inference.refused_permission — same event type as the HTTP path."""
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("AI_SOVEREIGNTY", "local_only")
    monkeypatch.setenv("ADAPTER_URL", "http://127.0.0.1:9100")
    monkeypatch.setenv("KAI_C_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("INTERNAL_API_KEY", "sekret")

    if "main" in sys.modules:
        importlib.reload(sys.modules["main"])
    import main as kaic_main

    # gpu → pending; infer_stream supported so the approval gate (not
    # the capability check) is what refuses the upgrade.
    caps = _base_caps(gpu=True)
    caps["endpoints"]["infer_stream"] = {"supported": True}
    stub = _StubAdapter("http://127.0.0.1:9100", caps)
    transport = httpx.MockTransport(stub.respond)

    original_init = kaic_main.AdapterRegistry.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(kaic_main.AdapterRegistry, "__init__", patched_init)

    from fastapi.testclient import TestClient

    with TestClient(kaic_main.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/v1/infer/default/stream",
                headers={"X-Internal-Api-Key": "sekret"},
            ) as ws:
                ws.receive_json()
        assert exc_info.value.code == 4001  # CLOSE_POLICY_REFUSED

        # The refusal landed in the audit trail.
        audit = client.get(
            "/api/v1/audit?event_type=inference.refused_permission",
            headers={"X-Internal-Api-Key": "sekret"},
        ).json()
        assert audit["events"], "expected inference.refused_permission audit event"
        assert audit["events"][-1]["adapter"] == "default"
        assert "gpu" in audit["events"][-1]["pending_permissions"]


# ── Drift: permission REMOVAL prunes the stale grant ─────────────────


@pytest.mark.asyncio
async def test_drift_removed_permission_prunes_grant(audit):
    """§11.3: removing a permission is allowed — but the grant for the
    removed key must be dropped (with an audited system revocation), so
    a later re-add of the same key goes back through operator approval
    instead of silently inheriting the old grant."""
    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("gpu-x", stub.url)
        reg.approve_all("gpu-x", "alice")
        adapter = reg.get("gpu-x")
        assert adapter.granted_permissions == {"gpu"}

        # Adapter narrows scope: drops the gpu declaration.
        stub.update_capabilities(_base_caps(gpu=False))
        await reg.refresh("gpu-x")

        adapter = reg.get("gpu-x")
        assert adapter is not None
        assert adapter.granted_permissions == set()  # stale grant pruned
        assert adapter.approval_status == "approved"  # nothing declared → approved

        # The system revocation is in the audit trail.
        rows = audit.read_all()
        revoked = [r for r in rows if r["type"] == "adapter.permission_revoked"]
        assert revoked
        assert revoked[-1]["keys"] == ["gpu"]
        assert revoked[-1]["actor"] == "system:permission_no_longer_declared"
        assert revoked[-1]["adapter_grant_id"]

        # Re-adding the key later requires FRESH approval.
        stub.update_capabilities(_base_caps(gpu=True))
        await reg.refresh("gpu-x")
        adapter = reg.get("gpu-x")
        assert adapter.approval_status == "pending"
        assert adapter.pending_keys() == ["gpu"]
    finally:
        await reg.aclose()


# ── legacy /infer + /infer/local fail-closed gate ───────────────────
# The legacy passthroughs resolve adapters via the static ADAPTER_REGISTRY
# dict and bypass registry.proxy_infer's gate. enforce_legacy_serving_gate
# closes that hole: a pending adapter must not serve by ANY path.


@pytest.mark.asyncio
async def test_legacy_gate_refuses_pending_adapter(audit, monkeypatch):
    import main as kaic_main

    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("default", stub.url)  # pending (declares gpu)
        monkeypatch.setattr(kaic_main, "_registry", reg)
        monkeypatch.setattr(kaic_main, "_audit", audit)
        with pytest.raises(kaic_main.HTTPException) as exc:
            kaic_main.enforce_legacy_serving_gate("default")
        assert exc.value.status_code == 403
        refused = [r for r in audit.read_all() if r["type"] == "inference.refused_permission"]
        assert refused and refused[-1]["pending_permissions"] == ["gpu"]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_legacy_gate_allows_after_approval(audit, monkeypatch):
    import main as kaic_main

    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        await reg.register("default", stub.url)
        reg.approve_all("default", actor="op")
        monkeypatch.setattr(kaic_main, "_registry", reg)
        monkeypatch.setattr(kaic_main, "_audit", audit)
        # Approved → no raise.
        kaic_main.enforce_legacy_serving_gate("default")
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_legacy_gate_escape_hatch_for_unregistered(audit, monkeypatch):
    import main as kaic_main

    stub = _StubAdapter("http://127.0.0.1:9100", _base_caps(gpu=True))
    reg = _make_registry(audit, stub)
    try:
        monkeypatch.setattr(kaic_main, "_registry", reg)
        # Adapter never registered with the v2 registry → the legacy escape
        # hatch is preserved (no raise); only KNOWN-pending adapters refuse.
        kaic_main.enforce_legacy_serving_gate("never-registered")
    finally:
        await reg.aclose()

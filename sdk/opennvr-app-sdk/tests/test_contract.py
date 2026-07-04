# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""Contract surface tests (spec §03) — the /health /manifest /state
server, the loop-fed counters, and best-effort self-registration
against the OpenNVR app registry. Real HTTP against an ephemeral
127.0.0.1 port; the registry side is a monkeypatched ``httpx.post``."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from opennvr_app_sdk import (
    Alert,
    AlertDispatcher,
    AppManifest,
    Detector,
    FrameApp,
)
from opennvr_app_sdk import contract as contract_mod


class _RecorderChannel:
    name = "recorder"

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.alerts.append(alert)
        return True


MANIFEST = AppManifest(
    id="contract-echo",
    name="Contract Echo",
    version="1.2.3",
    category="test",
    summary="Fires one alert per detection batch.",
)


class _EchoDetector(Detector):
    manifest = MANIFEST

    def on_detections(self, camera_id, detections, event):
        if detections:
            yield Alert(title="hit", description="d", camera_id=camera_id)

    def state_snapshot(self):
        return {"note": "live state"}


def _cfg(**overrides) -> SimpleNamespace:
    base = {"contract_port": 0, "contract_bind_host": "127.0.0.1"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _detector(cfg=None) -> _EchoDetector:
    return _EchoDetector(cfg or _cfg(), AlertDispatcher([_RecorderChannel()]))


def _event(n: int = 1) -> dict:
    return {
        "camera_id": "cam-1",
        "result": {"detections": [{"label": "person"}] * n},
    }


def _get(port: int, path: str) -> httpx.Response:
    return httpx.get(f"http://127.0.0.1:{port}{path}", timeout=2.0, trust_env=False)


# ── The three endpoints ────────────────────────────────────────────


def test_serves_health_manifest_and_state():
    det = _detector()
    server = det.start_contract_server()
    assert server is not None
    try:
        health = _get(server.port, "/health").json()
        assert health["ready"] is True
        assert health["uptime_s"] >= 0
        assert health["events_seen"] == 0
        assert health["alerts_fired"] == 0
        assert health["last_event_age_s"] is None

        manifest = _get(server.port, "/manifest").json()
        assert manifest == MANIFEST.to_dict()

        state = _get(server.port, "/state").json()
        assert state == {"note": "live state"}
    finally:
        det.stop_contract_server()


def test_health_counters_follow_the_event_loop():
    det = _detector()
    server = det.start_contract_server()
    try:
        det.handle_event(_event(2))           # 1 event, 1 alert
        det.handle_event({"camera_id": "cam-1", "result": {"detections": []}})
        det.handle_event("not-a-dict")        # malformed still counts as seen

        health = _get(server.port, "/health").json()
        assert health["events_seen"] == 3
        assert health["alerts_fired"] == 1
        assert health["last_event_age_s"] is not None
        assert health["last_event_age_s"] >= 0
    finally:
        det.stop_contract_server()


def test_default_state_snapshot_is_empty_dict():
    class _Bare(Detector):
        manifest = MANIFEST

        def on_detections(self, camera_id, detections, event):
            return None

    det = _Bare(_cfg(), AlertDispatcher([_RecorderChannel()]))
    server = det.start_contract_server()
    try:
        assert _get(server.port, "/state").json() == {}
    finally:
        det.stop_contract_server()


def test_unknown_path_is_json_404():
    det = _detector()
    server = det.start_contract_server()
    try:
        response = _get(server.port, "/nope")
        assert response.status_code == 404
        assert "unknown path" in response.json()["error"]
    finally:
        det.stop_contract_server()


def test_server_off_without_contract_port():
    det = _detector(SimpleNamespace())
    assert det.start_contract_server() is None
    det.stop_contract_server()  # must be a safe no-op


def test_frame_app_counters_follow_handle_tick():
    class _App(FrameApp):
        manifest = MANIFEST

        def on_frame(self, camera_id, frame_bytes):
            yield Alert(title="t", description="d", camera_id=camera_id)

    class _Source:
        def get_frame(self, camera_id):
            return b"jpeg" if camera_id == "cam-1" else None

    app_obj = _App(
        SimpleNamespace(poll_interval_seconds=0.01),
        AlertDispatcher([_RecorderChannel()]),
        frame_source=_Source(),
        cameras=["cam-1", "cam-2"],
    )
    app_obj.handle_tick()
    # cam-2 produced no frame → 1 event; cam-1's rule fired 1 alert.
    assert app_obj._events_seen == 1
    assert app_obj._alerts_fired == 1


# ── Self-registration ──────────────────────────────────────────────


class _FakePost:
    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None):
        self.calls: list[dict] = []
        self._status = status_code
        self._raise = raise_exc

    def __call__(self, url, *, json=None, headers=None, timeout=None, trust_env=None):
        self.calls.append(
            {"url": url, "json": json, "headers": headers or {}, "timeout": timeout}
        )
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(status_code=self._status, text="registered")


def test_registration_posts_url_manifest_and_auth_headers(monkeypatch):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    det = _detector(_cfg(
        contract_port=9200,
        contract_host="loitering",
        opennvr_url="http://opennvr:8080/",
        opennvr_token="sekrit",
    ))
    assert det.register_with_opennvr() is True

    call = fake.calls[0]
    assert call["url"] == "http://opennvr:8080/api/v1/apps/register"
    assert call["json"] == {
        "url": "http://loitering:9200",
        "manifest": MANIFEST.to_dict(),
    }
    # One token, both header shapes: the registry's register route
    # accepts a user JWT (bearer) or the deployment's INTERNAL_API_KEY
    # (X-Internal-Api-Key) — the SDK can't know which kind it holds.
    assert call["headers"]["Authorization"] == "Bearer sekrit"
    assert call["headers"]["X-Internal-Api-Key"] == "sekrit"


def test_registration_token_falls_back_to_env(monkeypatch):
    """No ``opennvr_token`` in the config ⇒ the token comes from the
    ``OPENNVR_INTERNAL_API_KEY`` environment variable (the compose
    overlay's wiring)."""
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    monkeypatch.setenv("OPENNVR_INTERNAL_API_KEY", "env-sekrit")
    det = _detector(_cfg(
        contract_port=9200,
        contract_host="loitering",
        opennvr_url="http://opennvr:8080",
    ))
    assert det.register_with_opennvr() is True
    headers = fake.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer env-sekrit"
    assert headers["X-Internal-Api-Key"] == "env-sekrit"


def test_registration_cfg_token_beats_env(monkeypatch):
    """An explicit config token wins over the env fallback."""
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    monkeypatch.setenv("OPENNVR_INTERNAL_API_KEY", "env-sekrit")
    det = _detector(_cfg(
        contract_port=9200,
        contract_host="loitering",
        opennvr_url="http://opennvr:8080",
        opennvr_token="cfg-sekrit",
    ))
    assert det.register_with_opennvr() is True
    assert fake.calls[0]["headers"]["X-Internal-Api-Key"] == "cfg-sekrit"


def test_registration_no_token_sends_no_auth_headers(monkeypatch):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    monkeypatch.delenv("OPENNVR_INTERNAL_API_KEY", raising=False)
    det = _detector(_cfg(
        contract_port=9200,
        contract_host="loitering",
        opennvr_url="http://opennvr:8080",
    ))
    assert det.register_with_opennvr() is True
    headers = fake.calls[0]["headers"]
    assert "Authorization" not in headers
    assert "X-Internal-Api-Key" not in headers


def test_registration_advertises_actual_ephemeral_port(monkeypatch):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    det = _detector(_cfg(
        contract_host="myapp", opennvr_url="http://opennvr:8080",
    ))
    server = det.start_contract_server()
    try:
        assert det.register_with_opennvr() is True
        assert server.port != 0
        assert fake.calls[0]["json"]["url"] == f"http://myapp:{server.port}"
    finally:
        det.stop_contract_server()


def test_registration_failure_is_nonfatal(monkeypatch, caplog):
    fake = _FakePost(raise_exc=RuntimeError("registry down"))
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    det = _detector(_cfg(contract_port=9200, opennvr_url="http://opennvr:8080"))
    with caplog.at_level("WARNING", logger="opennvr_app_sdk.contract"):
        assert det.register_with_opennvr() is False
    assert any("self-registration failed" in r.getMessage() for r in caplog.records)


def test_registration_rejection_is_nonfatal(monkeypatch, caplog):
    fake = _FakePost(status_code=400)
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    det = _detector(_cfg(contract_port=9200, opennvr_url="http://opennvr:8080"))
    with caplog.at_level("WARNING", logger="opennvr_app_sdk.contract"):
        assert det.register_with_opennvr() is False
    assert any("rejected" in r.getMessage() for r in caplog.records)


def test_registration_skipped_without_opennvr_url(monkeypatch):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    assert _detector(_cfg(contract_port=9200)).register_with_opennvr() is False
    assert fake.calls == []


def test_registration_needs_a_contract_port(monkeypatch, caplog):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)
    det = _detector(SimpleNamespace(opennvr_url="http://opennvr:8080"))
    with caplog.at_level("WARNING", logger="opennvr_app_sdk.contract"):
        assert det.register_with_opennvr() is False
    assert fake.calls == []
    assert any("contract_port" in r.getMessage() for r in caplog.records)


# ── run() lifecycle ────────────────────────────────────────────────


async def test_frame_app_run_starts_registers_and_stops_contract(monkeypatch):
    fake = _FakePost()
    monkeypatch.setattr(contract_mod.httpx, "post", fake)

    seen_ports: list[int] = []

    class _App(FrameApp):
        manifest = MANIFEST

        def on_frame(self, camera_id, frame_bytes):
            # Prove the contract server is live DURING the loop.
            seen_ports.append(self._contract_server.port)
            health = _get(self._contract_server.port, "/health").json()
            assert health["ready"] is True
            return None

    class _Source:
        def get_frame(self, camera_id):
            return b"jpeg"

    app_obj = _App(
        SimpleNamespace(
            poll_interval_seconds=0.01,
            contract_port=0,
            contract_bind_host="127.0.0.1",
            contract_host="pkg",
            opennvr_url="http://opennvr:8080",
        ),
        AlertDispatcher([_RecorderChannel()]),
        frame_source=_Source(),
        cameras=["cam-1"],
    )
    await app_obj.run(once=True)

    # Registered once, advertising the ephemeral port that was bound.
    assert len(fake.calls) == 1
    assert fake.calls[0]["json"]["url"] == f"http://pkg:{seen_ports[0]}"
    # And the server is torn down after run() returns.
    assert app_obj._contract_server is None
    with pytest.raises(httpx.TransportError):
        _get(seen_ports[0], "/health")

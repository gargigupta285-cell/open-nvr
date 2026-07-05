# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Skills derived live from KAI-C (blueprint "skills as capabilities").

``KaicCapabilitiesClient`` fetches KAI-C's aggregated capabilities
(``GET /api/v1/ai/capabilities``) with a 60s TTL and NEVER raises;
``skills_payload()`` intersects each skill's ``backing_tasks`` with the
live ``tasks_advertised`` union into an advisory ``tasks_available``
flag. ``skill_requirement_met`` stays the enable gate — a briefly
unreachable KAI-C must not disable (or grey out) working tools.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from adapter_clients import KaicCapabilitiesClient
from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec


def _runtime() -> CameraAgentRuntime:
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front door")],
    )
    return CameraAgentRuntime(cfg)


def _by_id(runtime: CameraAgentRuntime) -> dict[str, dict]:
    return {s["id"]: s for s in runtime.skills_payload()}


class _StubCaps:
    """Stands in for KaicCapabilitiesClient on the runtime: a canned
    ``tasks_advertised`` set (or None = unreachable) + a refresh counter."""

    def __init__(self, tasks: set[str] | None) -> None:
        self.tasks = tasks
        self.refreshes = 0

    @property
    def tasks_advertised(self) -> set[str] | None:
        return self.tasks

    async def refresh(self) -> set[str] | None:
        self.refreshes += 1
        return self.tasks


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Drop-in for the mixin's httpx.AsyncClient: canned payload or error."""

    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0
        self.last_headers: dict | None = None

    async def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
        self.calls += 1
        self.last_headers = headers
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload or {})


_CAPS_PAYLOAD = {
    "contract_version": "1",
    "sovereignty_mode": "local_only",
    "adapters": {
        "yolov8": {"tasks_advertised": ["object_detection"]},
        "blip": {"tasks_advertised": ["image_captioning"]},
        "insightface": {"tasks_advertised": ["face_recognition"]},
    },
}


# ── KaicCapabilitiesClient: fetch, auth header, TTL, failure modes ─────


def test_capabilities_client_fetches_union_of_tasks():
    client = KaicCapabilitiesClient(kaic_url="http://k/", api_key="sekrit")
    client._http = _FakeHttp(payload=_CAPS_PAYLOAD)
    tasks = asyncio.run(client.refresh())
    assert tasks == {"object_detection", "image_captioning", "face_recognition"}
    assert client.tasks_advertised == tasks
    # Internal key threaded exactly like the infer path; URL is the v1 route.
    assert client._http.last_headers == {"X-Internal-Api-Key": "sekrit"}
    assert client._url == "http://k/api/v1/ai/capabilities"


def test_capabilities_client_no_key_sends_no_header():
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="")
    client._http = _FakeHttp(payload=_CAPS_PAYLOAD)
    asyncio.run(client.refresh())
    assert client._http.last_headers == {}


def test_capabilities_client_caches_within_ttl():
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="x", ttl_seconds=60)
    client._http = _FakeHttp(payload=_CAPS_PAYLOAD)

    async def go():
        await client.refresh()
        await client.refresh()
        await client.refresh()

    asyncio.run(go())
    assert client._http.calls == 1          # served from cache after the first


def test_capabilities_client_refetches_after_ttl():
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="x", ttl_seconds=0)
    client._http = _FakeHttp(payload=_CAPS_PAYLOAD)

    async def go():
        await client.refresh()
        await client.refresh()

    asyncio.run(go())
    assert client._http.calls == 2


def test_capabilities_client_unreachable_yields_none_and_never_raises():
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="x")
    client._http = _FakeHttp(error=httpx.ConnectError("kai-c down"))
    tasks = asyncio.run(client.refresh())
    assert tasks is None
    assert client.tasks_advertised is None


def test_capabilities_client_negative_caches_failures():
    # A down KAI-C is retried at most once per TTL — the skills poll must
    # not pay a connect timeout on every request.
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="x", ttl_seconds=60)
    client._http = _FakeHttp(error=httpx.ConnectError("kai-c down"))

    async def go():
        await client.refresh()
        await client.refresh()

    asyncio.run(go())
    assert client._http.calls == 1
    assert client.tasks_advertised is None


def test_capabilities_client_malformed_payload_yields_none():
    client = KaicCapabilitiesClient(kaic_url="http://k", api_key="x")
    client._http = _FakeHttp(payload={"adapters": {"weird": {"tasks_advertised": None}}})
    assert asyncio.run(client.refresh()) == set()   # tolerated: no tasks listed
    client2 = KaicCapabilitiesClient(kaic_url="http://k", api_key="x")
    client2._http = _FakeHttp(payload={"adapters": "not-a-dict"})
    assert asyncio.run(client2.refresh()) is None   # structurally broken → unknown


# ── skills_payload: per-skill backing_tasks / tasks_available ──────────


def test_skills_payload_all_backing_tasks_available():
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(
        {"object_detection", "image_captioning", "face_recognition"})
    skills = _by_id(rt)
    assert skills["see"]["backing_tasks"] == ["image_captioning", "vqa"]
    assert skills["see"]["tasks_available"] is True
    assert skills["count"]["backing_tasks"] == ["object_detection"]
    assert skills["count"]["tasks_available"] is True
    assert skills["faces"]["backing_tasks"] == ["face_recognition"]
    assert skills["faces"]["tasks_available"] is True
    # Converged watch monitors: object detection + their SDK rule library.
    assert skills["watch"]["backing_tasks"] == ["object_detection"]
    assert skills["watch"]["tasks_available"] is True
    assert skills["watch"]["rules"] == ["line_crossing", "occupancy"]


def test_skills_payload_missing_tasks_flagged():
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps({"image_captioning"})   # only captioning
    skills = _by_id(rt)
    assert skills["see"]["tasks_available"] is True          # captioning|vqa
    assert skills["count"]["tasks_available"] is False
    assert skills["faces"]["tasks_available"] is False
    assert skills["watch"]["tasks_available"] is False


def test_skills_payload_vqa_alone_backs_see():
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps({"vqa"})
    assert _by_id(rt)["see"]["tasks_available"] is True


def test_skills_payload_kaic_unreachable_falls_back_to_config_behavior():
    # None (= unreachable / never fetched) must read as available: the field
    # is advisory and a down KAI-C must not grey out working skills.
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(None)
    skills = _by_id(rt)
    for sid in ("see", "count", "faces", "watch"):
        assert skills[sid]["tasks_available"] is True
    # ...and the pre-existing shape/gating is untouched.
    assert skills["see"]["enabled"] is True
    assert skills["count"]["enabled"] is True
    for key in ("id", "icon", "name", "example", "uses",
                "enabled", "available", "hint"):
        assert key in skills["see"]


def test_skills_without_kaic_backing_always_task_available():
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(set())                  # KAI-C: no adapters
    skills = _by_id(rt)
    for sid in ("events", "footage", "alarm", "report", "task"):
        assert skills[sid]["backing_tasks"] == []
        assert skills[sid]["tasks_available"] is True


def test_missing_tasks_do_not_gate_enablement():
    # skill_requirement_met stays the enable gate: tasks_available=False is
    # display data, the tool stays advertised/enabled.
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(set())                  # nothing advertised
    skills = _by_id(rt)
    assert skills["count"]["tasks_available"] is False
    assert skills["count"]["enabled"] is True
    assert "detect_objects" in {t["function"]["name"] for t in rt.tool_definitions}


# ── /skills endpoint: refresh (TTL'd) + fields on the wire ─────────────


def test_skills_endpoint_refreshes_and_reports_fields():
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps({"object_detection"})
    client = TestClient(build_app(rt))
    skills = {s["id"]: s for s in client.get("/skills").json()["skills"]}
    assert rt.kaic_capabilities.refreshes == 1
    assert skills["count"]["tasks_available"] is True
    assert skills["see"]["tasks_available"] is False
    assert skills["see"]["backing_tasks"] == ["image_captioning", "vqa"]
    assert skills["watch"]["rules"] == ["line_crossing", "occupancy"]


def test_skills_endpoint_survives_unreachable_kaic():
    class _BoomCaps(_StubCaps):
        async def refresh(self):                              # noqa: D401
            self.refreshes += 1
            self.tasks = None                                 # like a failed fetch
            return None

    rt = _runtime()
    rt.kaic_capabilities = _BoomCaps(None)
    client = TestClient(build_app(rt))
    resp = client.get("/skills")
    assert resp.status_code == 200
    assert all(s["tasks_available"] is True for s in resp.json()["skills"])

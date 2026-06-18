# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Watchlist / face enrollment. The recognition adapter is stubbed (no model
runs here) — these tests cover the agent-side enroll/list/forget flow,
graceful behaviour when faces aren't configured, and the /people endpoints."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec


def _runtime(faces_url=None):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        faces_url=faces_url, faces_token="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="door")],
    )
    rt = CameraAgentRuntime(cfg)

    async def fake_get_frame(cam):
        return b"\xff\xd8\xffFRAME"

    rt.context.get_frame = fake_get_frame
    return rt


class _FakeFaces:
    def __init__(self):
        self.enrolled = []
    async def enroll(self, *, name, frame_jpeg, category="known"):
        self.enrolled.append({"name": name, "category": category, "bytes": len(frame_jpeg)})
        return {"ok": True}
    async def list_people(self):
        return [{"name": e["name"], "category": e["category"]} for e in self.enrolled]
    async def forget(self, name):
        self.enrolled = [e for e in self.enrolled if e["name"] != name]
        return {"ok": True}
    async def aclose(self):
        pass


# ── not configured ─────────────────────────────────────────────────────


def test_face_tools_report_unconfigured():
    rt = _runtime(faces_url=None)
    assert rt.faces is None

    async def go():
        assert "isn't configured" in await rt._handle_enroll_face({"name": "Mom", "camera_id": "cam1"})
        assert "isn't configured" in await rt._handle_list_people({})
        assert "isn't configured" in await rt._handle_forget_face({"name": "Mom"})
    asyncio.run(go())


# ── enroll / list / forget ─────────────────────────────────────────────


def test_enroll_captures_frame_and_calls_adapter():
    rt = _runtime(faces_url="http://faces")
    rt.faces = _FakeFaces()

    async def go():
        msg = await rt._handle_enroll_face({"name": "Alex", "camera_id": "cam1", "category": "family"})
        assert "Alex" in msg
        assert rt.faces.enrolled[0] == {"name": "Alex", "category": "family", "bytes": len(b"\xff\xd8\xffFRAME")}
        listed = await rt._handle_list_people({})
        assert "Alex" in listed
        await rt._handle_forget_face({"name": "Alex"})
        assert rt.faces.enrolled == []
    asyncio.run(go())


def test_enroll_requires_name_and_valid_camera():
    rt = _runtime(faces_url="http://faces")
    rt.faces = _FakeFaces()

    async def go():
        assert (await rt._handle_enroll_face({"camera_id": "cam1"})).endswith("?")
        assert "Available" in await rt._handle_enroll_face({"name": "X", "camera_id": "nope"})
    asyncio.run(go())


def test_enroll_handles_adapter_error():
    rt = _runtime(faces_url="http://faces")

    class _Boom(_FakeFaces):
        async def enroll(self, **kw):
            raise RuntimeError("no face detected")
    rt.faces = _Boom()

    async def go():
        msg = await rt._handle_enroll_face({"name": "Y", "camera_id": "cam1"})
        assert "couldn't enroll" in msg.lower()
    asyncio.run(go())


# ── endpoints ──────────────────────────────────────────────────────────


def test_people_endpoints():
    rt = _runtime(faces_url="http://faces")
    rt.faces = _FakeFaces()
    client = TestClient(build_app(rt))

    assert client.get("/people").json()["configured"] is True
    r = client.post("/people", json={"name": "Sam", "camera_id": "cam1"})
    assert r.status_code == 202
    assert "Sam" in [p["name"] for p in client.get("/people").json()["people"]]
    assert client.delete("/people/Sam").status_code == 200
    assert client.get("/people").json()["people"] == []
    # bad enroll → 400
    assert client.post("/people", json={"camera_id": "cam1"}).status_code == 400


def test_people_endpoint_unconfigured():
    rt = _runtime(faces_url=None)
    client = TestClient(build_app(rt))
    body = client.get("/people").json()
    assert body["configured"] is False and body["people"] == []

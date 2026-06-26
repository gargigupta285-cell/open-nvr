# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""'Use this machine's camera' — GET /devices lists local capture devices and
POST /devices/use registers them at runtime (zero provisioning). Hardware is
never touched: discovery is monkeypatched and the device source opens lazily."""
from __future__ import annotations

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec
from fastapi.testclient import TestClient


def _runtime(monkeypatch, discovered):
    monkeypatch.setattr(ca, "discover_local_cameras",
                        lambda all_devices=False: list(discovered))
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    text_mode=True, cameras=[])
    return CameraAgentRuntime(cfg)


def test_list_local_devices_does_not_register(monkeypatch):
    rt = _runtime(monkeypatch, [("local0", "device:0")])
    assert rt.list_local_devices() == [{"camera_id": "local0", "frame_url": "device:0"}]
    # discovery alone must not add cameras
    assert rt.cfg.cameras == []


def test_use_local_cameras_registers_once(monkeypatch):
    rt = _runtime(monkeypatch, [("local0", "device:0")])
    added = rt.use_local_cameras()
    assert [c.camera_id for c in added] == ["local0"]
    assert rt.context.known_camera("local0")
    assert any(c.camera_id == "local0" for c in rt.cfg.cameras)
    # idempotent — second call adds nothing
    assert rt.use_local_cameras() == []
    assert sum(c.camera_id == "local0" for c in rt.cfg.cameras) == 1


def test_devices_http_endpoints(monkeypatch):
    rt = _runtime(monkeypatch, [("local0", "device:/dev/video0")])
    client = TestClient(build_app(rt))

    listed = client.get("/devices").json()
    assert listed["devices"] == [{"camera_id": "local0", "frame_url": "device:/dev/video0"}]

    used = client.post("/devices/use", json={}).json()
    assert used["added"] == [{"camera_id": "local0", "frame_url": "device:/dev/video0"}]
    assert any(c["camera_id"] == "local0" for c in used["cameras"])

    # now it shows up in the camera roster the demo dropdown reads
    roster = client.get("/cameras").json()["cameras"]
    assert any(c["camera_id"] == "local0" for c in roster)


def test_use_all_devices_flag(monkeypatch):
    rt = _runtime(monkeypatch, [("local0", "device:/dev/video0"),
                                ("local1", "device:/dev/video2")])
    client = TestClient(build_app(rt))
    used = client.post("/devices/use", json={"all_devices": True}).json()
    assert {c["camera_id"] for c in used["added"]} == {"local0", "local1"}

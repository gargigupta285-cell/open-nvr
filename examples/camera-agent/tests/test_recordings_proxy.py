# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The camera screen's Recorded row: user-token pass-through to the main
server's playback API. The agent stores no video and never uses its
service key here — every forward carries the CALLER's bearer token."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from camera_agent import AppConfig, CameraAgentRuntime, build_app
from context import CameraSpec

SEGS = [{"start": "2026-07-08T09:00:00Z", "duration": 300.0},
        {"start": "2026-07-07T22:10:00Z", "duration": 120.0}]


class _FakeRecordings:
    def __init__(self):
        self.tokens_seen: list[str] = []

    async def resolve_path(self, token, opennvr_camera_id):
        self.tokens_seen.append(token)
        return (200, "cam-7") if opennvr_camera_id == 7 else (200, None)

    async def playback_list(self, token, path):
        self.tokens_seen.append(token)
        return 200, {"recordings": SEGS, "path": path}

    async def playback_url(self, token, path, start, duration):
        self.tokens_seen.append(token)
        return 200, {"url": f"http://media/get?path={path}&start={start}",
                     "path": path, "start": start, "duration": duration}

    async def aclose(self):  # pragma: no cover
        pass


def _client(opennvr_camera_id=7):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        opennvr_api_url="http://srv",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg",
                            role="front", opennvr_camera_id=opennvr_camera_id),
                 CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="gate")],
    )
    rt = CameraAgentRuntime(cfg)
    rt.recordings = _FakeRecordings()
    return rt, TestClient(build_app(rt))


def test_list_forwards_with_callers_token():
    rt, c = _client()
    r = c.get("/recordings/cam1", headers={"Authorization": "Bearer USERTOK"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "cam-7" and body["recordings"] == SEGS
    assert set(rt.recordings.tokens_seen) == {"USERTOK"}   # never the service key


def test_play_returns_direct_player_url():
    rt, c = _client()
    r = c.get("/recordings/cam1/play",
              params={"start": SEGS[0]["start"], "duration": 300.0},
              headers={"Authorization": "Bearer USERTOK"})
    assert r.status_code == 200
    assert r.json()["url"].startswith("http://media/get?path=cam-7")


def test_unlinked_camera_is_a_labelled_404():
    _, c = _client()
    r = c.get("/recordings/cam2", headers={"Authorization": "Bearer USERTOK"})
    assert r.status_code == 404 and r.json()["unlinked"] is True


def test_unknown_camera_404_and_missing_token_401():
    _, c = _client()
    assert c.get("/recordings/nope",
                 headers={"Authorization": "Bearer T"}).status_code == 404
    assert c.get("/recordings/cam1").status_code == 401


def test_no_recordings_yet_is_a_calm_200():
    rt, c = _client()

    async def resolve_none(token, oid):
        return 200, None

    rt.recordings.resolve_path = resolve_none
    r = c.get("/recordings/cam1", headers={"Authorization": "Bearer T"})
    assert r.status_code == 200 and r.json()["recordings"] == []


def test_config_parses_opennvr_camera_id(tmp_path):
    import yaml
    from camera_agent import load_config

    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(yaml.safe_dump({
        "kaic_url": "http://k", "kaic_api_key": "x", "system_prompt": "t",
        "cameras": [
            {"camera_id": "front", "frame_url": "http://x/1.jpg",
             "opennvr_camera_id": 7},
            {"camera_id": "gate", "frame_url": "http://x/2.jpg"},
        ]}))
    cfg = load_config(cfg_file)
    assert cfg.cameras[0].opennvr_camera_id == 7
    assert cfg.cameras[1].opennvr_camera_id is None


# ── live stream proxy (the laggy-stills fix) ───────────────────────────


def test_live_proxy_returns_whep_with_jwt():
    rt, c = _client()

    async def stream_info(token, oid):
        rt.recordings.tokens_seen.append(token)
        assert oid == 7
        return 200, {"urls": {"webrtc": "http://mtx:8889/cam-7/whep"},
                     "token": "MMTX", "stream_name": "cam-7",
                     "expires_in_minutes": 60}

    rt.recordings.stream_info = stream_info
    r = c.get("/streams/cam1/live", headers={"Authorization": "Bearer USERTOK"})
    assert r.status_code == 200
    body = r.json()
    assert body["whep_url"] == "http://mtx:8889/cam-7/whep?jwt=MMTX"
    assert body["stream_name"] == "cam-7"
    assert "USERTOK" in rt.recordings.tokens_seen   # caller's token, never the service key


def test_live_proxy_states():
    rt, c = _client()
    # unlinked camera → labelled 404 (page falls back to stills)
    r = c.get("/streams/cam2/live", headers={"Authorization": "Bearer T"})
    assert r.status_code == 404 and r.json()["unlinked"] is True
    assert c.get("/streams/nope/live",
                 headers={"Authorization": "Bearer T"}).status_code == 404
    assert c.get("/streams/cam1/live").status_code == 401

    async def stream_info(token, oid):
        return 200, {"urls": {}, "token": "T"}    # server missing WebRTC

    rt.recordings.stream_info = stream_info
    assert c.get("/streams/cam1/live",
                 headers={"Authorization": "Bearer T"}).status_code == 502

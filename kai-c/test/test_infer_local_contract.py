# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ISSUE-73 regression tests for the ``/infer/local`` handler.

The backend's default transport (``OPENNVR_ADAPTER_CONTRACT=v1``) sends a
contract-v1 body with the frame bytes inline as ``frame_b64``. The handler
used to understand only the legacy ``{"input": {"frame": {"uri": ...}}}``
shape, so every v1 request found no URI and was rejected with
``400 frame not found``. These tests pin both shapes plus the
path-traversal guard on the legacy branch.

``/infer/local`` calls the adapter with the synchronous ``requests``
library, so we stub ``main.requests.post`` to capture the body KAI-C
forwards rather than standing up a real adapter.
"""
from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path

import pytest


class _FakeResp:
    def __init__(self, json_body: dict, status_code: int = 200) -> None:
        self._json = json_body
        self.status_code = status_code

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


@pytest.fixture
def kaic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build the app with a loopback adapter URL and a stubbed
    ``requests.post`` that records the forwarded body."""
    monkeypatch.setenv("AI_SOVEREIGNTY", "local_only")
    monkeypatch.setenv("ADAPTER_URL", "http://127.0.0.1:9100")
    monkeypatch.setenv("KAI_C_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("FRAMES_DIR", str(tmp_path / "frames"))
    (tmp_path / "frames").mkdir(parents=True, exist_ok=True)

    if "main" in sys.modules:
        importlib.reload(sys.modules["main"])
    import main as kaic_main

    captured: dict = {}

    adapter_json = {
        "model_name": "stub-model",
        "model_version": "v1",
        "inference_ms": 7,
        "result": {
            "detections": [
                {"label": "person", "confidence": 0.9,
                 "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
            ]
        },
    }

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return _FakeResp(adapter_json)

    monkeypatch.setattr(kaic_main.requests, "post", fake_post)

    from fastapi.testclient import TestClient

    with TestClient(kaic_main.app) as client:
        yield client, captured, tmp_path


# -- contract-v1 (frame_b64) -- the ISSUE-73 path --


def test_v1_frame_b64_body_is_forwarded(kaic):
    client, captured, _ = kaic
    resp = client.post(
        "/infer/local",
        json={"task": "person_detection", "frame_b64": "QUJD", "confidence": 0.5},
    )
    assert resp.status_code == 200, resp.text
    assert captured["body"]["frame_b64"] == "QUJD"
    assert captured["body"]["task"] == "person_detection"
    assert captured["body"]["confidence_threshold"] == 0.5
    assert "confidence" not in captured["body"]
    flat = resp.json()["response"]
    assert flat["label"] == "person"
    assert flat["count"] == 1


def test_v1_empty_frame_b64_is_rejected(kaic):
    client, _captured, _ = kaic
    resp = client.post("/infer/local", json={"task": "x", "frame_b64": ""})
    assert resp.status_code == 400
    assert "frame_b64" in resp.json()["detail"]


# -- legacy URI shape still works --


def test_legacy_uri_body_resolves_and_forwards(kaic):
    client, captured, tmp_path = kaic
    cam = tmp_path / "frames" / "camera_1"
    cam.mkdir(parents=True, exist_ok=True)
    (cam / "latest.jpg").write_bytes(b"JPEGDATA")

    resp = client.post(
        "/infer/local",
        json={
            "task": "person_detection",
            "input": {"frame": {"uri": "opennvr://frames/camera_1/latest.jpg"},
                      "params": {"confidence": 0.4}},
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["body"]["frame_b64"] == base64.b64encode(b"JPEGDATA").decode()
    assert captured["body"]["confidence_threshold"] == 0.4


def test_legacy_uri_missing_file_is_rejected(kaic):
    client, _captured, _ = kaic
    resp = client.post(
        "/infer/local",
        json={"input": {"frame": {"uri": "opennvr://frames/camera_1/nope.jpg"}}},
    )
    assert resp.status_code == 400
    assert "frame not found" in resp.json()["detail"]


def test_legacy_uri_path_traversal_blocked(kaic):
    client, _captured, _ = kaic
    resp = client.post(
        "/infer/local",
        json={"input": {"frame": {"uri": "opennvr://frames/../../../etc/passwd"}}},
    )
    assert resp.status_code == 400
    assert "outside the configured frames directory" in resp.json()["detail"]

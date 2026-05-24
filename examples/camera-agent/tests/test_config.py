# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for camera-agent config loading + the per-camera role
prompt assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from camera_agent import (
    AppConfig,
    CameraAgentRuntime,
    load_config,
)


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "c.yml"
    cfg.write_text(body)
    return cfg


def _minimal_yaml(frame_path: Path | None = None) -> str:
    frame_url = (
        f"file://{frame_path}" if frame_path is not None else "http://example.invalid/snap.jpg"
    )
    return (
        "kaic_url: http://x\n"
        "kaic_api_key: y\n"
        "cameras:\n"
        "  - camera_id: front-porch\n"
        f"    frame_url: {frame_url}\n"
        "    role: entrance\n"
    )


def _seed_frame(tmp_path: Path, name: str = "frame.jpg") -> Path:
    """Create a tiny placeholder file the FileFrameSource will accept.
    Contents don't matter for config-load tests; only existence does."""
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return p


def test_load_requires_kaic_url(tmp_path):
    cfg = _write(tmp_path, "kaic_api_key: y\ncameras: []\n")
    with pytest.raises(SystemExit, match="kaic_url"):
        load_config(cfg)


def test_load_requires_kaic_api_key(tmp_path):
    cfg = _write(tmp_path, "kaic_url: http://x\ncameras: []\n")
    with pytest.raises(SystemExit, match="kaic_api_key"):
        load_config(cfg)


def test_load_requires_at_least_one_camera(tmp_path):
    cfg = _write(tmp_path, "kaic_url: http://x\nkaic_api_key: y\ncameras: []\n")
    with pytest.raises(SystemExit, match="at least one camera"):
        load_config(cfg)


def test_load_rejects_camera_without_id_or_url(tmp_path):
    cfg = _write(
        tmp_path,
        "kaic_url: http://x\nkaic_api_key: y\n"
        "cameras:\n  - role: x\n",
    )
    with pytest.raises(SystemExit, match="camera_id"):
        load_config(cfg)


def test_load_pulls_camera_role(tmp_path):
    parsed = load_config(_write(tmp_path, _minimal_yaml()))
    assert parsed.cameras[0].role == "entrance"


def test_load_uses_default_role_when_missing(tmp_path):
    parsed = load_config(_write(
        tmp_path,
        "kaic_url: http://x\nkaic_api_key: y\n"
        "cameras:\n  - {camera_id: a, frame_url: http://example.invalid/x}\n",
    ))
    assert "no role" in parsed.cameras[0].role


def test_load_rejects_non_numeric_temperature(tmp_path):
    cfg = _write(
        tmp_path,
        _minimal_yaml() + "llm_temperature: very-hot\n",
    )
    with pytest.raises(SystemExit, match="llm_temperature"):
        load_config(cfg)


def test_load_carries_llm_overrides(tmp_path):
    cfg = _write(
        tmp_path,
        _minimal_yaml() +
        "llm_model: llama3.1:8b\nllm_temperature: 0.2\nllm_max_tokens: 64\n",
    )
    parsed = load_config(cfg)
    assert parsed.llm_model == "llama3.1:8b"
    assert parsed.llm_temperature == pytest.approx(0.2)
    assert parsed.llm_max_tokens == 64


# ── System-prompt assembly ─────────────────────────────────────────


def test_system_prompt_includes_camera_roster(tmp_path):
    """The LLM should see the configured camera ids + roles so it
    can map natural language ('the front door') to camera_id values."""
    f1 = _seed_frame(tmp_path, "porch.jpg")
    f2 = _seed_frame(tmp_path, "back.jpg")
    parsed = load_config(_write(
        tmp_path,
        "kaic_url: http://x\nkaic_api_key: y\n"
        "cameras:\n"
        f"  - {{camera_id: front-porch, frame_url: 'file://{f1}', role: 'main entrance'}}\n"
        f"  - {{camera_id: back-door,  frame_url: 'file://{f2}', role: 'garden facing'}}\n",
    ))
    runtime = CameraAgentRuntime(parsed)
    prompt = runtime.build_system_prompt()
    assert "front-porch" in prompt and "main entrance" in prompt
    assert "back-door" in prompt and "garden facing" in prompt
    assert "exactly as listed" in prompt

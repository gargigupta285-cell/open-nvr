# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Smoke tests for the inference-listener example.

The NATS roundtrip is exercised by the KAI-C side's
``test_nats_publisher.py``; here we just validate the config parser
and the default ``handle_event`` formatter so the example stays
working as a copy-as-template.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from textwrap import dedent

import pytest

from inference_listener import AppConfig, InferenceListener, load_config


def test_load_config_minimal(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        nats_url: "nats://nats:4222"
    """))
    c = load_config(str(cfg))
    assert c.nats_url == "nats://nats:4222"
    assert c.nats_token is None
    # Default wildcard subject
    assert c.subject_pattern == "opennvr.inference.>"


def test_load_config_full(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        nats_url: "nats://localhost:4222"
        nats_token: "secret-token"
        subject_pattern: "opennvr.inference.yolov8.>"
    """))
    c = load_config(str(cfg))
    assert c.nats_url == "nats://localhost:4222"
    assert c.nats_token == "secret-token"
    assert c.subject_pattern == "opennvr.inference.yolov8.>"


def test_load_config_rejects_missing_nats_url(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text("nats_token: t\n")
    with pytest.raises(ValueError, match="nats_url"):
        load_config(str(cfg))


def test_load_config_rejects_empty_subject(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        nats_url: "nats://x"
        subject_pattern: ""
    """))
    with pytest.raises(ValueError, match="subject_pattern"):
        load_config(str(cfg))


def test_handle_event_default_formats_one_line():
    """The default ``handle_event`` prints a one-line summary including
    the adapter, camera, correlation_id, latency, and (when present)
    detection labels. Tail-able + greppable; the canonical operator
    interaction with this example."""
    listener = InferenceListener(AppConfig(
        nats_url="nats://x", nats_token=None,
        subject_pattern="opennvr.inference.>",
    ))
    payload = {
        "correlation_id": "abc-123",
        "adapter": "yolov8",
        "adapter_version": "1.0.0",
        "camera_id": "cam-front",
        "model_name": "yolov8n",
        "model_version": "v1",
        "inference_ms": 38,
        "result": {
            "detections": [
                {"label": "person"},
                {"label": "car"},
            ],
        },
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        listener.handle_event("opennvr.inference.yolov8.cam-front.completed", payload)
    out = buf.getvalue().strip()
    assert "yolov8/cam-front" in out
    assert "correlation_id=abc-123" in out
    assert "inference_ms=38" in out
    assert "detections=2" in out
    assert "person" in out and "car" in out


def test_handle_event_no_detections_omits_summary():
    """For result bodies without a ``detections`` array (e.g., ASR
    transcripts, TTS clips), the formatter shouldn't pretend they
    have detections."""
    listener = InferenceListener(AppConfig(
        nats_url="nats://x", nats_token=None,
        subject_pattern="opennvr.inference.>",
    ))
    payload = {
        "correlation_id": "asr-1",
        "adapter": "whisper-asr",
        "camera_id": None,
        "model_name": "whisper-tiny",
        "model_version": "v1",
        "inference_ms": 220,
        "result": {"transcript": "hello world", "language": "en"},
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        listener.handle_event("opennvr.inference.whisper-asr.unknown.completed", payload)
    out = buf.getvalue().strip()
    assert "detections=" not in out
    assert "whisper-asr" in out


def test_handle_event_truncates_long_label_lists():
    """A frame with 20 people shouldn't dump 20 labels onto one
    stdout line — operators tail the listener, line length matters."""
    listener = InferenceListener(AppConfig(
        nats_url="nats://x", nats_token=None,
        subject_pattern="opennvr.inference.>",
    ))
    payload = {
        "correlation_id": "crowd-1", "adapter": "yolov8",
        "camera_id": "cam-busy", "model_name": "yolov8n",
        "model_version": "v1", "inference_ms": 50,
        "result": {"detections": [{"label": "person"} for _ in range(20)]},
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        listener.handle_event("opennvr.inference.yolov8.cam-busy.completed", payload)
    out = buf.getvalue().strip()
    # First 3 labels visible, then truncation marker.
    assert "detections=20" in out
    assert "…" in out or "..." in out

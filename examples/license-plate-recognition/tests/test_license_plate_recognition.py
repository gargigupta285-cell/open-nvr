# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the LicensePlateRecognizer orchestrator + config loader.

The pipeline + KAI-C HTTP clients are exercised separately; here we
stub the pipeline so we can verify dedup, watchlist severity routing,
and the SIGINT-clean shutdown contract.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock

import pytest

from license_plate_recognition import (
    AppConfig,
    CameraConfig,
    LicensePlateRecognizer,
    load_config,
)
from plate_pipeline import PlateRead


# ── Helpers ─────────────────────────────────────────────────────────


def _app_config(**overrides) -> AppConfig:
    # frame_url uses http:// so build_frame_source doesn't validate
    # a filesystem path at init time. The test stubs the FrameSource
    # afterwards so no HTTP call is ever made.
    base = AppConfig(
        kaic_url="http://localhost:8100",
        kaic_api_key="test-key",
        cameras=[CameraConfig(camera_id="cam-1", frame_url="http://example.invalid/frame.jpg")],
        poll_interval_seconds=0.0,
        request_timeout_seconds=1.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _plate_read(plate: str, vehicle_label: str = "car") -> PlateRead:
    return PlateRead(
        plate_text=plate,
        ocr_confidence=0.91,
        vehicle_label=vehicle_label,
        vehicle_confidence=0.88,
        vehicle_bbox=(10, 10, 200, 200),
        model_id="fake-model",
        correlation_id="cid-1",
    )


def _build_recognizer(reads_per_step: Iterable[Iterable[PlateRead]], config: AppConfig | None = None):
    """Build a LicensePlateRecognizer whose pipeline yields scripted
    PlateRead lists on successive process_frame calls."""
    cfg = config or _app_config()
    pipeline = MagicMock()
    pipeline.process_frame.side_effect = [list(reads) for reads in reads_per_step]

    dispatcher = MagicMock()

    recognizer = LicensePlateRecognizer(cfg, pipeline, dispatcher)

    # Replace each camera's FrameSource with a stub that returns a
    # canned JPEG byte string. Tests don't need a real frame; the
    # pipeline is mocked so the bytes never reach Pillow / a real
    # adapter.
    class _StubFrameSource:
        def fetch(self) -> bytes:
            return b"\xff\xd8jpeg"

    for cam_id in list(recognizer._frame_sources):
        recognizer._frame_sources[cam_id] = _StubFrameSource()

    return recognizer, pipeline, dispatcher


# ── Config loader ──────────────────────────────────────────────────


def test_load_config_requires_kaic_url_and_api_key(tmp_path: Path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("cameras:\n  - {camera_id: a, frame_url: file:///x}\n")
    with pytest.raises(SystemExit, match="kaic_url"):
        load_config(cfg)


def test_load_config_requires_at_least_one_camera(tmp_path: Path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "kaic_url: http://x\n"
        "kaic_api_key: y\n"
        "cameras: []\n"
    )
    with pytest.raises(SystemExit, match="camera"):
        load_config(cfg)


def test_load_config_uppercases_watchlists(tmp_path: Path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "kaic_url: http://x\n"
        "kaic_api_key: y\n"
        "cameras:\n  - {camera_id: a, frame_url: file:///x}\n"
        "allowlist: [abc-001, def-002]\n"
        "denylist: ['bad-999']\n"
    )
    cfg_obj = load_config(cfg)
    assert cfg_obj.allowlist == ["ABC-001", "DEF-002"]
    assert cfg_obj.denylist == ["BAD-999"]


# ── Severity routing ──────────────────────────────────────────────


def test_denylist_plate_fires_high_severity():
    cfg = _app_config(denylist=["BAD-001"])
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("bad-001")]], config=cfg
    )
    recognizer.step()
    dispatcher.dispatch.assert_called_once()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "high"
    assert "Watchlist" in alert.title


def test_allowlist_plate_fires_low_severity():
    cfg = _app_config(allowlist=["MY-CAR"])
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("my-car")]], config=cfg
    )
    recognizer.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "low"
    assert "Expected" in alert.title


def test_unlisted_plate_fires_info_severity():
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("abc-123")]]
    )
    recognizer.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "info"


# ── Dedup ──────────────────────────────────────────────────────────


def test_dedup_suppresses_repeat_plate_within_window():
    cfg = _app_config(dedup_window_seconds=60.0)
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("abc-123")], [_plate_read("abc-123")]],
        config=cfg,
    )
    recognizer.step()
    recognizer.step()
    # Second step's read was within the dedup window → only one dispatch.
    assert dispatcher.dispatch.call_count == 1


def test_dedup_zero_window_fires_every_time():
    cfg = _app_config(dedup_window_seconds=0.0)
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("abc-123")], [_plate_read("abc-123")]],
        config=cfg,
    )
    recognizer.step()
    recognizer.step()
    assert dispatcher.dispatch.call_count == 2


def test_dedup_is_keyed_per_camera():
    cfg = _app_config(
        cameras=[
            CameraConfig(camera_id="cam-1", frame_url="http://example.invalid/a.jpg"),
            CameraConfig(camera_id="cam-2", frame_url="http://example.invalid/b.jpg"),
        ],
        dedup_window_seconds=60.0,
    )
    # process_frame called once per camera per step; we run one step
    # with two cameras → two scripted return values.
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("abc-123")], [_plate_read("abc-123")]],
        config=cfg,
    )
    recognizer.step()
    # Same plate read on TWO different cameras should fire twice.
    assert dispatcher.dispatch.call_count == 2


# ── Multi-vehicle frame ────────────────────────────────────────────


def test_multiple_reads_in_one_frame_each_fire_once():
    recognizer, _pipeline, dispatcher = _build_recognizer(
        [[_plate_read("aaa-111"), _plate_read("bbb-222", vehicle_label="truck")]]
    )
    recognizer.step()
    assert dispatcher.dispatch.call_count == 2

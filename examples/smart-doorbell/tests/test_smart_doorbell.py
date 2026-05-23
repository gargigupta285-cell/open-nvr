# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Orchestrator tests for SmartDoorbell — dedup, severity routing,
snapshot attachment for unknown faces, and the config loader.

Pipeline + KAI-C HTTP clients are exercised separately; here we
stub the pipeline.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock

import pytest

from face_recognition_pipeline import FaceRead
from smart_doorbell import (
    AppConfig,
    CameraConfig,
    SmartDoorbell,
    load_config,
)


# ── Helpers ────────────────────────────────────────────────────────


def _app_config(**overrides) -> AppConfig:
    base = AppConfig(
        kaic_url="http://localhost:8100",
        kaic_api_key="test-key",
        cameras=[CameraConfig(camera_id="front-door", frame_url="http://example.invalid/snap.jpg")],
        poll_interval_seconds=0.0,
        request_timeout_seconds=1.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _known_read(person_id: str = "alice", name: str = "Alice Smith",
                category: str = "family") -> FaceRead:
    return FaceRead(
        face_detected=True,
        recognized=True,
        person_id=person_id,
        name=name,
        category=category,
        similarity=0.91,
        face_bbox=(100, 80, 240, 240),
        threshold=0.5,
        correlation_id="cid-1",
    )


def _unknown_read() -> FaceRead:
    return FaceRead(
        face_detected=True,
        recognized=False,
        face_bbox=(80, 60, 220, 220),
        threshold=0.5,
        correlation_id="cid-1",
    )


def _no_face_read() -> FaceRead:
    return FaceRead(
        face_detected=False,
        recognized=False,
        correlation_id="cid-1",
    )


def _build_doorbell(reads_per_call: Iterable[FaceRead | None], config: AppConfig | None = None):
    cfg = config or _app_config()
    pipeline = MagicMock()
    pipeline.process_frame.side_effect = list(reads_per_call)
    dispatcher = MagicMock()
    doorbell = SmartDoorbell(cfg, pipeline, dispatcher)

    # Stub each camera's FrameSource so tests don't hit the network.
    class _StubFrameSource:
        def fetch(self) -> bytes:
            return b"\xff\xd8jpeg"

    for cam_id in list(doorbell._frame_sources):
        doorbell._frame_sources[cam_id] = _StubFrameSource()

    return doorbell, pipeline, dispatcher


# ── Config loader ──────────────────────────────────────────────────


def test_load_config_requires_kaic_url_and_api_key(tmp_path: Path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("cameras:\n  - {camera_id: a, frame_url: http://x/a}\n")
    with pytest.raises(SystemExit, match="kaic_url"):
        load_config(cfg)


def test_load_config_allows_zero_cameras_for_enroll_subcommand(tmp_path: Path):
    """Enroll / list-faces don't need cameras configured. The daemon
    rejects later; the parser accepts."""
    cfg = tmp_path / "c.yml"
    cfg.write_text("kaic_url: http://x\nkaic_api_key: y\n")
    parsed = load_config(cfg)
    assert parsed.cameras == []


def test_load_config_carries_through_recognition_threshold(tmp_path: Path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "kaic_url: http://x\nkaic_api_key: y\n"
        "cameras:\n  - {camera_id: a, frame_url: http://x/a}\n"
        "recognition_threshold: 0.7\n"
    )
    parsed = load_config(cfg)
    assert parsed.recognition_threshold == pytest.approx(0.7)


# ── Severity routing ──────────────────────────────────────────────


def test_known_family_fires_low_severity():
    doorbell, _pipeline, dispatcher = _build_doorbell([_known_read(category="family")])
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "low"
    assert "Alice Smith" in alert.title


def test_known_non_family_fires_info_severity():
    """A registered face under e.g. category=friend isn't a family
    member — info level."""
    doorbell, _pipeline, dispatcher = _build_doorbell([_known_read(category="friend")])
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "info"


def test_unknown_face_fires_high_severity():
    doorbell, _pipeline, dispatcher = _build_doorbell([_unknown_read()])
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert alert.severity == "high"
    assert "Unknown visitor" in alert.title


# ── No-face / pipeline-failure handling ───────────────────────────


def test_no_face_detected_does_not_dispatch():
    doorbell, _pipeline, dispatcher = _build_doorbell([_no_face_read()])
    doorbell.step()
    dispatcher.dispatch.assert_not_called()


def test_pipeline_returning_none_does_not_dispatch():
    """``process_frame`` returning None (call failed) means drop
    the frame quietly — same shape as no-face."""
    doorbell, _pipeline, dispatcher = _build_doorbell([None])
    doorbell.step()
    dispatcher.dispatch.assert_not_called()


# ── Dedup ──────────────────────────────────────────────────────────


def test_dedup_keyed_on_person_id_for_known():
    cfg = _app_config(dedup_window_seconds=60.0)
    doorbell, _pipeline, dispatcher = _build_doorbell(
        [_known_read(person_id="alice"), _known_read(person_id="alice")], config=cfg,
    )
    doorbell.step()
    doorbell.step()
    assert dispatcher.dispatch.call_count == 1


def test_dedup_distinguishes_different_known_persons():
    cfg = _app_config(dedup_window_seconds=60.0)
    doorbell, _pipeline, dispatcher = _build_doorbell(
        [_known_read(person_id="alice"), _known_read(person_id="bob", name="Bob")],
        config=cfg,
    )
    doorbell.step()
    doorbell.step()
    assert dispatcher.dispatch.call_count == 2


def test_dedup_keyed_on_unknown_bucket_per_camera():
    """Two unknown faces on the SAME camera within the window dedup
    to one alert — we have no person_id to distinguish them."""
    cfg = _app_config(dedup_window_seconds=60.0)
    doorbell, _pipeline, dispatcher = _build_doorbell(
        [_unknown_read(), _unknown_read()], config=cfg,
    )
    doorbell.step()
    doorbell.step()
    assert dispatcher.dispatch.call_count == 1


def test_dedup_window_zero_fires_every_time():
    cfg = _app_config(dedup_window_seconds=0.0)
    doorbell, _pipeline, dispatcher = _build_doorbell(
        [_known_read(), _known_read()], config=cfg,
    )
    doorbell.step()
    doorbell.step()
    assert dispatcher.dispatch.call_count == 2


# ── Snapshot attachment ───────────────────────────────────────────


def test_unknown_face_alert_carries_snapshot_when_enabled():
    cfg = _app_config(attach_snapshot_for_unknowns=True)
    doorbell, _pipeline, dispatcher = _build_doorbell([_unknown_read()], config=cfg)
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert "snapshot_b64" in alert.evidence
    assert alert.evidence["snapshot_mime"] == "image/jpeg"
    # Verify the base64 decodes to the stub frame bytes.
    assert base64.b64decode(alert.evidence["snapshot_b64"]) == b"\xff\xd8jpeg"


def test_unknown_face_alert_does_not_carry_snapshot_when_disabled():
    cfg = _app_config(attach_snapshot_for_unknowns=False)
    doorbell, _pipeline, dispatcher = _build_doorbell([_unknown_read()], config=cfg)
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert "snapshot_b64" not in alert.evidence


def test_known_face_never_carries_snapshot():
    """Known-face alerts intentionally ride small so the alert bus
    stays low-bandwidth in the common case."""
    cfg = _app_config(attach_snapshot_for_unknowns=True)
    doorbell, _pipeline, dispatcher = _build_doorbell([_known_read()], config=cfg)
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert "snapshot_b64" not in alert.evidence


def test_oversized_snapshot_dropped_from_envelope(caplog):
    """A snapshot above ``snapshot_max_bytes`` is dropped from the
    envelope (the alert still fires) and a WARN log line is emitted.
    Keeps post-base64 envelope under NATS's 1 MB default max_payload."""
    # Stub frame in _build_doorbell is 6 bytes; setting the cap to 3
    # forces a drop without needing to allocate a giant blob.
    cfg = _app_config(attach_snapshot_for_unknowns=True, snapshot_max_bytes=3)
    doorbell, _pipeline, dispatcher = _build_doorbell([_unknown_read()], config=cfg)
    with caplog.at_level("WARNING", logger="smart-doorbell"):
        doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert "snapshot_b64" not in alert.evidence
    assert any(
        "exceeds snapshot_max_bytes" in rec.getMessage() for rec in caplog.records
    )


def test_snapshot_cap_zero_disables_limit():
    """``snapshot_max_bytes=0`` means 'no cap' — useful for operators
    on NATS configured for >1 MB payloads."""
    cfg = _app_config(attach_snapshot_for_unknowns=True, snapshot_max_bytes=0)
    doorbell, _pipeline, dispatcher = _build_doorbell([_unknown_read()], config=cfg)
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    assert "snapshot_b64" in alert.evidence


# ── Evidence payload ──────────────────────────────────────────────


def test_alert_evidence_carries_recognition_metadata():
    doorbell, _pipeline, dispatcher = _build_doorbell([_known_read()])
    doorbell.step()
    alert = dispatcher.dispatch.call_args.args[0]
    e = alert.evidence
    assert e["recognized"] is True
    assert e["person_id"] == "alice"
    assert e["name"] == "Alice Smith"
    assert e["category"] == "family"
    assert e["similarity"] == pytest.approx(0.91, rel=1e-3)
    assert e["face_bbox"] == [100, 80, 240, 240]
    assert e["threshold"] == pytest.approx(0.5)

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Synthetic demo mode: hardware-free `synth:` cameras that carry their own
ground truth + a demo detector that reads it, so the agent gives deterministic,
recordable answers with no cameras/adapters. (Needs Pillow for the renderer.)"""
from __future__ import annotations

import asyncio

import pytest

from adapter_clients import SyntheticDetectionClient
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec
from frame_sources import (
    SyntheticFrameSource,
    build_frame_source,
    parse_synth_spec,
    synth_detections_from_frame,
)

pytest.importorskip("PIL")  # the renderer needs Pillow


def test_parse_synth_spec_normalises_labels():
    assert parse_synth_spec("people=2,cars=1") == {"person": 2, "car": 1}
    assert parse_synth_spec("person=1") == {"person": 1}
    assert parse_synth_spec("") == {}
    assert parse_synth_spec("dogs=3") == {"dog": 3}


def test_factory_builds_synth_source():
    s = build_frame_source(camera_id="cam", url="synth:people=2,cars=1")
    assert isinstance(s, SyntheticFrameSource)


def test_synth_frame_is_jpeg_and_deterministic_and_carries_truth():
    s = SyntheticFrameSource(camera_id="front", spec="people=2,cars=1")
    a = s.fetch()
    b = s.fetch()
    assert a == b                       # deterministic — same frame every call
    assert a[:2] == b"\xff\xd8"         # JPEG SOI
    dets = synth_detections_from_frame(a)
    labels = sorted(d["label"] for d in dets)
    assert labels == ["car", "person", "person"]
    # boxes are spread out so the IoU de-dup keeps each object
    assert all("bbox" in d and d["confidence"] > 0 for d in dets)


def test_no_marker_means_no_detections():
    assert synth_detections_from_frame(b"\xff\xd8\xff\xd9 not a synth frame") == []


def test_demo_detection_client_reads_frame_truth():
    frame = SyntheticFrameSource(camera_id="c", spec="people=3").fetch()
    out = asyncio.run(SyntheticDetectionClient().infer(frame_jpeg=frame))
    dets = out["result"]["detections"]
    assert len(dets) == 3 and all(d["label"] == "person" for d in dets)


def _demo_runtime():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t", text_mode=True,
        synthetic_detection=True,
        cameras=[
            CameraSpec(camera_id="front_door", frame_url="synth:people=2", role="door"),
            CameraSpec(camera_id="driveway", frame_url="synth:cars=1", role="drive"),
        ],
    )
    return CameraAgentRuntime(cfg)


def test_runtime_uses_demo_detector_and_counts_match_scene():
    rt = _demo_runtime()
    assert isinstance(rt.detection_client, SyntheticDetectionClient)
    # detect_objects over the scripted scenes returns the exact counts drawn
    front = asyncio.run(rt.tools.detect_objects({"camera_id": "front_door"}))
    drive = asyncio.run(rt.tools.detect_objects({"camera_id": "driveway"}))
    assert "2 people" in front
    assert "car" in drive

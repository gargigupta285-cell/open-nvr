# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Run on whatever hardware you're already on: local capture-device frame
source (laptop webcam / USB / Pi / drone /dev/video) + auto-discovery, so the
example needs zero camera provisioning. Hardware is never touched here — the
device opens lazily and we monkeypatch the node lister."""
from __future__ import annotations

import sys
import types

import pytest

import frame_sources as fs
from frame_sources import (
    DeviceFrameSource,
    FrameSourceError,
    build_frame_source,
    discover_local_cameras,
    _parse_device_spec,
)


def test_device_spec_parsing():
    assert _parse_device_spec("0") == 0
    assert _parse_device_spec("auto") == 0
    assert _parse_device_spec("") == 0
    assert _parse_device_spec("/dev/video1") == "/dev/video1"
    assert _parse_device_spec("2") == 2


def test_factory_builds_device_source_for_index_and_path():
    s = build_frame_source(camera_id="cam", url="device:0")
    assert isinstance(s, DeviceFrameSource)
    assert s._spec == 0
    assert s._cap is None  # lazy — no hardware touched at construction
    s2 = build_frame_source(camera_id="cam", url="device:/dev/video0")
    assert isinstance(s2, DeviceFrameSource)
    assert s2._spec == "/dev/video0"


def test_discover_defaults_to_index0_when_no_nodes(monkeypatch):
    monkeypatch.setattr(fs, "_list_video_devices", lambda: [])
    assert discover_local_cameras() == [("local0", "device:0")]


def test_discover_picks_first_node_by_default(monkeypatch):
    monkeypatch.setattr(fs, "_list_video_devices", lambda: ["/dev/video0", "/dev/video1"])
    assert discover_local_cameras() == [("local0", "device:/dev/video0")]


def test_discover_all_devices(monkeypatch):
    monkeypatch.setattr(fs, "_list_video_devices", lambda: ["/dev/video0", "/dev/video2"])
    out = discover_local_cameras(all_devices=True)
    assert out == [("local0", "device:/dev/video0"), ("local1", "device:/dev/video2")]


def test_fetch_grabs_and_jpeg_encodes_with_fake_cv2(monkeypatch):
    """Inject a fake cv2 so we exercise open→read→imencode without a camera."""
    grabbed = {}

    class _Cap:
        def isOpened(self):
            return True
        def read(self):
            return True, "FRAME"
        def release(self):
            grabbed["released"] = True

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.VideoCapture = lambda spec: (grabbed.setdefault("spec", spec), _Cap())[1]
    fake_cv2.imencode = lambda ext, frame: (True, _FakeBuf(b"JPEGBYTES"))
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    s = DeviceFrameSource(camera_id="cam", device="3")
    assert s.fetch() == b"JPEGBYTES"
    assert grabbed["spec"] == 3


def test_fetch_raises_and_resets_on_read_failure(monkeypatch):
    class _Cap:
        def isOpened(self):
            return True
        def read(self):
            return False, None
        def release(self):
            pass

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.VideoCapture = lambda spec: _Cap()
    fake_cv2.imencode = lambda ext, frame: (True, _FakeBuf(b"x"))
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    s = DeviceFrameSource(camera_id="cam", device="0")
    with pytest.raises(FrameSourceError):
        s.fetch()
    assert s._cap is None  # handle dropped so next cycle re-opens


def test_open_failure_is_friendly(monkeypatch):
    class _Cap:
        def isOpened(self):
            return False

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.VideoCapture = lambda spec: _Cap()
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    s = DeviceFrameSource(camera_id="cam", device="0")
    with pytest.raises(FrameSourceError) as ei:
        s.fetch()
    assert "could not open capture device" in str(ei.value)


# ── load_config auto-discovery wiring ──────────────────────────────────


def test_load_config_auto_discovers_local_camera(monkeypatch, tmp_path):
    import camera_agent as ca

    monkeypatch.setattr(
        ca, "discover_local_cameras",
        lambda all_devices=False: [("local0", "device:0")],
    )
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "kaic_url: http://k\nkaic_api_key: x\n"
        "auto_discover_cameras: true\n"
        "system_prompt: hi\n"
    )
    cfg = ca.load_config(str(cfg_file))
    assert cfg.auto_discover_cameras is True
    assert [c.frame_url for c in cfg.cameras] == ["device:0"]
    assert cfg.cameras[0].camera_id == "local0"


class _FakeBuf:
    def __init__(self, b):
        self._b = b
    def tobytes(self):
        return self._b

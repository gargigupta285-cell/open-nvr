# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
WS-mode tests for the intrusion-detection example.

The HTTP path is exercised end-to-end by ``test_intrusion_detection.py``.
Here we focus on the new opt-in WS path (``kaic_transport: ws``):

* Detector dispatches to ``KaicStreamClient`` instead of ``KaicClient``
  when ``kaic_transport == "ws"``.
* Stream clients are built lazily on first frame, one per camera,
  reused across cycles (no reconnect storm).
* ``KaicError`` from the WS path is handled the same way as HTTP
  errors (skip, no alert, log warning).
* ``detector.close()`` tears down all stream clients.
* Config: ``kaic_transport`` validation accepts ``http`` / ``ws``,
  rejects everything else.
* ``KaicStreamClient`` URL translation: http→ws, https→wss, other → ValueError.

We use the ``stream_client_factory`` injection point on
``IntrusionDetector`` so these tests don't need a real WebSocket
server — that machinery is covered end-to-end by KAI-C's own
``test_stream_proxy.py`` (A2.4b). What we validate here is that the
example wires it together correctly.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from alerts import AlertDispatcher
from intrusion_detection import (
    AppConfig,
    CameraWatch,
    IntrusionDetector,
    KaicClient,
    KaicError,
    KaicStreamClient,
    RestrictedHours,
    load_config,
)
from zone import Zone


# ── Fake KaicStreamClient ───────────────────────────────────────────


class _FakeStreamClient:
    """Mimics ``KaicStreamClient`` without opening a real WS connection.

    Tracks frames sent + provides per-test detection injection. Same
    response shape as the real client (``InferResponse``-like dict so
    the detector's existing post-parser works unchanged)."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.frames_sent: int = 0
        self.closed: bool = False
        self.next_detections: list[dict[str, Any]] = []
        self.raise_on_next: Exception | None = None

    def infer_frame(self, *, frame_bytes: bytes, correlation_id: str) -> dict[str, Any]:
        if self.raise_on_next is not None:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc
        self.frames_sent += 1
        return {
            "status": "ok",
            "model_name": "yolov8n",
            "model_version": "v1",
            "inference_ms": 12,
            "result": {
                "detections": self.next_detections,
                "frame_dimensions": {"w": 1920, "h": 1080},
            },
        }

    def close(self) -> None:
        self.closed = True


def _detection(label: str, *, x: float, y: float) -> dict[str, Any]:
    return {
        "label": label,
        "confidence": 0.9,
        "bbox": {"x": x, "y": y, "w": 0.1, "h": 0.1},
        "track_id": None,
        "attributes": {},
    }


def _build_ws_detector(
    tmp_path: Path,
    *,
    now: _dt.datetime,
    restricted_hours: RestrictedHours | None = None,
) -> tuple[IntrusionDetector, dict[str, _FakeStreamClient]]:
    """Build a detector with kaic_transport=ws and a fake stream-client
    factory. Returns (detector, fakes_by_camera_id) so tests can
    inspect what was sent / inject responses."""
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"FAKE_JPEG")
    zone = Zone.from_config("center", [[480, 270], [1440, 270], [1440, 810], [480, 810]])
    camera = CameraWatch(
        camera_id="cam-ws-test",
        frame_url=f"file://{frame_path}",
        zone=zone,
        frame_width=1920,
        frame_height=1080,
    )
    config = AppConfig(
        kaic_url="http://kaic.test",
        kaic_adapter_name="yolov8",
        kaic_api_key=None,
        poll_interval_seconds=1.0,
        watch_labels=["person", "car"],
        restricted_hours=restricted_hours or RestrictedHours(
            start=_dt.time(0, 0), end=_dt.time(23, 59),
        ),
        cameras=[camera],
        webhook_url=None,
        kaic_transport="ws",  # <-- the new path
    )
    # We pass a never-used HTTP client because IntrusionDetector still
    # accepts one in __init__; in WS mode it's just not called.
    import httpx
    http_client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(500, json={"detail": "HTTP path should not be called in WS mode"})
    ))
    kaic = KaicClient(
        config.kaic_url, config.kaic_adapter_name,
        api_key=None, timeout_seconds=5.0,
        http_client=http_client,
    )

    fakes: dict[str, _FakeStreamClient] = {}

    def factory(camera_id: str) -> _FakeStreamClient:
        fake = _FakeStreamClient(camera_id)
        fakes[camera_id] = fake
        return fake  # type: ignore[return-value]

    class _Recorder:
        name = "recorder"
        def __init__(self): self.alerts = []
        def send(self, alert): self.alerts.append(alert); return True

    recorder = _Recorder()
    detector = IntrusionDetector(
        config, kaic, AlertDispatcher([recorder]),
        now=lambda: now,
        stream_client_factory=factory,
    )
    detector.recorder = recorder  # type: ignore[attr-defined]
    return detector, fakes


# ── Tests ──────────────────────────────────────────────────────────


def test_ws_mode_routes_to_stream_client_not_http(tmp_path):
    """A detector with kaic_transport=ws calls the stream-client
    factory, not the HTTP client."""
    detector, fakes = _build_ws_detector(
        tmp_path, now=_dt.datetime(2026, 5, 19, 10, 0),
    )
    # Setup: detection in zone → alert should fire.
    detector.step(detector._config.cameras[0])
    # After step, the fake stream client got exactly one frame.
    assert "cam-ws-test" in fakes
    assert fakes["cam-ws-test"].frames_sent == 1


def test_ws_mode_fires_alert_for_in_zone_detection(tmp_path):
    detector, fakes = _build_ws_detector(
        tmp_path, now=_dt.datetime(2026, 5, 19, 10, 0),
    )
    # Wire the detection BEFORE step() — the factory builds the fake
    # on the first call inside step(), so we set up via a small dance:
    # do one no-detection step first, then inject + step again.
    detector.step(detector._config.cameras[0])
    fakes["cam-ws-test"].next_detections = [_detection("person", x=0.45, y=0.45)]
    fired = detector.step(detector._config.cameras[0])
    assert len(fired) == 1
    assert "person" in fired[0].title.lower()
    assert detector.recorder.alerts == fired


def test_ws_mode_reuses_client_across_steps(tmp_path):
    """One persistent stream client per camera — not one per step."""
    detector, fakes = _build_ws_detector(
        tmp_path, now=_dt.datetime(2026, 5, 19, 10, 0),
    )
    detector.step(detector._config.cameras[0])
    first = fakes["cam-ws-test"]
    detector.step(detector._config.cameras[0])
    detector.step(detector._config.cameras[0])
    # Same fake instance, three frames sent through it.
    assert fakes["cam-ws-test"] is first
    assert first.frames_sent == 3


def test_ws_mode_kaic_error_skips_cycle_no_alert(tmp_path):
    """Transport failures on the WS path skip the cycle like HTTP —
    no alert, no crash."""
    detector, fakes = _build_ws_detector(
        tmp_path, now=_dt.datetime(2026, 5, 19, 10, 0),
    )
    # Prime the fake (force it built) then make next call fail.
    detector.step(detector._config.cameras[0])
    fakes["cam-ws-test"].raise_on_next = KaicError("WS connection lost")
    fakes["cam-ws-test"].next_detections = [_detection("person", x=0.45, y=0.45)]
    fired = detector.step(detector._config.cameras[0])
    assert fired == []
    assert detector.recorder.alerts == []


def test_ws_mode_close_tears_down_stream_clients(tmp_path):
    detector, fakes = _build_ws_detector(
        tmp_path, now=_dt.datetime(2026, 5, 19, 10, 0),
    )
    detector.step(detector._config.cameras[0])
    assert not fakes["cam-ws-test"].closed
    detector.close()
    assert fakes["cam-ws-test"].closed
    # And the dict is cleared so a subsequent step would rebuild —
    # exercises the "reconnect after shutdown" path operators may
    # see when the daemon restarts.
    assert detector._stream_clients == {}


# ── Config + URL translation ────────────────────────────────────────


def test_config_accepts_ws_transport(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        kaic_url: "http://127.0.0.1:8100"
        kaic_transport: "ws"
        restricted_hours:
            start: "00:00"
            end: "23:59"
        cameras:
          - camera_id: "c"
            frame_url: "file:///x"
            zone: [[0,0],[1,0],[1,1]]
    """))
    config = load_config(str(cfg))
    assert config.kaic_transport == "ws"


def test_config_defaults_to_http_transport(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        kaic_url: "http://127.0.0.1:8100"
        restricted_hours:
            start: "00:00"
            end: "23:59"
        cameras:
          - camera_id: "c"
            frame_url: "file:///x"
            zone: [[0,0],[1,0],[1,1]]
    """))
    config = load_config(str(cfg))
    assert config.kaic_transport == "http"


def test_config_rejects_invalid_transport(tmp_path: Path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(dedent("""
        kaic_url: "http://127.0.0.1:8100"
        kaic_transport: "grpc"
        restricted_hours:
            start: "00:00"
            end: "23:59"
        cameras:
          - camera_id: "c"
            frame_url: "file:///x"
            zone: [[0,0],[1,0],[1,1]]
    """))
    with pytest.raises(ValueError, match="kaic_transport"):
        load_config(str(cfg))


def test_stream_client_translates_http_to_ws():
    client = KaicStreamClient(
        "http://127.0.0.1:8100", "yolov8", "cam-1",
        api_key=None, timeout_seconds=5.0,
    )
    assert client._url == "ws://127.0.0.1:8100/api/v1/infer/yolov8/stream"


def test_stream_client_translates_https_to_wss():
    client = KaicStreamClient(
        "https://kaic.example/", "yolov8", "cam-2",
        api_key="k", timeout_seconds=5.0,
    )
    assert client._url == "wss://kaic.example/api/v1/infer/yolov8/stream"


def test_stream_client_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="http"):
        KaicStreamClient(
            "ftp://nope", "yolov8", "cam-3",
            api_key=None, timeout_seconds=5.0,
        )

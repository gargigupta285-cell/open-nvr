# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Alerts-subscriber tests — config parsing, alert handling, webhook forward."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from alerts_subscriber import (
    AlertConsumer,
    AppConfig,
    load_config,
)


# ── Config loading ─────────────────────────────────────────────────


def _write_config(tmp_path: Path, body: str) -> str:
    p = tmp_path / "config.yml"
    p.write_text(body)
    return str(p)


def test_load_config_minimal(tmp_path: Path):
    path = _write_config(tmp_path, 'nats_url: "nats://broker:4222"\n')
    cfg = load_config(path)
    assert cfg.nats_url == "nats://broker:4222"
    assert cfg.nats_token is None
    # Default subject pattern catches everything.
    assert cfg.subject_pattern == "opennvr.alerts.>"
    assert cfg.webhook_url is None


def test_load_config_requires_nats_url(tmp_path: Path):
    path = _write_config(tmp_path, "{}\n")
    with pytest.raises(ValueError, match="nats_url"):
        load_config(path)


def test_load_config_rejects_blank_nats_url(tmp_path: Path):
    path = _write_config(tmp_path, 'nats_url: "   "\n')
    with pytest.raises(ValueError, match="nats_url"):
        load_config(path)


def test_load_config_rejects_empty_subject_pattern_when_present(tmp_path: Path):
    path = _write_config(
        tmp_path,
        'nats_url: "nats://broker:4222"\nsubject_pattern: ""\n',
    )
    with pytest.raises(ValueError, match="subject_pattern"):
        load_config(path)


def test_load_config_accepts_custom_subject_pattern(tmp_path: Path):
    path = _write_config(
        tmp_path,
        'nats_url: "nats://broker:4222"\n'
        'subject_pattern: "opennvr.alerts.app.intrusion-detection.>"\n',
    )
    cfg = load_config(path)
    assert cfg.subject_pattern == "opennvr.alerts.app.intrusion-detection.>"


def test_load_config_carries_webhook(tmp_path: Path):
    path = _write_config(
        tmp_path,
        'nats_url: "nats://broker:4222"\n'
        'webhook_url: "https://example.invalid/hook"\n'
        'webhook_timeout_seconds: 9.5\n',
    )
    cfg = load_config(path)
    assert cfg.webhook_url == "https://example.invalid/hook"
    assert cfg.webhook_timeout_seconds == 9.5


def test_load_config_rejects_non_mapping_root(tmp_path: Path):
    path = _write_config(tmp_path, '- 1\n- 2\n')
    with pytest.raises(ValueError, match="mapping"):
        load_config(path)


# ── handle_alert default formatting ────────────────────────────────


def _alert_payload() -> dict:
    return {
        "alert_id": "alrt_abc123",
        "fired_at": "2026-05-21T14:33:24+00:00",
        "title": "Person loitering in zone 'shed-perimeter'",
        "description": "Detected loitering.",
        "severity": "medium",
        "source": {"kind": "app", "name": "loitering-detection", "version": "1.0.0"},
        "camera_id": "cam-back-shed",
        "correlation_id": "corr-xyz",
        "evidence": {"dwell_seconds": 75.0, "threshold_seconds": 60.0},
        "tags": ["loitering", "person"],
    }


def test_handle_alert_prints_one_line(capsys):
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
    )
    consumer = AlertConsumer(cfg)
    consumer.handle_alert(
        "opennvr.alerts.app.loitering-detection.cam-back-shed",
        _alert_payload(),
    )
    out = capsys.readouterr().out
    assert "ALERT" in out
    assert "MEDIUM" in out  # severity upper-cased
    assert "cam-back-shed" in out
    assert "corr-xyz" in out
    assert "loitering-detection" in out  # source.name surfaces
    # Single line — operators grep with tail -f.
    assert out.count("\n") == 1


def test_handle_alert_handles_missing_fields_gracefully(capsys):
    """A malformed §11.5 envelope (missing severity / source / etc.)
    should not crash the consumer — print ``?`` placeholders and move
    on."""
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
    )
    consumer = AlertConsumer(cfg)
    consumer.handle_alert("opennvr.alerts.unknown", {})
    out = capsys.readouterr().out
    assert "ALERT" in out
    assert "?" in out  # placeholder for missing fields


# ── handle_alert webhook forward ───────────────────────────────────


class _CapturingTransport(httpx.BaseTransport):
    def __init__(self, status_code: int = 200) -> None:
        self.calls: list[dict] = []
        self._status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = bytes(request.read())
        self.calls.append({
            "url": str(request.url),
            "method": request.method,
            "body": body,
        })
        return httpx.Response(self._status_code, json={"ok": True})


def test_handle_alert_forwards_to_webhook(monkeypatch, capsys):
    transport = _CapturingTransport(200)

    def _patched(url, **kwargs):
        kwargs.pop("trust_env", None)
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr("alerts_subscriber.httpx.post", _patched)
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
        webhook_url="https://example.invalid/hook",
    )
    consumer = AlertConsumer(cfg)
    payload = _alert_payload()
    consumer.handle_alert(
        "opennvr.alerts.app.loitering-detection.cam-back-shed",
        payload,
    )
    capsys.readouterr()  # drain stdout

    assert len(transport.calls) == 1
    parsed = json.loads(transport.calls[0]["body"])
    assert parsed == payload  # forwards the alert verbatim
    assert consumer._forwarded_count == 1
    assert consumer._forward_failed_count == 0


def test_handle_alert_records_webhook_failure(monkeypatch, capsys):
    transport = _CapturingTransport(503)

    def _patched(url, **kwargs):
        kwargs.pop("trust_env", None)
        return httpx.Client(transport=transport).post(url, **kwargs)

    monkeypatch.setattr("alerts_subscriber.httpx.post", _patched)
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
        webhook_url="https://example.invalid/hook",
    )
    consumer = AlertConsumer(cfg)
    consumer.handle_alert(
        "opennvr.alerts.app.loitering-detection.cam-back-shed",
        _alert_payload(),
    )
    capsys.readouterr()
    assert consumer._forwarded_count == 0
    assert consumer._forward_failed_count == 1


def test_handle_alert_swallows_webhook_exception(monkeypatch, capsys):
    """Webhook transport exception (DNS failure, timeout, etc.) must
    not propagate — same contract as intrusion-detection's
    WebhookChannel."""
    def _raises(*args, **kwargs):
        raise RuntimeError("DNS lookup failed")

    monkeypatch.setattr("alerts_subscriber.httpx.post", _raises)
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
        webhook_url="https://example.invalid/hook",
    )
    consumer = AlertConsumer(cfg)
    # MUST NOT raise.
    consumer.handle_alert(
        "opennvr.alerts.app.loitering-detection.cam-back-shed",
        _alert_payload(),
    )
    capsys.readouterr()
    assert consumer._forward_failed_count == 1


def test_handle_alert_no_webhook_when_url_unset(monkeypatch, capsys):
    """No webhook URL → no httpx call, no counter movement. Just stdout."""
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("httpx.post called when webhook_url is None")

    monkeypatch.setattr("alerts_subscriber.httpx.post", _should_not_be_called)
    cfg = AppConfig(
        nats_url="nats://broker:4222",
        nats_token=None,
        subject_pattern="opennvr.alerts.>",
        webhook_url=None,
    )
    consumer = AlertConsumer(cfg)
    consumer.handle_alert(
        "opennvr.alerts.app.loitering-detection.cam-back-shed",
        _alert_payload(),
    )
    capsys.readouterr()
    assert consumer._forwarded_count == 0
    assert consumer._forward_failed_count == 0

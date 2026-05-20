# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for KAI-C's NATS publisher (B1 event-bus surface).

Covers the publish-or-skip semantics: subject construction, disabled-
mode short-circuit, sovereignty validation under ``local_only``, and
the "publish failure doesn't propagate" guarantee. The end-to-end
roundtrip against a real NATS broker lives in the integration test
suite (skipped by default; run via ``OPENNVR_NATS_INTEGRATION=1``
when a broker is available locally).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kai_c.events import (
    InferenceCompletedEvent,
    inference_completed_subject,
)
from kai_c.nats_publisher import NatsPublisher


# ── Subject + schema sanity ────────────────────────────────────────


def test_subject_includes_adapter_and_camera_id():
    assert inference_completed_subject("yolov8", "cam-front-gate") == (
        "opennvr.inference.yolov8.cam-front-gate.completed"
    )


def test_subject_uses_unknown_for_missing_camera_id():
    """Conformance probes / adapter health checks may not carry a
    camera_id. Subscribers using wildcards still need to see those
    events — fall back to the literal ``unknown``."""
    assert inference_completed_subject("piper-tts", None) == (
        "opennvr.inference.piper-tts.unknown.completed"
    )


def test_event_serializes_all_required_fields():
    ev = InferenceCompletedEvent(
        correlation_id="abc-123", adapter="yolov8",
        camera_id="cam-1", model_name="yolov8n", model_version="v1",
        inference_ms=42, result={"detections": []},
    )
    body = ev.model_dump_json()
    # Spot-check — full schema is in the Pydantic model.
    assert '"correlation_id":"abc-123"' in body
    assert '"adapter":"yolov8"' in body
    assert '"inference_ms":42' in body


# ── Disabled mode (NATS_URL empty) ─────────────────────────────────


def test_disabled_publisher_reports_disabled():
    pub = NatsPublisher(url=None, token=None, sovereignty_mode="local_only")
    assert pub.enabled is False


@pytest.mark.asyncio
async def test_disabled_publisher_publish_returns_false():
    pub = NatsPublisher(url="", token=None, sovereignty_mode="local_only")
    ev = InferenceCompletedEvent(
        correlation_id="x", adapter="a", camera_id="c",
        model_name="m", model_version="v", inference_ms=0,
    )
    assert await pub.publish_inference_completed(ev) is False
    assert pub.published_count == 0


@pytest.mark.asyncio
async def test_disabled_publisher_start_logs_and_returns():
    """Calling start() on a disabled publisher should be a no-op
    (logs an info line; doesn't crash)."""
    pub = NatsPublisher(url=None, token=None, sovereignty_mode="local_only")
    await pub.start()  # must not raise
    await pub.close()  # must not raise


# ── Sovereignty enforcement ────────────────────────────────────────


def test_sovereignty_refuses_public_ip_under_local_only():
    pub = NatsPublisher(
        url="nats://8.8.8.8:4222", token="t",
        sovereignty_mode="local_only",
    )
    with pytest.raises(ValueError, match="public address"):
        pub._validate_sovereignty()


def test_sovereignty_allows_loopback_under_local_only():
    pub = NatsPublisher(
        url="nats://127.0.0.1:4222", token="t",
        sovereignty_mode="local_only",
    )
    pub._validate_sovereignty()  # should not raise


def test_sovereignty_allows_private_ip_under_local_only():
    """RFC 1918 private ranges (10/8, 172.16/12, 192.168/16) are
    treated as private under local_only."""
    pub = NatsPublisher(
        url="nats://192.168.1.50:4222", token="t",
        sovereignty_mode="local_only",
    )
    pub._validate_sovereignty()  # private IP — allowed


def test_sovereignty_allows_docker_hostnames_under_local_only():
    """Docker-bridge service names (alphabetic) may not resolve at
    KAI-C startup time. Sovereignty check defers to connect time
    rather than refusing — operators on docker-compose deployments
    must be able to use ``nats://nats:4222`` directly."""
    pub = NatsPublisher(
        url="nats://nats:4222", token="t",
        sovereignty_mode="local_only",
    )
    pub._validate_sovereignty()  # docker hostname — allowed


def test_sovereignty_skipped_under_cloud_allowed():
    """cloud_allowed is the operator's explicit opt-out — NATS can live
    anywhere. (Matches KAI-C's main.py sovereignty vocabulary:
    local_only / federated / cloud_allowed.)"""
    pub = NatsPublisher(
        url="nats://nats.example.com:4222", token="t",
        sovereignty_mode="cloud_allowed",
    )
    pub._validate_sovereignty()  # any host — allowed


def test_sovereignty_skipped_under_federated():
    """federated mode: adapters off-host, raw frames stay on-host. The
    NATS bus is allowed to live with the adapters (off-host partner
    LAN). Local_only's loopback-only enforcement does NOT apply."""
    pub = NatsPublisher(
        url="nats://nats.partner.example:4222", token="t",
        sovereignty_mode="federated",
    )
    pub._validate_sovereignty()  # any host — allowed under federated


# ── Publish failure swallowing ─────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_swallows_connect_failure():
    """If connecting to NATS raises, publish_inference_completed
    must return False and never propagate the exception. The
    request path that called us is unaffected."""
    pub = NatsPublisher(
        url="nats://127.0.0.1:4222", token="t",
        sovereignty_mode="local_only",
    )

    async def boom():
        raise ConnectionRefusedError("nats unreachable")

    pub._do_connect = boom  # type: ignore[method-assign]
    ev = InferenceCompletedEvent(
        correlation_id="x", adapter="a", camera_id="c",
        model_name="m", model_version="v", inference_ms=0,
    )
    # Must NOT raise, even on connect failure.
    ok = await pub.publish_inference_completed(ev)
    assert ok is False
    assert pub.failed_count == 1


@pytest.mark.asyncio
async def test_publish_swallows_send_failure():
    """If the broker connection is up but ``publish`` raises (broker
    restarted mid-flight), we drop the cached connection so the next
    call retries from scratch, log + return False."""
    pub = NatsPublisher(
        url="nats://127.0.0.1:4222", token="t",
        sovereignty_mode="local_only",
    )

    fake_client = MagicMock()
    fake_client.publish = AsyncMock(side_effect=BrokenPipeError("conn reset"))
    pub._client = fake_client  # bypass ensure_connected

    ev = InferenceCompletedEvent(
        correlation_id="x", adapter="a", camera_id="c",
        model_name="m", model_version="v", inference_ms=0,
    )
    ok = await pub.publish_inference_completed(ev)
    assert ok is False
    assert pub.failed_count == 1
    # Connection cleared so the next call attempts a fresh connect.
    assert pub._client is None


@pytest.mark.asyncio
async def test_publish_success_path_counts_and_subjects():
    """Happy path: publish_count increments, subject is the
    contract-shaped one."""
    pub = NatsPublisher(
        url="nats://127.0.0.1:4222", token="t",
        sovereignty_mode="local_only",
    )
    fake_client = MagicMock()
    fake_client.publish = AsyncMock(return_value=None)
    pub._client = fake_client
    ev = InferenceCompletedEvent(
        correlation_id="abc", adapter="yolov8", camera_id="cam-1",
        model_name="yolov8n", model_version="v1", inference_ms=12,
        result={"detections": []},
    )
    ok = await pub.publish_inference_completed(ev)
    assert ok is True
    assert pub.published_count == 1
    # Verify the publish() call shape — subject + JSON bytes
    args, _ = fake_client.publish.call_args
    subject, payload = args
    assert subject == "opennvr.inference.yolov8.cam-1.completed"
    assert payload.startswith(b'{') and b'"correlation_id":"abc"' in payload

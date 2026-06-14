# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""End-to-end contract test: the body the backend now builds
(``build_infer_payload``) is accepted by a REAL SDK-based adapter, and
the adapter's response flattens to the shape the inference loop persists.

This guards the regression that originally broke server↔adapter wiring:
the backend used to send an ``opennvr://`` file URI that SDK adapters
can't parse. It skips when the adapter SDK isn't importable (the SDK
lives in the sibling ``ai-adapter`` repo)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))
import adapter_contract as ac  # noqa: E402

# Locate the sibling ai-adapter repo so we can import the real SDK.
_AI_ADAPTER = Path(__file__).resolve().parents[3] / "ai-adapter"
if _AI_ADAPTER.exists():
    sys.path.insert(0, str(_AI_ADAPTER))

sdk = pytest.importorskip(
    "opennvr_adapter_sdk",
    reason="adapter SDK (ai-adapter repo) not importable in this environment",
)
try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    pytest.skip("fastapi not installed", allow_module_level=True)


def _build_stub_app():
    from opennvr_adapter_sdk import (
        AdapterApp, AdapterService, BodyShape, BODY_BYTES_KEY,
        HardwareEvaluationResponse, HardwareVerdict, InferResponse, ModelInfo,
    )

    class StubDetector(AdapterService):
        def load(self): pass
        def is_ready(self): return True
        def fingerprint(self): return "sha256:stub"
        def model_info(self):
            return ModelInfo(name="stub", version="1.0.0", framework="none",
                             fingerprint="sha256:stub")
        def hardware_evaluation(self):
            return HardwareEvaluationResponse(verdict=HardwareVerdict.OK, reasoning="ok")
        def infer(self, payload):
            frame = payload[BODY_BYTES_KEY]
            assert isinstance(frame, bytes) and frame, "adapter received no frame bytes"
            return InferResponse(
                model_name="stub", model_version="1.0.0", inference_ms=5,
                result={"detections": [
                    {"label": "person", "confidence": 0.88,
                     "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}]})

    return AdapterApp(
        service=StubDetector(), name="stub", version="1.0.0", vendor="t",
        license="MIT", tasks_advertised=["object_detection"],
        body_shape=BodyShape.IMAGE,
    ).fastapi_app


def test_backend_payload_accepted_by_real_sdk_adapter():
    os.environ["OPENNVR_ADAPTER_TOKEN"] = "tok"
    client = TestClient(_build_stub_app())

    # Backend builds the contract body from JPEG bytes (the new path).
    body = ac.build_infer_payload(task="object_detection", jpeg_bytes=b"\xff\xd8\xff-fake")

    # The adapter accepts exactly this body (what KAI-C forwards).
    resp = client.post("/infer", json=body, headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200, resp.text

    # Backend flattens the structured response for its inference loop.
    flat = ac.flatten_infer_response(resp.json())
    assert flat["label"] == "person"
    assert flat["confidence"] == 0.88
    assert flat["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert flat["count"] == 1
    assert flat["latency_ms"] == 5

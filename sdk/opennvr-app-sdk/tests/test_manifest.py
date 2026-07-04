# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""AppManifest / Param / AlertType — the future GET /manifest payload."""
from __future__ import annotations

from opennvr_app_sdk.manifest import AlertType, AppManifest, Param


def _manifest() -> AppManifest:
    return AppManifest(
        id="loitering-detection",
        name="Loitering Detection",
        version="1.0.0",
        category="perimeter",
        summary="Alerts when a watched object dwells in a zone.",
        requires_tasks=["object_detection"],
        subscribes="opennvr.inference.>",
        params=[
            Param("watch_labels", list, default=["person"]),
            Param("threshold_seconds", float, default=30.0),
            Param("zones", "geometry.polygon", per_camera=True,
                  description="Drawn in the catalog UI."),
        ],
        emits=[AlertType("loitering", severity="high")],
    )


def test_to_dict_shape():
    d = _manifest().to_dict()
    assert d["id"] == "loitering-detection"
    assert d["category"] == "perimeter"
    assert d["requires_tasks"] == ["object_detection"]
    assert d["subscribes"] == "opennvr.inference.>"
    assert len(d["params"]) == 3
    assert d["emits"] == [
        {"name": "loitering", "severity": "high", "description": ""},
    ]


def test_param_python_types_render_by_name():
    d = _manifest().to_dict()
    by_name = {p["name"]: p for p in d["params"]}
    assert by_name["watch_labels"]["type"] == "list"
    assert by_name["threshold_seconds"]["type"] == "float"
    assert by_name["watch_labels"]["default"] == ["person"]
    assert by_name["watch_labels"]["per_camera"] is False


def test_param_ui_schema_types_pass_through():
    d = _manifest().to_dict()
    zones = next(p for p in d["params"] if p["name"] == "zones")
    assert zones["type"] == "geometry.polygon"
    assert zones["per_camera"] is True


def test_manifest_defaults():
    m = AppManifest(id="x", name="X", version="0.1", category="test")
    d = m.to_dict()
    assert d["summary"] == ""
    assert d["requires_tasks"] == []
    assert d["subscribes"] is None
    assert d["params"] == []
    assert d["emits"] == []


def test_to_dict_is_json_serializable():
    import json
    json.dumps(_manifest().to_dict())  # must not raise


def test_alert_type_default_severity_is_medium():
    assert AlertType("thing").severity == "medium"

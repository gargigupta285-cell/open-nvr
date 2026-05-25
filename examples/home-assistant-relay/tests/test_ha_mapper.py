# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the alert → HaEntity mapper."""
from __future__ import annotations

import pytest

from ha_mapper import HaMapper, MappingOverride, parse_overrides


def _alert(
    source: str,
    *,
    camera_id: str = "front-porch",
    severity: str = "info",
    title: str = "Test alert",
    evidence: dict | None = None,
    alert_id: str = "alrt_1",
) -> dict:
    return {
        "alert_id": alert_id,
        "fired_at": "2026-05-22T10:00:00Z",
        "title": title,
        "description": "...",
        "severity": severity,
        "camera_id": camera_id,
        "source": {"kind": "app", "name": source, "version": "1.0.0"},
        "correlation_id": "cid-1",
        "evidence": evidence or {},
        "tags": [],
    }


# ── Default mappings ──────────────────────────────────────────────


def test_smart_doorbell_maps_to_binary_sensor_occupancy():
    mapper = HaMapper()
    entity = mapper.map(_alert("smart-doorbell"))
    assert entity is not None
    assert entity.component == "binary_sensor"
    assert entity.device_class == "occupancy"
    assert entity.object_id == "opennvr_front_porch_doorbell_visitor"
    # Exact match — looser substring asserts on either capitalisation
    # are tautological since the template literally interpolates the
    # camera_id verbatim.
    assert entity.name == "front-porch Doorbell Visitor"
    assert entity.state == "ON"


def test_package_delivery_maps_to_binary_sensor_occupancy():
    mapper = HaMapper()
    entity = mapper.map(_alert("package-delivery"))
    assert entity is not None
    assert entity.component == "binary_sensor"
    assert entity.device_class == "occupancy"
    assert "package" in entity.object_id


def test_intrusion_detection_maps_to_motion():
    mapper = HaMapper()
    entity = mapper.map(_alert("intrusion-detection"))
    assert entity is not None
    assert entity.device_class == "motion"


def test_loitering_detection_maps_to_motion():
    mapper = HaMapper()
    entity = mapper.map(_alert("loitering-detection"))
    assert entity is not None
    assert entity.device_class == "motion"


def test_lpr_maps_to_sensor_with_plate_text():
    """License-plate alerts surface as a 'last plate seen' sensor;
    the plate text is the entity state, not ON/OFF."""
    mapper = HaMapper()
    alert = _alert(
        "license-plate-recognition",
        evidence={"plate_text": "ABC-1234", "confidence": 0.92},
    )
    entity = mapper.map(alert)
    assert entity is not None
    assert entity.component == "sensor"
    assert entity.state == "ABC-1234"
    assert entity.device_class is None


def test_unknown_source_falls_back_to_generic_binary_sensor():
    mapper = HaMapper()
    entity = mapper.map(_alert("some-future-example"))
    assert entity is not None
    assert entity.component == "binary_sensor"
    # Generic fallback uses the slugged source name.
    assert "some_future_example" in entity.object_id


# ── Skip / error paths ────────────────────────────────────────────


def test_alert_without_camera_id_is_skipped():
    mapper = HaMapper()
    alert = _alert("smart-doorbell")
    alert["camera_id"] = ""
    assert mapper.map(alert) is None


def test_alert_without_source_name_is_skipped():
    mapper = HaMapper()
    alert = _alert("smart-doorbell")
    alert["source"] = {}
    assert mapper.map(alert) is None


# ── Slug normalisation ────────────────────────────────────────────


def test_camera_id_with_dashes_is_slugified():
    mapper = HaMapper()
    entity = mapper.map(_alert("smart-doorbell", camera_id="front-porch-cam"))
    assert entity is not None
    assert "front_porch_cam" in entity.object_id


def test_camera_id_with_unicode_chars_is_slugified():
    """Non-ascii camera ids — operator might name a camera 'frente'
    with accent; the slug helper should not crash and should produce
    something HA-valid."""
    mapper = HaMapper()
    entity = mapper.map(_alert("smart-doorbell", camera_id="caméra-1"))
    assert entity is not None
    # Exact slug — accents become _, ascii letters / digits survive.
    # "caméra-1" → lowercase → "caméra-1" → non-[a-z0-9] runs become _
    # → "cam_ra_1". Then prefix + suffix.
    assert entity.object_id == "opennvr_cam_ra_1_doorbell_visitor"


def test_empty_camera_id_gets_hashed_fallback():
    """An alert with camera_id of just special characters slugs to
    something unique, not a shared 'unknown' bucket — otherwise
    two malformed cameras would collide onto one HA entity."""
    mapper = HaMapper()
    e1 = mapper.map(_alert("smart-doorbell", camera_id="???"))
    e2 = mapper.map(_alert("smart-doorbell", camera_id="!!!"))
    assert e1 is not None and e2 is not None
    assert e1.object_id != e2.object_id
    assert "unknown_" in e1.object_id
    assert "unknown_" in e2.object_id


def test_very_long_camera_id_is_capped():
    """A 1000-char camera_id mustn't produce a 1000-char object_id
    (it'd embed in MQTT topics ≥2x and blow past broker limits)."""
    mapper = HaMapper()
    long_id = "x" * 1000
    entity = mapper.map(_alert("smart-doorbell", camera_id=long_id))
    assert entity is not None
    # opennvr_ + slug-cap (64) + _doorbell_visitor → at most ~90.
    assert len(entity.object_id) < 200


# ── Attributes ────────────────────────────────────────────────────


def test_snapshot_b64_is_stripped_from_attributes():
    """A 100KB+ base64 blob would balloon the MQTT payload; we
    deliberately drop it. Operators wanting the snapshot build a
    template sensor that points at evidence in their own scheme."""
    mapper = HaMapper()
    alert = _alert(
        "smart-doorbell",
        evidence={"snapshot_b64": "AAAA" * 5000, "person_id": None},
    )
    entity = mapper.map(alert)
    assert entity is not None
    assert "snapshot_b64" not in entity.attributes["evidence"]


def test_attributes_carry_core_envelope_fields():
    mapper = HaMapper()
    alert = _alert("smart-doorbell", severity="high")
    entity = mapper.map(alert)
    assert entity is not None
    attrs = entity.attributes
    assert attrs["alert_id"] == "alrt_1"
    assert attrs["severity"] == "high"
    assert attrs["camera_id"] == "front-porch"
    assert attrs["source"] == "smart-doorbell"


# ── Overrides ─────────────────────────────────────────────────────


def test_explicit_entity_id_override_wins():
    overrides = [MappingOverride(
        source="smart-doorbell",
        entity_id="binary_sensor.front_doorbell",
        name="Front Doorbell",
        device_class="door",
    )]
    mapper = HaMapper(overrides=overrides)
    entity = mapper.map(_alert("smart-doorbell"))
    assert entity is not None
    assert entity.full_entity_id == "binary_sensor.front_doorbell"
    assert entity.name == "Front Doorbell"
    assert entity.device_class == "door"


def test_entity_id_template_substitutes_camera_id():
    overrides = [MappingOverride(
        source="package-delivery",
        entity_id_template="binary_sensor.pkg_{camera_id}",
    )]
    mapper = HaMapper(overrides=overrides)
    entity = mapper.map(_alert("package-delivery", camera_id="back-door"))
    assert entity is not None
    assert entity.full_entity_id == "binary_sensor.pkg_back_door"


def test_camera_specific_override_beats_wildcard():
    """A camera-specific override should beat a same-source wildcard,
    even if the wildcard appears later in the list."""
    overrides = [
        MappingOverride(
            source="smart-doorbell", entity_id="binary_sensor.generic"
        ),
        MappingOverride(
            source="smart-doorbell",
            camera_id="front-porch",
            entity_id="binary_sensor.specific",
        ),
    ]
    mapper = HaMapper(overrides=overrides)
    entity = mapper.map(_alert("smart-doorbell", camera_id="front-porch"))
    assert entity is not None
    assert entity.full_entity_id == "binary_sensor.specific"


def test_wildcard_override_applies_when_no_camera_match():
    overrides = [
        MappingOverride(source="smart-doorbell", entity_id="binary_sensor.fallback"),
        MappingOverride(
            source="smart-doorbell", camera_id="other-cam",
            entity_id="binary_sensor.other",
        ),
    ]
    mapper = HaMapper(overrides=overrides)
    entity = mapper.map(_alert("smart-doorbell", camera_id="front-porch"))
    assert entity is not None
    assert entity.full_entity_id == "binary_sensor.fallback"


def test_override_auto_off_seconds_propagates():
    overrides = [MappingOverride(
        source="loitering-detection", auto_off_seconds=600,
    )]
    mapper = HaMapper(overrides=overrides, default_auto_off_seconds=30)
    entity = mapper.map(_alert("loitering-detection"))
    assert entity is not None
    assert entity.auto_off_seconds == 600


def test_unparseable_override_entity_id_skips_alert():
    """An override with a malformed entity_id (e.g. no dot) should
    log + skip rather than produce garbage entities."""
    overrides = [MappingOverride(
        source="smart-doorbell", entity_id="not_a_valid_entity_id",
    )]
    mapper = HaMapper(overrides=overrides)
    assert mapper.map(_alert("smart-doorbell")) is None


# ── parse_overrides ───────────────────────────────────────────────


def test_parse_overrides_empty_input():
    assert parse_overrides(None) == []
    assert parse_overrides([]) == []


def test_parse_overrides_skips_non_mapping_entries():
    out = parse_overrides(["not a dict", 42, None, {"source": "x"}])
    assert len(out) == 1
    assert out[0].source == "x"


def test_parse_overrides_requires_source():
    out = parse_overrides([{"entity_id": "binary_sensor.x"}, {"source": "y"}])
    assert len(out) == 1
    assert out[0].source == "y"


def test_parse_overrides_handles_non_numeric_auto_off():
    """A typo in auto_off_seconds shouldn't blow up the whole
    override — log and treat as 'use default'."""
    out = parse_overrides([{"source": "x", "auto_off_seconds": "abc"}])
    assert len(out) == 1
    assert out[0].auto_off_seconds is None

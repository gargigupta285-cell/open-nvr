# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Map OpenNVR §11.5 Alert envelopes onto Home Assistant entity
definitions + state.

A single OpenNVR alert may correspond to a *binary_sensor* (e.g.
"unknown visitor at front-porch", state ``on`` for a short window
then back ``off``), a *sensor* (e.g. "last seen plate" carrying the
plate text), or even a custom entity. The mapping rules live here
so the daemon stays focused on transport.

The default rules cover the four shipped producer-side examples
(smart-doorbell, package-delivery, license-plate-recognition,
intrusion-detection) plus loitering-detection. Operators override
per (source, camera_id) via the ``mappings`` block in config.yml.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Entity dataclass ───────────────────────────────────────────────


@dataclass
class HaEntity:
    """One HA entity worth publishing.

    The relay calls the publisher backend with this object; backends
    translate to MQTT discovery payloads + state topics, or to REST
    /api/states bodies.
    """

    # ``binary_sensor`` or ``sensor`` — the HA component name. We
    # don't currently produce switches / lights / climate; alerts
    # are read-only signals.
    component: str

    # Slug-safe id minus the component prefix. E.g.
    # ``opennvr_front_porch_unknown_visitor`` (HA prepends
    # "binary_sensor." automatically when publishing via the
    # discovery topic; the REST endpoint takes the full
    # ``binary_sensor.opennvr_front_porch_unknown_visitor``).
    object_id: str

    # Friendly name HA shows in the UI.
    name: str

    # HA device class — e.g. ``occupancy``, ``motion``, ``door``,
    # ``connectivity``. ``None`` means "no device class" (still a
    # valid HA entity, just renders generically).
    device_class: str | None

    # Current state value to publish. For binary_sensor this is
    # ``"ON"`` or ``"OFF"``; for sensor it's the numeric / text
    # reading.
    state: str

    # Attributes attached to the state for context. Snapshot URLs,
    # alert ids, correlation ids — all useful for HA automations
    # ("when sensor changes to ON and severity is high, send a
    # notification with the snapshot").
    attributes: dict[str, Any] = field(default_factory=dict)

    # How long (seconds) the binary_sensor should hold ``ON`` before
    # the relay flips it back to ``OFF`` automatically. Only
    # consulted for binary_sensor entities; ignored for sensor.
    # ``0`` means "no auto-off — the entity stays in its last
    # published state".
    auto_off_seconds: int = 30

    @property
    def full_entity_id(self) -> str:
        """``binary_sensor.<object_id>`` — needed for the REST
        backend and for log lines."""
        return f"{self.component}.{self.object_id}"

    @property
    def unique_id(self) -> str:
        """Stable id HA uses to dedupe across discovery restarts.
        Object ids are already ``opennvr_<camera>_<suffix>`` shaped,
        so we use them directly — no need to double-prefix.

        Multi-instance caveat: if two OpenNVR installs publish to
        the same MQTT broker / HA, their entities will collide on
        unique_id. Operators in that setup should override
        ``mqtt.discovery_prefix`` per-instance OR fork the mapper
        to add an instance-specific prefix here.
        """
        return self.object_id


# ── Default per-source mappings ────────────────────────────────────
#
# Each entry is the recipe for one (alert source, optional kind) →
# HaEntity transformation. The relay walks them in order and uses
# the first match; sources we don't know about fall back to the
# generic "binary_sensor per camera_id" path.


@dataclass(frozen=True)
class _DefaultRule:
    source: str                    # alert source.name
    component: str
    object_suffix: str             # appended to "opennvr_{camera_id}_"
    device_class: str | None
    name_template: str             # uses {camera_id}, {name}, etc.

    def matches(self, alert: dict[str, Any]) -> bool:
        src = (alert.get("source") or {}).get("name", "")
        return src == self.source


_DEFAULT_RULES: tuple[_DefaultRule, ...] = (
    _DefaultRule(
        source="smart-doorbell",
        component="binary_sensor",
        object_suffix="doorbell_visitor",
        device_class="occupancy",
        name_template="{camera_id} Doorbell Visitor",
    ),
    _DefaultRule(
        source="package-delivery",
        component="binary_sensor",
        object_suffix="package",
        device_class="occupancy",
        name_template="{camera_id} Package",
    ),
    _DefaultRule(
        source="intrusion-detection",
        component="binary_sensor",
        object_suffix="intrusion",
        device_class="motion",
        name_template="{camera_id} Intrusion",
    ),
    _DefaultRule(
        source="loitering-detection",
        component="binary_sensor",
        object_suffix="loitering",
        device_class="motion",
        name_template="{camera_id} Loitering",
    ),
    _DefaultRule(
        source="license-plate-recognition",
        component="sensor",
        object_suffix="last_plate",
        device_class=None,
        name_template="{camera_id} Last Plate",
    ),
)


# ── Operator overrides ─────────────────────────────────────────────


@dataclass
class MappingOverride:
    """One ``mappings:`` entry from config.yml. Any field left as
    None / empty falls back to the default rule's value."""

    source: str
    camera_id: str | None = None     # None means "any camera"
    entity_id: str | None = None     # explicit override (component.object_id)
    entity_id_template: str | None = None   # template with {camera_id} placeholder
    device_class: str | None = None
    name: str | None = None
    name_template: str | None = None
    auto_off_seconds: int | None = None

    def matches(self, alert: dict[str, Any]) -> bool:
        if self.source != (alert.get("source") or {}).get("name"):
            return False
        if self.camera_id is not None and self.camera_id != alert.get("camera_id"):
            return False
        return True


# ── The mapper ─────────────────────────────────────────────────────


class HaMapper:
    """Turns alert dicts into HaEntity records."""

    def __init__(
        self,
        *,
        overrides: list[MappingOverride] | None = None,
        default_auto_off_seconds: int = 30,
    ) -> None:
        self._overrides = list(overrides or [])
        self._default_auto_off = int(default_auto_off_seconds)

    def map(self, alert: dict[str, Any]) -> HaEntity | None:
        """Return the HaEntity to publish for this alert, or ``None``
        if we deliberately skip it (e.g. missing camera_id, missing
        source). The relay logs the skip but doesn't crash.
        """
        camera_id = alert.get("camera_id")
        source_name = (alert.get("source") or {}).get("name")
        if not camera_id or not source_name:
            logger.warning(
                "skipping alert without camera_id or source: alert_id=%s",
                alert.get("alert_id"),
            )
            return None

        # 1. Operator override wins.
        override = self._find_override(alert)
        if override is not None:
            return self._apply_override(alert, override)

        # 2. Default per-source rule.
        for rule in _DEFAULT_RULES:
            if rule.matches(alert):
                return self._apply_default(alert, rule)

        # 3. Fallback — unknown source. Make a generic binary_sensor
        # so the operator at least sees the alert in HA; they can
        # customise via overrides afterwards.
        return self._apply_generic(alert, source_name)

    # ── Internals ──────────────────────────────────────────────────

    def _find_override(self, alert: dict[str, Any]) -> MappingOverride | None:
        # Camera-specific overrides win over wildcard overrides.
        camera_specific: MappingOverride | None = None
        wildcard: MappingOverride | None = None
        for ov in self._overrides:
            if not ov.matches(alert):
                continue
            if ov.camera_id is not None:
                camera_specific = ov
            else:
                wildcard = ov
        return camera_specific or wildcard

    def _apply_default(self, alert: dict[str, Any], rule: _DefaultRule) -> HaEntity:
        camera_id = alert["camera_id"]
        object_id = f"opennvr_{_slug(camera_id)}_{rule.object_suffix}"
        name = rule.name_template.format(camera_id=camera_id)
        return HaEntity(
            component=rule.component,
            object_id=object_id,
            name=name,
            device_class=rule.device_class,
            state=self._state_for(alert, rule.component),
            attributes=self._attributes(alert),
            auto_off_seconds=self._default_auto_off,
        )

    def _apply_override(
        self, alert: dict[str, Any], override: MappingOverride
    ) -> HaEntity | None:
        camera_id = alert["camera_id"]
        # Resolve entity_id from explicit > template > default rule.
        entity_id: str
        if override.entity_id:
            entity_id = override.entity_id
        elif override.entity_id_template:
            entity_id = override.entity_id_template.format(camera_id=camera_id)
        else:
            # Fall back to the default rule's shape if any.
            default = next(
                (r for r in _DEFAULT_RULES if r.matches(alert)), None
            )
            if default is None:
                entity_id = f"binary_sensor.opennvr_{_slug(camera_id)}_{_slug(override.source)}"
            else:
                entity_id = (
                    f"{default.component}."
                    f"opennvr_{_slug(camera_id)}_{default.object_suffix}"
                )

        component, object_id = _split_entity_id(entity_id)
        if not component or not object_id:
            logger.warning(
                "override produced unparseable entity_id %r; skipping alert",
                entity_id,
            )
            return None

        # Name resolution.
        if override.name:
            name = override.name
        elif override.name_template:
            name = override.name_template.format(camera_id=camera_id)
        else:
            default = next(
                (r for r in _DEFAULT_RULES if r.matches(alert)), None
            )
            name = (
                default.name_template.format(camera_id=camera_id)
                if default else f"OpenNVR {camera_id} {override.source}"
            )

        device_class = override.device_class
        if device_class is None:
            default = next(
                (r for r in _DEFAULT_RULES if r.matches(alert)), None
            )
            device_class = default.device_class if default else None

        auto_off = (
            override.auto_off_seconds
            if override.auto_off_seconds is not None
            else self._default_auto_off
        )

        return HaEntity(
            component=component,
            object_id=object_id,
            name=name,
            device_class=device_class,
            state=self._state_for(alert, component),
            attributes=self._attributes(alert),
            auto_off_seconds=auto_off,
        )

    def _apply_generic(self, alert: dict[str, Any], source_name: str) -> HaEntity:
        camera_id = alert["camera_id"]
        object_id = f"opennvr_{_slug(camera_id)}_{_slug(source_name)}"
        return HaEntity(
            component="binary_sensor",
            object_id=object_id,
            name=f"OpenNVR {camera_id} {source_name}",
            device_class=None,
            state="ON",
            attributes=self._attributes(alert),
            auto_off_seconds=self._default_auto_off,
        )

    @staticmethod
    def _state_for(alert: dict[str, Any], component: str) -> str:
        """binary_sensor → ON. sensor → derive a short, displayable
        value from the alert's evidence (the most specific field
        wins). Falls back to the title."""
        if component == "binary_sensor":
            return "ON"
        evidence = alert.get("evidence") or {}
        for key in ("plate_text", "label", "summary", "value", "text"):
            val = evidence.get(key)
            if isinstance(val, (str, int, float)) and str(val).strip():
                return str(val)
        title = alert.get("title")
        return str(title) if isinstance(title, str) else "unknown"

    @staticmethod
    def _attributes(alert: dict[str, Any]) -> dict[str, Any]:
        """Pull a curated set of fields into the HA entity attributes.
        Skipping snapshot_b64 by default — it inflates the MQTT
        payload past most brokers' default limit and HA doesn't
        consume it natively. Operators who want it can build their
        own template sensor that points at evidence; we surface the
        rest of the envelope.
        """
        evidence = dict(alert.get("evidence") or {})
        # Strip the embedded snapshot — it's typically 100s of KB
        # base64; keep an event_kind/track/etc. hint instead. The
        # MQTT broker default max_packet is 256MB but many HA
        # deployments cap much lower; size matters.
        evidence.pop("snapshot_b64", None)
        return {
            "alert_id": alert.get("alert_id"),
            "fired_at": alert.get("fired_at"),
            "severity": alert.get("severity"),
            "title": alert.get("title"),
            "description": alert.get("description"),
            "camera_id": alert.get("camera_id"),
            "source": (alert.get("source") or {}).get("name"),
            "correlation_id": alert.get("correlation_id"),
            "tags": list(alert.get("tags") or []),
            "evidence": evidence,
        }


# ── Helpers ────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_COMPONENT_RE = re.compile(r"^[a-z][a-z_]*$")

# Cap on slug length. HA's object_id has no formal limit but the
# MQTT topic embeds it twice; brokers (Mosquitto default) cap topic
# length, and very long ids surface ugly in HA's UI. 64 is comfortable.
_SLUG_MAX_LEN = 64


def _slug(value: str) -> str:
    """HA object_ids accept ``[a-z0-9_]``. Lowercase, replace any
    other character with ``_``, collapse runs, strip edges, cap
    length. For empty / all-special inputs we synthesise a short
    deterministic suffix so two malformed camera ids don't collide
    onto the same entity."""
    raw = value or ""
    lowered = raw.lower()
    slugged = _SLUG_RE.sub("_", lowered).strip("_")
    if not slugged:
        # Hash the original so two different malformed ids get
        # different slugs instead of all becoming "unknown".
        return f"unknown_{abs(hash(raw)) & 0xffff:04x}"
    if len(slugged) > _SLUG_MAX_LEN:
        slugged = slugged[:_SLUG_MAX_LEN].rstrip("_") or slugged[:_SLUG_MAX_LEN]
    return slugged


def _split_entity_id(entity_id: str) -> tuple[str, str]:
    """Split ``binary_sensor.opennvr_porch_visitor`` → ``("binary_sensor",
    "opennvr_porch_visitor")``. Returns empty strings if malformed
    (bad component, missing dot, empty object_id) so callers detect
    + log + skip rather than passing garbage to HA."""
    parts = entity_id.split(".", 1)
    if len(parts) != 2:
        return "", ""
    component_raw, object_id_raw = parts
    component = component_raw.strip().lower()
    # HA component names are ``[a-z][a-z_]*`` (binary_sensor, sensor,
    # device_tracker, …). Reject anything else — caller will log.
    if not _COMPONENT_RE.match(component):
        return "", ""
    object_id = _slug(object_id_raw)
    if not object_id:
        return "", ""
    return component, object_id


def parse_overrides(raw: list[dict[str, Any]] | None) -> list[MappingOverride]:
    """Convert the ``mappings:`` config block into MappingOverride
    objects. Quietly skips malformed entries with a warning so one
    typo doesn't block the whole daemon."""
    out: list[MappingOverride] = []
    if not raw:
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("mapping entry is not a dict; skipping: %r", entry)
            continue
        source = entry.get("source")
        if not isinstance(source, str) or not source.strip():
            logger.warning("mapping entry missing source; skipping: %r", entry)
            continue
        auto_off = entry.get("auto_off_seconds")
        try:
            auto_off_int = int(auto_off) if auto_off is not None else None
        except (TypeError, ValueError):
            logger.warning(
                "mapping entry auto_off_seconds not numeric (%r); ignoring",
                auto_off,
            )
            auto_off_int = None
        out.append(MappingOverride(
            source=source.strip(),
            camera_id=(str(entry["camera_id"]) if entry.get("camera_id") else None),
            entity_id=(str(entry["entity_id"]) if entry.get("entity_id") else None),
            entity_id_template=(
                str(entry["entity_id_template"])
                if entry.get("entity_id_template") else None
            ),
            device_class=(
                str(entry["device_class"]) if entry.get("device_class") else None
            ),
            name=(str(entry["name"]) if entry.get("name") else None),
            name_template=(
                str(entry["name_template"]) if entry.get("name_template") else None
            ),
            auto_off_seconds=auto_off_int,
        ))
    return out

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
App manifests — the declarative block every app ships (App SDK spec §03/§05).

The manifest is load-bearing three ways:

* the future ``GET /manifest`` contract endpoint returns
  ``manifest.to_dict()`` so the catalog can render a card + config
  form without app-specific code;
* ``PUT /apps/{id}/config`` validates operator config against
  ``params`` without app-specific code;
* ``requires_tasks`` is checked against ``GET /api/v1/adapters`` so
  the catalog can grey out apps whose model prerequisites aren't met.

Param ``type`` accepts either a Python type (``float``, ``list``, …)
or a string for UI-schema types the catalog renders specially
(``"geometry.polygon"`` becomes a zone editor on a camera still).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _type_name(t: Any) -> str:
    """Render a Param type for the wire: Python types by name
    (``float`` → ``"float"``), strings pass through
    (``"geometry.polygon"``)."""
    if isinstance(t, type):
        return t.__name__
    return str(t)


@dataclass
class Param:
    """One typed, declarative config knob.

    ``per_camera=True`` marks params the catalog collects per camera
    (zones, tripwires) rather than once per app."""

    name: str
    type: Any
    default: Any = None
    per_camera: bool = False
    description: str = ""
    # Required params have no usable default; the future PUT /config
    # validator and the catalog form both need the distinction.
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "type": _type_name(self.type),
            "default": self.default,
            "per_camera": self.per_camera,
            "description": self.description,
        }


@dataclass
class AlertType:
    """One alert kind the app can emit — drives catalog documentation
    and downstream routing defaults."""

    name: str
    severity: str = "medium"  # low / medium / high / critical
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StateView:
    """One declarative view over the app's ``GET /state`` payload.

    The catalog renders these with ZERO app-specific UI code — the same
    bet as ``params`` → config form. An app that exposes richer live
    state (occupancy per zone, plates deduped, tracks active) declares
    how to show it instead of shipping a frontend:

    ``kind="metric"``
        A single scalar at ``path`` rendered as a stat chip
        (e.g. ``path="denylist_size"`` → "Denylist · 4").
    ``kind="table"``
        A list at ``path``; ``columns`` names the keys to show when the
        rows are dicts. A list of scalars renders as one column.

    ``path`` is a dot-path into the ``/state`` dict (``"zones"``,
    ``"counters.in"``). A missing path renders as an em-dash, never an
    error — ``/state`` is live data and may not have filled in yet.
    """

    name: str
    label: str
    kind: str = "metric"  # "metric" | "table"
    path: str = ""
    columns: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    """One operator-invokable action on the app's contract surface.

    Declared like params, rendered like params: the catalog builds a
    generic form from ``params`` and POSTs it to
    ``/actions/{name}`` on the app — proxied through the server's
    ``POST /api/v1/apps/{id}/actions/{name}``, which is **user-JWT
    only**. The governance boundary is deliberate: actions are operator
    verbs (search footage, enroll a face); the OpenNVR Agent's service
    key can read state but can NEVER invoke an action.

    ``confirm=True`` makes the catalog ask before invoking (for actions
    with side effects). The app implements the verb by overriding
    :meth:`ContractMixin.on_action`.
    """

    name: str
    label: str
    params: list[Param] = field(default_factory=list)
    description: str = ""
    confirm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "params": [p.to_dict() for p in self.params],
            "description": self.description,
            "confirm": self.confirm,
        }


@dataclass
class AppManifest:
    """The static identity + schema of one app.

    ``subscribes`` is the NATS subject pattern for InferenceSubscriber
    apps (``None`` for FrameApps that drive inference themselves).
    ``requires_tasks`` names adapter task types the app depends on,
    e.g. ``["object_detection"]``.
    """

    id: str
    name: str
    version: str
    category: str
    summary: str = ""
    requires_tasks: list[str] = field(default_factory=list)
    subscribes: str | None = None
    params: list[Param] = field(default_factory=list)
    emits: list[AlertType] = field(default_factory=list)
    # Declarative live-state views (optional) — how the catalog renders
    # this app's GET /state payload. Empty ⇒ the catalog shows raw
    # state JSON as before.
    state_schema: list[StateView] = field(default_factory=list)
    # Declarative operator actions (optional) — verbs the catalog can
    # invoke on the app's contract surface via the server's JWT-only
    # proxy. Empty ⇒ no Actions section renders.
    actions: list[Action] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The ``GET /manifest`` payload (and the ``manifest_json``
        snapshot the app registry stores)."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "summary": self.summary,
            "requires_tasks": list(self.requires_tasks),
            "subscribes": self.subscribes,
            "params": [p.to_dict() for p in self.params],
            "emits": [a.to_dict() for a in self.emits],
            "state_schema": [v.to_dict() for v in self.state_schema],
            "actions": [a.to_dict() for a in self.actions],
        }

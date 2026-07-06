# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
__APP_NAME__ — an OpenNVR Detector app scaffolded from
``templates/opennvr-app`` (see ``docs/FIRST_DETECTOR.md``).

A Detector SUBSCRIBES to KAI-C's NATS inference broadcast surface
(``opennvr.inference.*``) and consumes detection results another app is
already driving — so adapter GPU is paid once and N subscribers fan out
from one inference stream. The SDK's :class:`~opennvr_app_sdk.Detector`
base owns everything that used to be boilerplate: the NATS
subscribe/decode loop, per-message exception isolation, the
``camera_id`` / ``result.detections`` payload walk, ``completed_at``
timestamp parsing, alert dispatch, the CLI, and signal handling.

What's left for YOU to write is THE RULE — the ``on_detections`` method
below. Everything else in this file is a working, runnable skeleton.

Run::

    python __APP_MODULE__.py --config config.yml
    python __APP_MODULE__.py --config config.yml --once   # one event then exit
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opennvr_app_sdk import (
    Alert,
    AlertType,
    AppManifest,
    Detector,
    Param,
    app,
)
from opennvr_app_sdk.config import load_yaml

logger = logging.getLogger("__APP_ID__")


# ── Manifest ───────────────────────────────────────────────────────
#
# The manifest is your app's declarative identity. It's load-bearing:
# the catalog renders a config form from ``params``, greys the app out
# unless an installed adapter advertises every name in ``requires_tasks``,
# and documents ``emits``. The App Store index entry mirrors these
# fields (see docs/CONTRIBUTING_APPS.md), so decide them here first.
MANIFEST = AppManifest(
    id="__APP_ID__",
    name="__APP_NAME__",
    version="0.1.0",
    category="analytics",  # perimeter | analytics | vehicle | doorstep | forensics | integration
    summary="Fires an alert when __APP_NAME__ sees a watched label.",
    # Adapter task types this app rides. The catalog greys the app out
    # unless an installed adapter advertises this task. See
    # docs/AI_ADAPTER_CONTRACT.md §4 for the vocabulary.
    requires_tasks=["__TASK__"],
    subscribes="opennvr.inference.>",
    params=[
        # Every knob an operator can turn shows up as a form field in the
        # catalog. Add your own as the rule grows.
        Param("watch_labels", list, default=["person"],
              description="Detection labels that count toward the rule."),
    ],
    emits=[AlertType("__APP_ID__", severity="medium")],
)


# ── Config ─────────────────────────────────────────────────────────
#
# The SDK loads + shape-checks the YAML; each app keeps its own typed
# parse because config semantics (which keys, their defaults, their
# validation messages) are app business logic your tests pin down.


@dataclass
class AppConfig:
    """Top-level config loaded from YAML.

    The three NATS fields are what the SDK's subscribe loop reads
    (``nats_url``, ``nats_token``, ``subject_pattern``); the rest is
    this app's own business config. ``webhook_url`` / ``nats_alerts_*``
    are optional alert fan-out the SDK dispatcher picks up if present.
    """

    nats_url: str
    subject_pattern: str
    watch_labels: list[str]
    nats_token: str | None = None
    webhook_url: str | None = None
    nats_alerts_url: str | None = None
    nats_alerts_token: str | None = None
    nats_alerts_subject_prefix: str = "opennvr.alerts"

    # App contract (spec §03) — all optional. ``contract_port`` serves
    # /health /manifest /state; ``opennvr_url`` triggers registry
    # self-registration on boot so the app shows up in the catalog.
    contract_port: int | None = None
    contract_bind_host: str | None = None
    contract_host: str | None = None
    opennvr_url: str | None = None
    opennvr_token: str | None = None


def load_config(path: str) -> AppConfig:
    """Parse a YAML config file into a typed :class:`AppConfig`.

    Raises ``ValueError`` on malformed config — the SDK runner catches
    it, prints a useful operator message, and exits non-zero."""
    raw = load_yaml(path)

    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required")

    if "subject_pattern" in raw:
        subject = str(raw.get("subject_pattern") or "").strip()
        if not subject:
            raise ValueError("config: 'subject_pattern' must not be empty")
    else:
        subject = "opennvr.inference.>"

    # ``watch_labels`` defaults to ["person"] when absent, but reject an
    # explicit empty list rather than build a rule that never matches.
    watch_labels_raw = raw.get("watch_labels")
    if watch_labels_raw is None:
        watch_labels = ["person"]
    else:
        watch_labels = [str(s).lower() for s in watch_labels_raw]
        if not watch_labels:
            raise ValueError(
                "config: 'watch_labels' must not be empty (omit the key to "
                "use the default ['person'], or list at least one label)"
            )

    return AppConfig(
        nats_url=nats_url,
        subject_pattern=subject,
        watch_labels=watch_labels,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        webhook_url=str(raw["webhook_url"]) if raw.get("webhook_url") else None,
        nats_alerts_url=str(raw["nats_alerts_url"]) if raw.get("nats_alerts_url") else None,
        nats_alerts_token=str(raw["nats_alerts_token"]) if raw.get("nats_alerts_token") else None,
        nats_alerts_subject_prefix=str(
            raw.get("nats_alerts_subject_prefix") or "opennvr.alerts"
        ),
        contract_port=(
            int(raw["contract_port"]) if raw.get("contract_port") is not None else None
        ),
        contract_bind_host=raw.get("contract_bind_host"),
        contract_host=raw.get("contract_host"),
        opennvr_url=raw.get("opennvr_url"),
        opennvr_token=raw.get("opennvr_token"),
    )


# ── The rule ───────────────────────────────────────────────────────


class __APP_CLASS__(Detector):
    """Consumes ``opennvr.inference.{adapter}.{camera_id}.completed``
    events (via the SDK's Detector loop) and decides what to alert on.

    Subclass responsibilities: a class-level ``manifest``, an optional
    :meth:`setup` to allocate state, and :meth:`on_detections` — the rule.
    """

    manifest = MANIFEST

    def setup(self) -> None:
        """Optional hook — allocate per-app state here. Runs once at
        construction, after ``self.cfg`` is set.

        Stateful rules (dwell timers, counters, per-track tracking) use
        the SDK's ``keyed_state`` — see the loitering-detection example.
        The starter rule below is stateless, so there's nothing to set up.
        """

    def on_detections(
        self,
        camera_id: str,
        detections: list[dict[str, Any]],
        event: dict[str, Any],
    ) -> list[Alert]:
        """THE RULE — this is the one method you fill in.

        Called once per decoded inference event that has a ``camera_id``
        and a ``result.detections`` list. Return the :class:`Alert`
        objects to fire (the SDK base dispatches them to stdout + any
        configured webhook / NATS channel). Return ``[]`` to stay quiet.

        This method is pure w.r.t. the event: the tests drive it
        directly without a NATS broker (see ``tests/test_smoke.py``).

        ── STARTER RULE ──────────────────────────────────────────────
        Fire one alert the first time a watched label appears in a
        frame. It's deliberately trivial — REPLACE the body with your
        real predicate: a zone check (``opennvr_app_sdk.geometry.Zone``),
        a dwell timer (``opennvr_app_sdk.state.keyed_state``), a
        confidence gate, a time-of-day window, whatever your app needs.
        """
        # Each detection is a §5.1 dict: {"label", "confidence", "bbox":
        # {"x","y","w","h"}, "track_id", "attributes"}. bbox coords are
        # normalized (0..1). Filter to the labels this app watches.
        matches = [
            det
            for det in detections
            if isinstance(det, dict)
            and str(det.get("label", "")).lower() in self.cfg.watch_labels
        ]
        if not matches:
            return []

        label = str(matches[0].get("label", "")).lower()
        # TODO: put YOUR rule here. This starter alerts on any sighting.
        return [self._build_alert(camera_id=camera_id, label=label, event=event)]

    def _build_alert(
        self,
        *,
        camera_id: str,
        label: str,
        event: dict[str, Any],
    ) -> Alert:
        """Build the §11.5-shaped alert. Override in a subclass to enrich
        the payload (snapshot URL, evidence bundle, etc.)."""
        return Alert(
            title=f"{label.capitalize()} seen on {camera_id}",
            description=(
                f"__APP_NAME__ observed a {label} on camera {camera_id}."
            ),
            camera_id=camera_id,
            severity="medium",
            correlation_id=str(event.get("correlation_id") or ""),
            evidence={
                "label": label,
                "adapter": event.get("adapter"),
                "adapter_version": event.get("adapter_version"),
                "model_fingerprint": event.get("model_fingerprint"),
            },
            tags=["__APP_ID__", label],
        )


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. The SDK runner owns argparse,
    logging, signals, and the alert dispatcher."""
    return app(__APP_CLASS__, load_config=load_config).run(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compatibility shim — the implementation moved to ``opennvr_app_sdk.alerts``.

This file used to hold the full §11.5 alert stack (copied verbatim
from ``examples/intrusion-detection/alerts.py`` per the old
copy-as-template model). Per §08 step 5 of the App SDK spec, that
canonical implementation now lives in the ``opennvr-app-sdk`` package;
this module re-exports it so existing imports (``from alerts import
Alert, ...``) keep working unchanged.

Two local touches survive the move:

* the ``AlertSource.name`` default of ``"package-delivery"`` — the SDK
  replaces the old hardcoded per-copy default with a process-wide
  setting, asserted here (and re-asserted from the manifest by the
  ``FrameApp`` base at construction time);
* ``AlertDispatcher.dispatch`` — this example historically called the
  dispatcher as ``dispatcher.dispatch(alert)`` (the SDK canonical name
  is ``fire``). The subclass below keeps the alias so the orchestrator
  and its tests stay source-compatible.
"""
from __future__ import annotations

# ``httpx`` is re-exported as a module attribute on purpose: tests (and
# any downstream copy of them) monkeypatch ``alerts.httpx.post``.
import httpx  # noqa: F401

from opennvr_app_sdk.alerts import (  # noqa: F401
    DEFAULT_ALERT_SUBJECT_PREFIX,
    Alert,
    AlertChannel,
    AlertSource,
    NatsAlertChannel,
    StdoutChannel,
    WebhookChannel,
    alert_subject,
    get_default_source,
    set_default_source,
)
from opennvr_app_sdk.alerts import AlertDispatcher as _SdkAlertDispatcher


class AlertDispatcher(_SdkAlertDispatcher):
    """The SDK dispatcher plus this example's historical method name.

    ``dispatch`` and ``fire`` are the same call — kept as an alias so
    ``package_delivery.py`` (and any operator forks of it) don't need
    a rename to ride the SDK."""

    dispatch = _SdkAlertDispatcher.fire


def build_dispatcher(
    *,
    webhook_url: str | None,
    nats_alerts_url: str | None = None,
    nats_alerts_token: str | None = None,
    nats_alerts_subject_prefix: str = DEFAULT_ALERT_SUBJECT_PREFIX,
) -> AlertDispatcher:
    """Same construction rules as the SDK factory (stdout always;
    webhook / NATS opt-in via config), returning the local subclass so
    the ``dispatch`` alias is available."""
    channels: list[AlertChannel] = [StdoutChannel()]
    if webhook_url:
        channels.append(WebhookChannel(webhook_url))
    if nats_alerts_url:
        channels.append(
            NatsAlertChannel(
                nats_alerts_url,
                token=nats_alerts_token,
                subject_prefix=nats_alerts_subject_prefix,
            )
        )
    return AlertDispatcher(channels)


# This process is the package-delivery app — see module docstring.
set_default_source(kind="app", name="package-delivery", version="1.0.0")

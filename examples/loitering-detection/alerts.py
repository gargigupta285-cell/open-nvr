# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compatibility shim — the implementation moved to ``opennvr_app_sdk.alerts``.

This file used to hold the full §11.5 alert stack (copied verbatim
from ``examples/intrusion-detection/alerts.py`` per the old
copy-as-template model). Per §08 step 1 of the App SDK spec, that
canonical implementation now lives in the ``opennvr-app-sdk`` package;
this module re-exports it so existing imports (``from alerts import
Alert, ...``) keep working unchanged.

The one thing the old copy carried that a shared library can't: the
``AlertSource.name`` default of ``"loitering-detection"``. The SDK
replaces the hardcoded default with a process-wide setting; declaring
it here keeps this process's alerts stamped with the right §11.5
source identity (the ``Detector`` base re-asserts it from the manifest
at construction time).
"""
from __future__ import annotations

# ``httpx`` is re-exported as a module attribute on purpose: tests (and
# any downstream copy of them) monkeypatch ``alerts.httpx.post``.
import httpx  # noqa: F401

from opennvr_app_sdk.alerts import (  # noqa: F401
    DEFAULT_ALERT_SUBJECT_PREFIX,
    Alert,
    AlertChannel,
    AlertDispatcher,
    AlertSource,
    NatsAlertChannel,
    StdoutChannel,
    WebhookChannel,
    alert_subject,
    build_dispatcher,
    get_default_source,
    set_default_source,
)

# This process is the loitering-detection app — see module docstring.
set_default_source(kind="app", name="loitering-detection", version="1.0.0")

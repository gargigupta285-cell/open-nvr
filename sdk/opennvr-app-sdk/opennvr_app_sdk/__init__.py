# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
opennvr-app-sdk — the shared base for OpenNVR monitoring apps.

Per the App SDK spec, the SDK folds config loading, §11.5 alert
dispatch, zone geometry, keyed TTL state, the NATS subscribe loop, the
CLI, and signal handling behind ``app(Detector).run()`` — what's left
in an app is the rule plus a declarative :class:`AppManifest`.

Archetypes (spec §02):

* :class:`Detector` — subscribes to ``opennvr.inference.*`` events
  another app is already driving (loitering, counting, dashboards).
* :class:`FrameApp` — drives inference itself by polling frames into
  KAI-C (intrusion, LPR, package delivery).
* AlertSubscriber — consumes ``opennvr.alerts.*`` (HA relay, SIEM
  bridges); lands with a later rollout step.

Apache-2.0, unlike the AGPL example apps — the SDK is meant to be
embedded in third-party apps the same way ``opennvr-adapter-sdk`` is.
"""
from .alerts import (
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
    set_default_source,
)
from .config import load_yaml, require
from .contract import ContractServer
from .detector import AppRunner, Detector, app
from .frame_app import FrameApp, FrameSource, KaiCClient, KaiCError
from .frame_sources import (
    CameraFrameSource,
    DictFrameSource,
    FileFrameSource,
    FrameSourceError,
    HttpSnapshotSource,
    build_frame_source,
    dict_frame_source,
)
from .geometry import Point, Zone, bbox_center
from .manifest import AlertType, AppManifest, Param
from .state import KeyedState, StateRecord, keyed_state

__version__ = "0.1.0"

__all__ = [
    # Archetype bases + runner
    "Detector",
    "FrameApp",
    "AppRunner",
    "app",
    # Alerts (§11.5)
    "Alert",
    "AlertSource",
    "AlertChannel",
    "AlertDispatcher",
    "StdoutChannel",
    "WebhookChannel",
    "NatsAlertChannel",
    "alert_subject",
    "build_dispatcher",
    "set_default_source",
    "DEFAULT_ALERT_SUBJECT_PREFIX",
    # Manifest
    "AppManifest",
    "Param",
    "AlertType",
    # Keyed TTL state
    "keyed_state",
    "KeyedState",
    "StateRecord",
    # Geometry
    "Point",
    "Zone",
    "bbox_center",
    # Config helpers
    "load_yaml",
    "require",
    # Frame-app plumbing
    "FrameSource",
    "KaiCClient",
    "KaiCError",
    # Per-camera frame sources
    "CameraFrameSource",
    "FileFrameSource",
    "HttpSnapshotSource",
    "FrameSourceError",
    "build_frame_source",
    "DictFrameSource",
    "dict_frame_source",
    # Contract surface (§03)
    "ContractServer",
]

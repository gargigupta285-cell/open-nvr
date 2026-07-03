# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compatibility shim — the implementation moved to
``opennvr_app_sdk.frame_sources``.

This file used to hold the file:// / http(s):// snapshot frame sources
and their scheme factory. Per §08 of the App SDK spec, the canonical
implementation now lives in the ``opennvr-app-sdk`` package; this
module re-exports it so existing imports (``from frame_sources import
FrameSource, build_frame_source, ...``) keep working unchanged.

Naming note: this example's ``FrameSource`` protocol is the *bound*,
per-camera shape (``fetch()`` + ``camera_id``) — the SDK calls that
:class:`~opennvr_app_sdk.frame_sources.CameraFrameSource` to keep it
distinct from the ``FrameApp`` poll loop's routing protocol
(``get_frame(camera_id)``). The alias below preserves this module's
historical name.
"""
from __future__ import annotations

# ``httpx`` is re-exported as a module attribute on purpose, matching
# the old copy — the tests monkeypatch ``frame_sources.httpx.get``.
import httpx  # noqa: F401

from opennvr_app_sdk.frame_sources import (  # noqa: F401
    CameraFrameSource,
    FileFrameSource,
    FrameSourceError,
    HttpSnapshotSource,
    build_frame_source,
)

# Historical name for the per-camera protocol (see module docstring).
FrameSource = CameraFrameSource

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compatibility shim — the implementation moved to ``opennvr_app_sdk.geometry``.

This file used to hold the directional line-crossing geometry (the
oriented ``Tripwire`` segment, the side/crossing predicates, and the
segment-intersection test) as self-contained copy-as-template code.
Per §08 of the App SDK spec, the canonical implementation now lives in
the ``opennvr-app-sdk`` package alongside the polygon ``Zone``; this
module re-exports it so existing imports (``from line import Point,
Tripwire, ...``) keep working unchanged.
"""
from __future__ import annotations

from opennvr_app_sdk.geometry import (  # noqa: F401
    Point,
    Tripwire,
    bbox_center,
)

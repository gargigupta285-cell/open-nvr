# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compatibility shim — the implementation moved to ``opennvr_app_sdk.geometry``.

This file used to hold the ray-casting point-in-polygon zone math (the
original copy the other zone-shaped examples cloned per the old
copy-as-template model). Per §08 step 1 of the App SDK spec, the
canonical implementation now lives in the ``opennvr-app-sdk`` package;
this module re-exports it so existing imports (``from zone import
Zone, ...``) keep working unchanged.
"""
from __future__ import annotations

from opennvr_app_sdk.geometry import (  # noqa: F401
    Point,
    Zone,
    bbox_center,
    scale_vertices,
)

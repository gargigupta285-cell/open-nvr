# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: Apache-2.0

"""
Generic YAML config helpers.

Deliberately thin: the SDK loads and shape-checks the YAML document;
each app keeps its own typed parse (``load_config(path) -> AppConfig``)
because config semantics — which keys exist, their defaults, their
validation messages — are app business logic that the app's own tests
pin down. Once manifests drive config (spec §05), ``manifest.params``
becomes the validator and these helpers stay as the file-loading edge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read + parse a YAML config file, requiring a mapping at the root.

    Raises ``ValueError`` on a non-mapping root and lets ``OSError``
    from the read propagate — callers surface both as operator-facing
    config errors and exit non-zero."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {str(path)!r}: root must be a mapping")
    return raw


def require(cfg: dict[str, Any], key: str, *, path: str = "config") -> Any:
    """Fetch a required config value, rejecting missing / empty values.

    ``path`` names the config source in the error message (file path or
    a nested-section breadcrumb like ``"config: cameras[0]"``)."""
    value = cfg.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{path}: {key!r} is required")
    return value

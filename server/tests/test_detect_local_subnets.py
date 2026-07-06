# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""
Tests for detect_local_subnets() — the multi-NIC auto-detection used by ONVIF
discovery when no Camera LAN subnet is configured.

    cd server && pytest tests/test_detect_local_subnets.py -v
"""

from __future__ import annotations

import os
import secrets
import sys
import types as _types
from pathlib import Path

from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("MEDIAMTX_SECRET", secrets.token_hex(32))
os.environ.setdefault("INTERNAL_API_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

_lm = _types.ModuleType("core.logging_config")


class _L:
    def __getattr__(self, _n):
        return lambda *a, **k: None


_lm.__getattr__ = lambda _n: _L()
_lm.setup_logging = lambda *a, **k: None
sys.modules.setdefault("core.logging_config", _lm)

import routers.network as net  # noqa: E402


def _stub_hostname_ips(monkeypatch, ips):
    """Make getaddrinfo(hostname) return the given IPv4 addresses, and force the
    default-route probe to fail so the test controls the full input set."""
    def boom(*a, **k):
        raise OSError("no default route")

    monkeypatch.setattr(net.socket, "socket", boom)
    monkeypatch.setattr(net.socket, "gethostname", lambda: "testhost")
    monkeypatch.setattr(
        net.socket,
        "getaddrinfo",
        lambda host, port, family: [(None, None, None, None, (ip, 0)) for ip in ips],
    )


def test_detects_all_private_subnets_across_nics(monkeypatch):
    # A multi-NIC host: camera LAN, default/uplink LAN, and a second camera-LAN IP.
    _stub_hostname_ips(
        monkeypatch,
        ["192.168.1.10", "192.168.1.44", "192.168.31.63", "10.20.0.5"],
    )
    subs = net.detect_local_subnets()
    assert "192.168.1.0/24" in subs  # the camera's subnet, NOT on the default route
    assert "192.168.31.0/24" in subs
    assert "10.20.0.0/24" in subs
    # two IPs on 192.168.1.x collapse to a single /24
    assert subs.count("192.168.1.0/24") == 1


def test_filters_out_loopback_linklocal_and_public(monkeypatch):
    # 127.x loopback, 169.254.x link-local, and 8.8.8.8 (globally routable) all drop.
    _stub_hostname_ips(
        monkeypatch,
        ["127.0.0.1", "169.254.83.107", "8.8.8.8", "192.168.1.10"],
    )
    subs = net.detect_local_subnets()
    assert subs == ["192.168.1.0/24"]  # only the private LAN survives


def test_returns_empty_when_nothing_detectable(monkeypatch):
    _stub_hostname_ips(monkeypatch, [])
    assert net.detect_local_subnets() == []

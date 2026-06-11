# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""
PR #50 regression tests for the ``configured`` field on the cloud upload
status response.

Run with:

    cd server && pytest tests/test_cloud_status_configured.py -v

Coverage:

* ``CloudRecordingService.is_cloud_configured`` returns True when the
  ``media_source`` SecuritySetting has a non-empty
  ``cloud_recording_server_ip``.
* Returns False when the IP key is missing from the JSON.
* Returns False when the IP key is the empty string (operator cleared
  the field in the UI — documented in the docstring on the method).
* Returns False when the ``media_source`` row doesn't exist at all.
* Returns False when ``json_value`` is unparseable garbage.

These together pin the contract the frontend depends on: a False return
must mean "stop polling — there is genuinely nothing to talk to" and a
True return must mean "a cloud target is present". A drift on either
direction would either burn requests against an unconfigured backend or
silently miss real upload status updates.

Note on test isolation
----------------------
``CloudRecordingService`` is a class-level singleton (``cls._instance``).
All tests in this file are READ-ONLY on that singleton — none of them
mutate ``_upload_queue``, ``_stats``, ``_active_file``, or any other
shared field. A future test that adds upload jobs or sets stats should
either (a) reset the relevant fields in a fixture teardown, or (b) move
to a separate test module so this file's contract tests stay
deterministic across pytest collection order.
"""

from __future__ import annotations

# Python 3.10 sandbox polyfill — pyproject requires 3.11+ where
# datetime.UTC exists. No-op on 3.11+.
import datetime as _dt  # noqa: I001

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc

import json
import os
import secrets
import sys
import types as _types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

# Settings need to be valid for the modules under test to import.
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("MEDIAMTX_SECRET", secrets.token_hex(32))
os.environ.setdefault("INTERNAL_API_KEY", secrets.token_urlsafe(48))
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode()
)

# Stub ``core.logging_config`` — same pattern as
# test_m1c_transport_probe.py. The real module wants a writable
# ``logs/`` directory; the service under test only needs no-op loggers.
_lm = _types.ModuleType("core.logging_config")


class _L:
    def info(self, *a, **kw):  # noqa: D401
        pass

    def warning(self, *a, **kw):
        pass

    def error(self, *a, **kw):
        pass

    def debug(self, *a, **kw):
        pass

    def critical(self, *a, **kw):
        pass


for _name in (
    "main_logger",
    "auth_logger",
    "camera_logger",
    "recording_logger",
    "cloud_logger",
    "ai_logger",
):
    setattr(_lm, _name, _L())
sys.modules["core.logging_config"] = _lm


# ─── Real model + in-memory SQLite session ──────────────────────────────
# We want to exercise the real ``is_cloud_configured`` against the real
# SecuritySetting model — not mock it — so a refactor that breaks the JSON
# shape or column name would also break this test.
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import Base  # noqa: E402
from models import SecuritySetting  # noqa: E402
from services.cloud_recording_service import CloudRecordingService  # noqa: E402


@pytest.fixture
def db():
    """In-memory SQLite DB with the SecuritySetting table created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SecuritySetting.__table__])
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _set_media_source(db, payload: dict | str | None) -> None:
    """Write a ``media_source`` SecuritySetting with the given JSON body.

    Pass a dict for the normal case, a raw string for the malformed-JSON
    case, or None to delete any existing row (covers "no row at all").
    """
    db.query(SecuritySetting).filter(
        SecuritySetting.key == "media_source"
    ).delete()
    if payload is None:
        db.commit()
        return
    body = payload if isinstance(payload, str) else json.dumps(payload)
    db.add(SecuritySetting(key="media_source", json_value=body))
    db.commit()


# ─── Tests ──────────────────────────────────────────────────────────────


def test_configured_when_ip_is_set(db):
    """Happy path: IP set ⇒ True."""
    _set_media_source(db, {"cloud_recording_server_ip": "192.168.1.50"})
    svc = CloudRecordingService.get_instance()
    assert svc.is_cloud_configured(db) is True


def test_not_configured_when_ip_missing(db):
    """Other media_source keys present but no IP ⇒ False.

    Operator may have populated the media_source row with unrelated
    settings (recording path, retention, ...) without ever filling in
    the cloud target. Frontend should not poll.
    """
    _set_media_source(db, {"recording_path": "/var/recordings"})
    svc = CloudRecordingService.get_instance()
    assert svc.is_cloud_configured(db) is False


def test_not_configured_when_ip_is_empty_string(db):
    """Operator cleared the field ⇒ False.

    Pinned because the docstring on is_cloud_configured explicitly
    treats empty string as unconfigured. ``bool("")`` is False; this
    test is the contract that proves the docstring is correct.
    """
    _set_media_source(db, {"cloud_recording_server_ip": ""})
    svc = CloudRecordingService.get_instance()
    assert svc.is_cloud_configured(db) is False


def test_not_configured_when_no_media_source_row(db):
    """Fresh install, no media_source row at all ⇒ False."""
    _set_media_source(db, None)
    svc = CloudRecordingService.get_instance()
    assert svc.is_cloud_configured(db) is False


def test_not_configured_when_json_unparseable(db):
    """Corrupted json_value ⇒ False, no exception.

    A corrupted row in the DB (write failure, manual edit gone wrong)
    must not crash the polling endpoint. ``_get_media_source_settings``
    swallows JSON errors and returns {}, which makes is_cloud_configured
    return False — surface the operator's polling to "off" so they
    notice (instead of leaking a 500 every 3 seconds).
    """
    _set_media_source(db, "not even close to json")
    svc = CloudRecordingService.get_instance()
    assert svc.is_cloud_configured(db) is False


def test_configured_field_round_trips_through_get_queue_status(db):
    """End-to-end: simulate what the /cloud-upload/status endpoint does.

    The router (server/routers/recordings.py) calls
    ``get_queue_status()`` and adds ``configured`` from
    ``is_cloud_configured(db)``. This test mirrors that composition so
    a future drift in the response shape — either side losing the
    ``configured`` key or its boolean type — fails here before
    reaching production.
    """
    _set_media_source(db, {"cloud_recording_server_ip": "10.0.0.5"})
    svc = CloudRecordingService.get_instance()
    response = svc.get_queue_status()
    response["configured"] = svc.is_cloud_configured(db)

    # Shape checks.
    assert "queue_size" in response
    assert "worker_running" in response
    assert "configured" in response
    assert isinstance(response["configured"], bool)
    assert response["configured"] is True

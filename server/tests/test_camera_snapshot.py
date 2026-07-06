# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""GET /cameras/{id}/snapshot — the geometry editors' backdrop.

Focused router tests with an in-memory DB and a stubbed KAI-C capture
service (no OpenCV, no RTSP): happy JPEG path, ownership 404, offline
503, and the no-store cache header the editor relies on.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("INTERNAL_API_KEY", secrets.token_urlsafe(48))
try:
    from cryptography.fernet import Fernet
    os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
except Exception:
    pass
os.environ.setdefault("MEDIAMTX_SECRET", secrets.token_hex(32))
# postgres-shaped URL keeps core.database's pooled engine import-time
# happy (same convention as the sibling test modules); the test itself
# uses its own in-memory sqlite session below.
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from core.auth import get_current_active_user  # noqa: E402
from core.database import Base, get_db  # noqa: E402
from models import Camera, Role, User  # noqa: E402
from routers import cameras as cameras_router  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegbytes" * 4


class _StubKaiC:
    def __init__(self, jpeg):
        self._jpeg = jpeg
        self.calls: list[tuple[str, int]] = []

    async def capture_frame_bytes(self, rtsp_url, camera_id):
        self.calls.append((rtsp_url, camera_id))
        return self._jpeg


def _make_app(jpeg=JPEG, *, superuser=True, owner_id=1):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[Role.__table__, User.__table__, Camera.__table__]
    )
    session_factory = sessionmaker(bind=engine)

    s = session_factory()
    s.add(Role(id=1, name="admin"))
    s.flush()
    s.add(User(id=1, username="op", email="op@x", hashed_password="x",
               is_active=True, is_superuser=superuser, role_id=1))
    s.add(Camera(id=7, name="Gate", ip_address="192.168.1.10",
                 rtsp_url="rtsp://gate/stream", owner_id=owner_id))
    s.add(Camera(id=8, name="NoStream", ip_address="192.168.1.11",
                 rtsp_url=None, owner_id=owner_id))
    s.commit()

    app = FastAPI()
    app.include_router(cameras_router.router)

    def _db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    user = s.query(User).first()
    s.close()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user

    stub = _StubKaiC(jpeg)
    return app, stub, engine


@pytest.fixture
def snap(monkeypatch):
    app, stub, engine = _make_app()
    import services.kai_c_service as kmod

    monkeypatch.setattr(kmod, "get_kai_c_service", lambda: stub)
    with TestClient(app) as tc:
        yield tc, stub
    engine.dispose()


def test_snapshot_returns_fresh_jpeg(snap):
    tc, stub = snap
    resp = tc.get("/cameras/7/snapshot")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"  # editors want NOW
    assert resp.content == JPEG
    assert stub.calls == [("rtsp://gate/stream", 7)]


def test_snapshot_unknown_camera_404(snap):
    tc, _ = snap
    assert tc.get("/cameras/999/snapshot").status_code == 404


def test_snapshot_no_stream_url_503(snap):
    tc, stub = snap
    resp = tc.get("/cameras/8/snapshot")
    assert resp.status_code == 503
    assert stub.calls == []  # never tried to capture


def test_snapshot_capture_failure_503(monkeypatch):
    app, stub, engine = _make_app(jpeg=None)
    import services.kai_c_service as kmod

    monkeypatch.setattr(kmod, "get_kai_c_service", lambda: stub)
    with TestClient(app) as tc:
        resp = tc.get("/cameras/7/snapshot")
    engine.dispose()
    assert resp.status_code == 503


def test_snapshot_not_owner_403(monkeypatch):
    """Non-superuser caller who doesn't own the camera: 403, matching
    CameraService.get_camera_by_id's established semantics across the
    camera routes."""
    app, stub, engine = _make_app(superuser=False, owner_id=42)
    import services.kai_c_service as kmod

    monkeypatch.setattr(kmod, "get_kai_c_service", lambda: stub)
    with TestClient(app) as tc:
        resp = tc.get("/cameras/7/snapshot")
    engine.dispose()
    assert resp.status_code == 403
    assert stub.calls == []

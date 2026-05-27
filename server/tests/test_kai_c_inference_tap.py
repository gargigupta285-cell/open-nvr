# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""
Unit tests for the KAI-C inference fast-path (MediaMTX loopback tap).

These tests pin the contract of three pieces:

* ``_resolve_inference_rtsp_url`` — when does the resolver return a
  MediaMTX tap URL, when does it fall back to the camera's raw URL?
* ``_get_inference_mediamtx_jwt`` — does the cache hit, expire, and
  recover from a mint failure correctly?
* ``capture_frame_from_rtsp`` — does a capture failure under the tap
  invalidate the cached JWT so the next call re-mints?

The cv2-driven frame capture itself is NOT exercised (it would need
either a live RTSP source or a heavy mock of OpenCV). What we cover is
the URL routing + JWT lifecycle that surrounds it — the part the
optimization actually changes.

Run with:

    cd server && pytest tests/test_kai_c_inference_tap.py -v
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_SERVER = Path(__file__).resolve().parent.parent
if str(REPO_SERVER) not in sys.path:
    sys.path.insert(0, str(REPO_SERVER))

# ────────────────────────────────────────────────────────────────────
# Test-environment setup (must run BEFORE any ``from core...`` import)
# ────────────────────────────────────────────────────────────────────
#
# core.config.Settings refuses to load without the four required
# secrets — for good reason (the strong-secret validator is part of
# V-018), but it blocks unit tests that don't actually USE those
# values. Set throwaway placeholders here so the import succeeds; the
# tests don't validate any secret-derived behaviour.
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("MEDIAMTX_SECRET", "x" * 64)
# CREDENTIAL_ENCRYPTION_KEY is a Fernet key — must be 32 url-safe base64
# bytes. Generate one on the fly so we don't ship a hardcoded "test
# fixture key" that linters might flag as a leaked secret.
try:
    from cryptography.fernet import Fernet  # noqa: E402

    os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
except ImportError:  # pragma: no cover — cryptography is a hard dep
    pass
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

# mediamtx_jwt_service.py uses ``from datetime import UTC`` which lands
# in Python 3.11+. The project's runtime IS 3.11+, but CI sandboxes
# sometimes run on 3.10. We inject a stub module into sys.modules so
# tests that mock ``services.mediamtx_jwt_service.MediaMtxJwtService``
# don't fail at import time on 3.10. The stub mirrors only the surface
# the production code calls (``create_stream_token``).
if "services.mediamtx_jwt_service" not in sys.modules:
    try:  # If real import works (Python 3.11+), prefer the real module.
        import services.mediamtx_jwt_service  # noqa: F401
    except ImportError:
        _stub = types.ModuleType("services.mediamtx_jwt_service")

        class _StubJwtService:  # noqa: D401 — test stub
            @staticmethod
            def create_stream_token(**_kwargs):  # signature matches real one
                return "stub-token"

        _stub.MediaMtxJwtService = _StubJwtService  # type: ignore[attr-defined]
        sys.modules["services.mediamtx_jwt_service"] = _stub
        # unittest.mock.patch("services.mediamtx_jwt_service.X") looks up
        # the attribute via ``getattr(services, "mediamtx_jwt_service")``.
        # sys.modules registration alone isn't enough — also attach to
        # the parent ``services`` package so attribute lookup works.
        import services as _services_pkg  # noqa: E402

        _services_pkg.mediamtx_jwt_service = _stub  # type: ignore[attr-defined]


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def kai_c_service():
    """A fresh KaiCService with cv2 / network dependencies stubbed.

    KaiCService.__init__ creates an httpx.AsyncClient and a thread pool;
    neither is touched by the methods we test. We mock httpx.AsyncClient
    so the constructor succeeds even in CI/sandbox environments where
    weird proxy env vars (e.g. ``ALL_PROXY=socks5h://localhost:1080``)
    would otherwise break the real client init.
    """
    from unittest.mock import MagicMock

    with patch("services.kai_c_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value = MagicMock()
        from services.kai_c_service import KaiCService

        svc = KaiCService(kai_c_url="http://localhost:8100")
        yield svc


@pytest.fixture
def fake_settings():
    """Returns an object that quacks like core.config.settings with the
    four fields the resolver reads. Tests mutate this in-place and the
    patch below routes the resolver at it."""

    class _FakeSettings:
        inference_use_mediamtx_tap: bool = True
        mediamtx_rtsp_url: str | None = "rtsp://mediamtx:8554"
        mediamtx_stream_prefix: str | None = "cam-"
        mediamtx_path_mode: str = "id"  # default; peer-review #1

    return _FakeSettings()


@pytest.fixture
def patched_settings(fake_settings):
    """Patch core.config.settings inside the resolver's import scope.

    The resolver does a late import (``from core.config import
    settings``) every call, so we patch the attribute on the
    ``core.config`` module — that's the binding the late import
    resolves to.
    """
    with patch("core.config.settings", fake_settings):
        yield fake_settings


# ────────────────────────────────────────────────────────────────────
# _resolve_inference_rtsp_url
# ────────────────────────────────────────────────────────────────────


class TestResolveInferenceRtspUrl:
    """The URL resolver is the choke point for the optimization. If it
    routes wrong, either inference fails (returns an unreachable URL)
    or the optimization silently doesn't apply (returns the camera
    URL when it should return the tap)."""

    def test_tap_enabled_with_url_configured_returns_tap_url(
        self, kai_c_service, patched_settings
    ):
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value="TESTTOKEN"):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=42, fallback_url="rtsp://192.168.1.50/stream1"
            )
        assert url.startswith("rtsp://mediamtx:8554/cam-42?jwt=")
        assert "TESTTOKEN" in url
        # The fallback URL must not appear in the result when the tap is active.
        assert "192.168.1.50" not in url

    def test_tap_disabled_returns_fallback(
        self, kai_c_service, patched_settings
    ):
        patched_settings.inference_use_mediamtx_tap = False
        url = kai_c_service._resolve_inference_rtsp_url(
            camera_id=42, fallback_url="rtsp://192.168.1.50/stream1"
        )
        assert url == "rtsp://192.168.1.50/stream1"

    def test_no_mediamtx_url_configured_returns_fallback(
        self, kai_c_service, patched_settings
    ):
        patched_settings.mediamtx_rtsp_url = None
        url = kai_c_service._resolve_inference_rtsp_url(
            camera_id=42, fallback_url="rtsp://192.168.1.50/stream1"
        )
        assert url == "rtsp://192.168.1.50/stream1"

    def test_jwt_mint_failure_returns_fallback(
        self, kai_c_service, patched_settings
    ):
        """If MediaMtxJwtService can't mint (missing keys, crypto error),
        the resolver MUST degrade to the camera URL — handing back a
        tap URL without a token would just produce a 401."""
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value=None):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=42, fallback_url="rtsp://192.168.1.50/stream1"
            )
        assert url == "rtsp://192.168.1.50/stream1"

    def test_custom_stream_prefix_honored(self, kai_c_service, patched_settings):
        patched_settings.mediamtx_stream_prefix = "site-a/cam-"
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value="T"):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=7, fallback_url="rtsp://x"
            )
        assert "/site-a/cam-7?" in url

    def test_default_stream_prefix_when_unset(self, kai_c_service, patched_settings):
        patched_settings.mediamtx_stream_prefix = None
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value="T"):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=7, fallback_url="rtsp://x"
            )
        # Resolver's documented fallback default is "cam-".
        assert "/cam-7?" in url

    def test_trailing_slash_in_base_url_doesnt_double(
        self, kai_c_service, patched_settings
    ):
        """Operators may set MEDIAMTX_RTSP_URL with a trailing slash; the
        resolver should rstrip so we don't produce ``//cam-N``."""
        patched_settings.mediamtx_rtsp_url = "rtsp://mediamtx:8554/"
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value="T"):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=1, fallback_url="rtsp://x"
            )
        assert "rtsp://mediamtx:8554/cam-1?" in url
        assert "//cam-1" not in url.replace("rtsp://", "")

    def test_path_mode_ip_falls_back_to_camera_url(
        self, kai_c_service, patched_settings
    ):
        """Peer-review #1. ``mediamtx_path_mode=ip`` produces
        ``cam-{ip_with_dots_to_underscores}`` paths in MediaMTX, but
        the resolver only knows camera_id. Rather than serve MediaMTX
        a URL it'll 404, fall back to the camera's direct URL."""
        patched_settings.mediamtx_path_mode = "ip"
        # _get_inference_mediamtx_jwt should NOT be called when the
        # path-mode short-circuit fires — verify with a mock that
        # would record the call.
        with patch.object(
            kai_c_service, "_get_inference_mediamtx_jwt", return_value="T"
        ) as mock_mint:
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=42, fallback_url="rtsp://192.168.1.50/stream1"
            )
        assert url == "rtsp://192.168.1.50/stream1"
        assert mock_mint.call_count == 0

    def test_path_mode_is_case_insensitive(self, kai_c_service, patched_settings):
        """``ID`` / ``Id`` / ``id`` should all behave the same."""
        patched_settings.mediamtx_path_mode = "ID"
        with patch.object(kai_c_service, "_get_inference_mediamtx_jwt", return_value="T"):
            url = kai_c_service._resolve_inference_rtsp_url(
                camera_id=1, fallback_url="rtsp://x"
            )
        assert "rtsp://mediamtx:8554/cam-1?" in url


# ────────────────────────────────────────────────────────────────────
# _get_inference_mediamtx_jwt
# ────────────────────────────────────────────────────────────────────


class TestInferenceJwtCache:
    """The JWT cache exists so we don't pay the ~1ms RSA-sign cost per
    frame. Cache correctness matters — a token re-minted on every
    capture is wasteful, but worse, a token cached past expiry yields
    a 401 from MediaMTX and silently kills inference."""

    def test_first_call_mints_and_returns_token(self, kai_c_service):
        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            return_value="MINTED-TOKEN",
        ) as mock_mint:
            tok = kai_c_service._get_inference_mediamtx_jwt()
        assert tok == "MINTED-TOKEN"
        assert mock_mint.call_count == 1
        # Peer-review #2: mint must request explicit regex-wildcard
        # ``~.*`` path scope (not rely on absent-path semantics, which
        # are documented inconsistently in MediaMTX).
        call_kwargs = mock_mint.call_args.kwargs
        assert call_kwargs["camera_id"] is None
        assert call_kwargs["camera_path"] == "~.*"
        assert call_kwargs["actions"] == ["read"]

    def test_cache_hit_avoids_remint(self, kai_c_service):
        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            return_value="MINTED-TOKEN",
        ) as mock_mint:
            t1 = kai_c_service._get_inference_mediamtx_jwt()
            t2 = kai_c_service._get_inference_mediamtx_jwt()
            t3 = kai_c_service._get_inference_mediamtx_jwt()
        assert t1 == t2 == t3 == "MINTED-TOKEN"
        # Three calls but only ONE mint — the cache did its job.
        assert mock_mint.call_count == 1

    def test_cache_expiry_triggers_remint(self, kai_c_service):
        """Past the cache TTL, the next call must re-mint."""
        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            side_effect=["T1", "T2"],
        ) as mock_mint:
            t1 = kai_c_service._get_inference_mediamtx_jwt()
            # Manually expire the cache (simulates time advancing past 50min).
            kai_c_service._inference_jwt_expires_at = 0.0
            t2 = kai_c_service._get_inference_mediamtx_jwt()
        assert t1 == "T1"
        assert t2 == "T2"
        assert mock_mint.call_count == 2

    def test_mint_exception_returns_none_and_doesnt_cache(self, kai_c_service):
        """A failed mint must NOT cache None — otherwise we'd be stuck
        returning None until the (zero) TTL elapses on the next call."""
        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            side_effect=RuntimeError("missing keys"),
        ) as mock_mint:
            tok1 = kai_c_service._get_inference_mediamtx_jwt()
            tok2 = kai_c_service._get_inference_mediamtx_jwt()
        assert tok1 is None
        assert tok2 is None
        # Both attempts hit the mint path, because the failure was not cached.
        assert mock_mint.call_count == 2

    def test_explicit_cache_invalidation_forces_remint(self, kai_c_service):
        """The capture-failure path zeroes _inference_jwt + expires_at.
        Next call to _get_inference_mediamtx_jwt must re-mint."""
        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            side_effect=["T1", "T2"],
        ) as mock_mint:
            t1 = kai_c_service._get_inference_mediamtx_jwt()
            # Simulate the invalidation that capture_frame_from_rtsp does
            # when a tap-active capture returns None.
            kai_c_service._inference_jwt = None
            kai_c_service._inference_jwt_expires_at = 0.0
            t2 = kai_c_service._get_inference_mediamtx_jwt()
        assert (t1, t2) == ("T1", "T2")
        assert mock_mint.call_count == 2

    def test_invalidation_backoff_prevents_mint_storm(self, kai_c_service):
        """Peer-review #8. Under a sustained MediaMTX outage, every
        capture-failure invalidates the JWT cache. Without back-off,
        we'd mint a fresh ~1ms RSA-signed token on every poll cycle of
        every camera. The back-off skips the mint while a recent
        invalidation is still fresh."""
        import time as _time

        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            return_value="T",
        ) as mock_mint:
            # Simulate a fresh invalidation as capture_frame_from_rtsp
            # would record it.
            kai_c_service._inference_jwt = None
            kai_c_service._inference_jwt_expires_at = 0.0
            kai_c_service._inference_jwt_invalidated_at = _time.monotonic()

            # Calls immediately after invalidation must NOT mint.
            for _ in range(5):
                assert kai_c_service._get_inference_mediamtx_jwt() is None
            assert mock_mint.call_count == 0

            # Walk the clock past the back-off window. We do that by
            # setting the invalidation timestamp to the past — same
            # observable behaviour as time advancing.
            kai_c_service._inference_jwt_invalidated_at = (
                _time.monotonic()
                - kai_c_service._INFERENCE_JWT_INVALIDATION_BACKOFF_SECONDS
                - 1.0
            )
            tok = kai_c_service._get_inference_mediamtx_jwt()
        assert tok == "T"
        assert mock_mint.call_count == 1

    def test_successful_mint_clears_backoff(self, kai_c_service):
        """When MediaMTX comes back, the next invalidation should mint
        immediately — not wait out a stale back-off window."""
        import time as _time

        with patch(
            "services.mediamtx_jwt_service.MediaMtxJwtService.create_stream_token",
            side_effect=["T1", "T2"],
        ):
            # First mint succeeds, clears any prior back-off.
            kai_c_service._inference_jwt_invalidated_at = (
                _time.monotonic() - 999.0  # forces past the window
            )
            assert kai_c_service._get_inference_mediamtx_jwt() == "T1"
            # Successful mint must have cleared the invalidation marker.
            assert kai_c_service._inference_jwt_invalidated_at == 0.0

            # Now invalidate again and immediately re-mint — the new
            # invalidation imposes its own fresh back-off, which is
            # what we want (we just got a 401, give the system a
            # moment to settle before hammering it).
            kai_c_service._inference_jwt = None
            kai_c_service._inference_jwt_expires_at = 0.0
            kai_c_service._inference_jwt_invalidated_at = _time.monotonic()
            assert kai_c_service._get_inference_mediamtx_jwt() is None


# ────────────────────────────────────────────────────────────────────
# capture_frame_from_rtsp — JWT invalidation on tap failure
# ────────────────────────────────────────────────────────────────────


class TestCaptureFrameTapInvalidation:
    """capture_frame_from_rtsp must invalidate the JWT cache when the
    tap URL is used AND the capture fails. Without this, a token that
    MediaMTX has rejected (clock skew, JWKS rotation, MediaMTX restart)
    keeps being replayed for the full cache TTL — silently breaking
    inference for that window."""

    @pytest.mark.asyncio
    async def test_tap_failure_invalidates_jwt(
        self, kai_c_service, patched_settings
    ):
        # Pre-populate the cache so we can observe invalidation.
        kai_c_service._inference_jwt = "STALE-TOKEN"
        kai_c_service._inference_jwt_expires_at = 1e12  # effectively forever

        # Patch the sync capture to simulate a failure.
        with patch.object(kai_c_service, "_capture_frame_sync", return_value=None):
            result = await kai_c_service.capture_frame_from_rtsp(
                "rtsp://192.168.1.50/stream1", camera_id=42
            )

        assert result is None
        # Tap was active (token + URL present), capture failed → JWT
        # cache must be cleared so the next call re-mints fresh.
        assert kai_c_service._inference_jwt is None
        assert kai_c_service._inference_jwt_expires_at == 0.0

    @pytest.mark.asyncio
    async def test_tap_success_preserves_jwt(
        self, kai_c_service, patched_settings
    ):
        """A successful capture under the tap MUST NOT invalidate the
        JWT cache — that would defeat the whole caching purpose."""
        kai_c_service._inference_jwt = "GOOD-TOKEN"
        kai_c_service._inference_jwt_expires_at = 1e12

        with patch.object(
            kai_c_service, "_capture_frame_sync",
            return_value="opennvr://frames/camera_42/latest.jpg",
        ):
            result = await kai_c_service.capture_frame_from_rtsp(
                "rtsp://192.168.1.50/stream1", camera_id=42
            )

        assert result == "opennvr://frames/camera_42/latest.jpg"
        assert kai_c_service._inference_jwt == "GOOD-TOKEN"
        assert kai_c_service._inference_jwt_expires_at == 1e12

    @pytest.mark.asyncio
    async def test_fallback_failure_doesnt_invalidate_jwt(
        self, kai_c_service, patched_settings
    ):
        """When the tap is disabled and the fallback (direct camera URL)
        fails, the JWT cache is irrelevant and must not be touched."""
        patched_settings.inference_use_mediamtx_tap = False
        kai_c_service._inference_jwt = "PRESERVE-ME"
        kai_c_service._inference_jwt_expires_at = 1e12

        with patch.object(kai_c_service, "_capture_frame_sync", return_value=None):
            await kai_c_service.capture_frame_from_rtsp(
                "rtsp://192.168.1.50/stream1", camera_id=42
            )

        # JWT cache untouched because the tap was never used.
        assert kai_c_service._inference_jwt == "PRESERVE-ME"


# ────────────────────────────────────────────────────────────────────
# URL redaction for safe logging
# ────────────────────────────────────────────────────────────────────


class TestRedactRtspUrlForLog:
    """Tap URLs carry ``?jwt=<TOKEN>``; camera URLs may carry
    ``user:password@`` basic-auth. The redactor strips both so
    secrets don't land in operator log files."""

    def test_redacts_jwt_query_string(self):
        from services.kai_c_service import KaiCService

        redacted = KaiCService._redact_rtsp_url_for_log(
            "rtsp://mediamtx:8554/cam-42?jwt=eyJhbGciOiJSUzI1NiJ9.payload.sig"
        )
        assert "eyJhbGciOiJSUzI1NiJ9" not in redacted
        assert "payload.sig" not in redacted
        assert redacted == "rtsp://mediamtx:8554/cam-42?<redacted>"

    def test_url_without_query_passes_through(self):
        from services.kai_c_service import KaiCService

        # A bare RTSP URL with no secrets stays exactly as-is.
        url = "rtsp://192.168.1.50/stream1"
        assert KaiCService._redact_rtsp_url_for_log(url) == url

    def test_redacts_basic_auth_userinfo(self):
        """Peer-review #9. Camera RTSP URLs commonly embed credentials
        as ``user:password@host``. Logging those was a pre-existing
        gap closed here at the same time as the JWT redaction."""
        from services.kai_c_service import KaiCService

        redacted = KaiCService._redact_rtsp_url_for_log(
            "rtsp://admin:hunter2@192.168.1.50/stream1"
        )
        assert "admin" not in redacted
        assert "hunter2" not in redacted
        assert "<redacted>@192.168.1.50" in redacted
        assert "/stream1" in redacted  # path preserved

    def test_redacts_both_userinfo_and_jwt(self):
        """If a URL has both userinfo AND a query string (unusual but
        not impossible — e.g. an operator's custom MediaMTX setup),
        both get redacted."""
        from services.kai_c_service import KaiCService

        redacted = KaiCService._redact_rtsp_url_for_log(
            "rtsp://user:pass@mediamtx:8554/cam-1?jwt=TOKEN"
        )
        assert "user" not in redacted
        assert "pass" not in redacted
        assert "TOKEN" not in redacted
        assert "<redacted>@mediamtx:8554" in redacted
        assert "?<redacted>" in redacted

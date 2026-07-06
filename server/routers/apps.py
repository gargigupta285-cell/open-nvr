# Copyright (c) 2026 OpenNVR
# This file is part of OpenNVR.
#
# OpenNVR is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenNVR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.

"""
App Registry Router — the missing seam from App SDK spec §05.

Apps built on the OpenNVR App SDK self-register on boot
(``POST /apps/register``), exactly the shape adapters already use
against KAI-C. The registry stores the ``AppManifest.to_dict()``
snapshot plus operator config, and because ``manifest.params`` is
typed and declarative, ``PUT /apps/{id}/config`` validates without any
app-specific code (see :func:`validate_app_config`).

Routes (mounted under ``/api/v1``):

- ``GET  /apps``                 — catalog listing
- ``POST /apps/register``        — app calls this on boot (upsert)
- ``POST /apps/{id}/enable``     — operator toggles
- ``POST /apps/{id}/disable``
- ``PUT  /apps/{id}/config``     — validated vs manifest.params
- ``GET  /apps/{id}/status``     — proxies the app's /health + /state
"""

import ipaddress
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import get_current_active_user, verify_token
from core.database import get_db
from models import InstalledApp, User
from services.audit_service import write_audit_log

router = APIRouter(prefix="/apps", tags=["apps"])

# Seconds allowed for each proxied call to the app's /health and /state.
STATUS_PROBE_TIMEOUT_S = 3.0


# ── Config validation (pure, reusable by the SDK conformance kit) ──

# Manifest param "type" names → Python types accepted on the wire.
# "float" accepts int too (JSON has one number type); bool is excluded
# from the numeric checks because bool is a subclass of int in Python.
_PRIMITIVE_TYPES: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


def _value_matches_type(value: Any, type_name: str) -> bool:
    """True when ``value`` is acceptable for a manifest param ``type``.

    Dotted UI-schema types (``"geometry.polygon"``) are list-shaped on
    the wire — deep validation is the catalog zone editor's job, so we
    only require a list. Unknown plain type names are not blocked.
    """
    if "." in type_name:
        return isinstance(value, list)
    expected = _PRIMITIVE_TYPES.get(type_name)
    if expected is None:
        return True
    if type_name in ("int", "float") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def validate_app_config(manifest: dict, config: dict) -> list[str]:
    """Validate operator ``config`` against ``manifest["params"]``.

    Returns a list of human-readable error strings (empty ⇒ valid):

    - config keys not declared in the manifest;
    - params with ``required=True`` and no default that are absent;
    - values whose type doesn't match the param ``type`` name;
    - ``per_camera=True`` params must be a dict keyed by camera id
      whose values each pass the type check.
    """
    errors: list[str] = []
    params: dict[str, dict] = {
        p["name"]: p
        for p in (manifest.get("params") or [])
        if isinstance(p, dict) and "name" in p
    }

    unknown = sorted(set(config) - set(params))
    if unknown:
        errors.append(f"unknown config keys: {', '.join(unknown)}")

    for name, param in params.items():
        if name not in config:
            if param.get("required") and param.get("default") is None:
                errors.append(f"missing required param: {name}")
            continue

        value = config[name]
        type_name = str(param.get("type", ""))

        if param.get("per_camera"):
            if not isinstance(value, dict):
                errors.append(
                    f"param '{name}' is per_camera and must be a dict "
                    "keyed by camera id"
                )
                continue
            for camera_id, camera_value in value.items():
                if not _value_matches_type(camera_value, type_name):
                    errors.append(
                        f"param '{name}' for camera '{camera_id}' must be "
                        f"of type {type_name}"
                    )
            continue

        if not _value_matches_type(value, type_name):
            errors.append(f"param '{name}' must be of type {type_name}")

    return errors


# ── App URL sovereignty guard (SSRF) ───────────────────────────────

# RFC1918 private ranges — a single-box / LAN deployment's app
# containers live here (or on loopback); nothing else is a legitimate
# place for an SDK app to be reached from this server.
_RFC1918_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def validate_app_url(url: str) -> str | None:
    """Return an operator-facing error string if ``url`` is not a safe
    app base URL, or ``None`` when it is acceptable.

    ``GET /apps/{id}/status`` server-side fetches ``{url}/health`` and
    ``{url}/state`` and reflects the bodies to the caller, so an
    arbitrary registered URL is an SSRF primitive. This guard mirrors
    the V-022 sovereignty precedent in ``kai-c/kai_c/sovereignty.py``
    (adapters must live on this machine / the operator's own network):

    - scheme must be ``http`` or ``https`` and a hostname is required;
    - IP literals: loopback (``127/8``, ``::1``) and RFC1918 private
      (``10/8``, ``172.16/12``, ``192.168/16``) are allowed; link-local
      ``169.254/16`` (cloud metadata) and all public addresses are
      refused;
    - hostnames: ``localhost`` and single-label Docker service names
      (no dots, e.g. ``loitering``) are allowed; dotted FQDNs are
      refused.

    Pure — no DNS resolution, so the answer can't be spoofed by a
    resolver and the check is safe to run per request.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"app url {url!r}: scheme must be http or https"
    host = parsed.hostname
    if not host:
        return f"app url {url!r}: hostname is required"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A name, not an IP literal: localhost or a single-label Docker
        # service name only. Dotted FQDNs point off-box.
        if host == "localhost" or "." not in host:
            return None
        return (
            f"app url {url!r}: host {host!r} is a dotted hostname; only "
            "localhost, single-label Docker service names, loopback, or "
            "RFC1918 private IPs are allowed"
        )

    if ip.is_link_local:
        return (
            f"app url {url!r}: host {host!r} is link-local "
            "(169.254/16 — cloud metadata range) and is refused"
        )
    if ip.is_loopback:
        return None
    if ip.version == 4 and any(ip in net for net in _RFC1918_NETS):
        return None
    return (
        f"app url {url!r}: host {host!r} is not a loopback or RFC1918 "
        "private address"
    )


# ── Registration auth (user JWT or service key) ────────────────────

# Registration is a service-to-service call: SDK apps boot with only
# the deployment's INTERNAL_API_KEY (the same secret adapters use
# against KAI-C), not a user JWT. ``POST /apps/register`` therefore
# accepts EITHER a normal user bearer token OR an ``X-Internal-Api-Key``
# header matching ``settings.internal_api_key``. Every other route
# (enable/disable/config/status) is an operator action and stays
# strictly user-authenticated via ``get_current_active_user``.

# auto_error=False: a missing Authorization header must fall through to
# the service-key check instead of short-circuiting with a 403.
_optional_bearer = HTTPBearer(auto_error=False)


def _internal_api_key() -> str:
    """The shared secret SDK apps send in ``X-Internal-Api-Key``. Read
    lazily from settings (same pattern as
    ``services.kai_c_service.KaiCService._internal_api_key``) so tests
    and dev setups that mutate the environment late still see it."""
    try:
        from core.config import settings

        return settings.internal_api_key or ""
    except Exception:
        import os

        return os.environ.get("INTERNAL_API_KEY", "")


def get_register_principal(
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Authenticate a registration call.

    Returns the ``User`` for the JWT path, or ``None`` for the
    service-key path (audit-logged as the ``app-sdk`` service
    identity). Raises 401 when neither credential is valid.
    """
    if x_internal_api_key is not None:
        expected = _internal_api_key()
        if expected and secrets.compare_digest(x_internal_api_key, expected):
            return None  # service identity
        # The SDK sends its one token as BOTH headers (it can't know
        # which kind the operator provisioned) — a non-matching key
        # only fails the request when there's no bearer to fall back to.
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal API key",
            )

    if credentials is not None:
        token_data = verify_token(credentials.credentials)
        if token_data is not None:
            user = (
                db.query(User)
                .filter(User.username == token_data.username)
                .first()
            )
            if user is not None and user.is_active:
                return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide a bearer token or X-Internal-Api-Key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_read_principal(
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Authenticate a READ-ONLY registry call (``GET /apps`` and
    ``GET /apps/{id}/status``).

    Mirrors :func:`get_register_principal` exactly — same constant-time,
    empty-key-safe compare, same JWT fallback — but is a *separate*
    dependency so it reads clearly at the call sites as "reads only".
    A registered SDK app / a companion service (e.g. the OpenNVR Agent)
    needs to *see* the catalog and probe an app's health to relay its
    state conversationally; it holds only the deployment's
    ``INTERNAL_API_KEY``, not a user JWT.

    Returns the ``User`` for the JWT path, or ``None`` for the
    service-key path. Raises 401 when neither credential is valid. The
    write routes (enable/disable/config/register) never call this — they
    stay strictly ``get_current_active_user`` (register additionally
    accepts the key via :func:`get_register_principal`).
    """
    if x_internal_api_key is not None:
        expected = _internal_api_key()
        if expected and secrets.compare_digest(x_internal_api_key, expected):
            return None  # service identity
        # A service sends its one token as BOTH headers (it can't know
        # which kind the operator provisioned) — a non-matching key
        # only fails the request when there's no bearer to fall back to.
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal API key",
            )

    if credentials is not None:
        token_data = verify_token(credentials.credentials)
        if token_data is not None:
            user = (
                db.query(User)
                .filter(User.username == token_data.username)
                .first()
            )
            if user is not None and user.is_active:
                return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide a bearer token or X-Internal-Api-Key",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Request schemas / serialization ────────────────────────────────


class AppRegisterRequest(BaseModel):
    """What the SDK POSTs on boot: where the app lives + its manifest."""

    url: str
    manifest: dict[str, Any]


def _serialize_app(row: InstalledApp) -> dict[str, Any]:
    """The wire shape of one installed app (list + register response)."""
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "version": row.version,
        "url": row.url,
        "enabled": bool(row.enabled),
        "status": row.status,
        "last_seen": row.last_seen,
        "manifest": row.manifest_json,
        "config": row.config_json or {},
    }


def _get_app_or_404(db: Session, app_id: str) -> InstalledApp:
    row = db.query(InstalledApp).filter(InstalledApp.id == app_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_id}' is not registered",
        )
    return row


# ── Routes ─────────────────────────────────────────────────────────


@router.get("")
async def list_apps(
    principal: User | None = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """
    List all installed apps — the catalog's data source.

    Each card checks ``manifest.requires_tasks`` against
    ``GET /api/v1/adapters`` client-side to grey out apps whose model
    prerequisites aren't met.

    Requires an authenticated user OR the deployment's internal API key
    (``X-Internal-Api-Key``) — a service (the OpenNVR Agent) reads the
    catalog to relay installed apps as conversational skills. Read only;
    see :func:`get_read_principal`.
    """
    rows = db.query(InstalledApp).order_by(InstalledApp.id).all()
    return [_serialize_app(row) for row in rows]


@router.post("/register")
async def register_app(
    request: AppRegisterRequest,
    principal: User | None = Depends(get_register_principal),
    db: Session = Depends(get_db),
):
    """
    Register (or re-register) an app — the SDK calls this on boot.

    Upserts by manifest id: url / manifest / name / version / category
    refresh on every boot, while the operator-owned ``enabled`` flag
    and ``config_json`` survive restarts.

    The app URL must pass :func:`validate_app_url` — /status later
    server-side fetches it, so off-box URLs are refused here (SSRF).

    Requires an authenticated user OR the deployment's internal API
    key (``X-Internal-Api-Key``) — registration is service-to-service;
    see :func:`get_register_principal`.
    """
    manifest = request.manifest
    missing = [key for key in ("id", "name", "version") if not manifest.get(key)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Manifest is missing required fields: {', '.join(missing)}",
        )

    url_error = validate_app_url(request.url)
    if url_error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=url_error,
        )

    app_id = str(manifest["id"])
    row = db.query(InstalledApp).filter(InstalledApp.id == app_id).first()
    created = row is None
    if created:
        row = InstalledApp(id=app_id, enabled=False, config_json={})
        db.add(row)

    row.name = str(manifest["name"])
    row.version = str(manifest["version"])
    row.category = manifest.get("category")
    row.url = request.url.rstrip("/")
    row.manifest_json = manifest
    row.status = "registered"
    row.last_seen = datetime.now(UTC)
    db.commit()
    db.refresh(row)

    write_audit_log(
        db,
        action="app.register",
        # Service-key registrations have no user row; the actor is
        # recorded in details instead so the audit trail stays whole.
        user_id=principal.id if principal is not None else None,
        entity_type="app",
        entity_id=app_id,
        details={
            "created": created,
            "url": row.url,
            "version": row.version,
            "registered_by": (
                f"user:{principal.username}"
                if principal is not None
                else "service:internal-api-key"
            ),
        },
    )
    return _serialize_app(row)


@router.post("/{app_id}/enable")
async def enable_app(
    app_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Enable an app. 404 if the app was never registered.

    Requires authenticated user.
    """
    row = _get_app_or_404(db, app_id)
    row.enabled = True
    db.commit()
    db.refresh(row)

    write_audit_log(
        db,
        action="app.enable",
        user_id=current_user.id,
        entity_type="app",
        entity_id=app_id,
    )
    return _serialize_app(row)


@router.post("/{app_id}/disable")
async def disable_app(
    app_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Disable an app. 404 if the app was never registered.

    Requires authenticated user.
    """
    row = _get_app_or_404(db, app_id)
    row.enabled = False
    db.commit()
    db.refresh(row)

    write_audit_log(
        db,
        action="app.disable",
        user_id=current_user.id,
        entity_type="app",
        entity_id=app_id,
    )
    return _serialize_app(row)


@router.put("/{app_id}/config")
async def update_app_config(
    app_id: str,
    config: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Replace an app's config, validated against ``manifest.params``.

    Because the manifest is typed and declarative, this endpoint needs
    zero app-specific code — see :func:`validate_app_config`.

    Manifest defaults are materialized into the stored config: any
    param omitted from the payload whose manifest ``default`` is not
    None is filled in before persisting (provided values are never
    overwritten), so ``config_json`` is the effective config and the
    registry stays the single source of truth (spec §05).

    Requires authenticated user.
    """
    row = _get_app_or_404(db, app_id)
    manifest = row.manifest_json or {}
    errors = validate_app_config(manifest, config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid config: {'; '.join(errors)}",
        )

    effective = dict(config)
    for param in manifest.get("params") or []:
        if not (isinstance(param, dict) and "name" in param):
            continue
        name = param["name"]
        if name not in effective and param.get("default") is not None:
            effective[name] = param["default"]

    row.config_json = effective
    db.commit()
    db.refresh(row)

    write_audit_log(
        db,
        action="app.config.update",
        user_id=current_user.id,
        entity_type="app",
        entity_id=app_id,
        details={"config_keys": sorted(effective)},
    )
    return _serialize_app(row)


@router.get("/{app_id}/status")
async def get_app_status(
    app_id: str,
    principal: User | None = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """
    Proxy the app's live ``/health`` and ``/state`` endpoints.

    Degrades gracefully: an unreachable app yields
    ``{"health": {"status": "unreachable"}, "state": None}`` rather
    than a 5xx, and the stored row status flips to ``unreachable`` so
    the catalog dot goes red without polling the app itself.

    The stored URL is re-checked against :func:`validate_app_url`
    before any fetch (defense in depth for rows registered before the
    guard existed): a blocked URL short-circuits to
    ``{"health": {"status": "blocked_url"}, "state": None}``.

    Requires an authenticated user OR the deployment's internal API key
    (``X-Internal-Api-Key``) — a service reads live app state to relay
    it; see :func:`get_read_principal`. Read only.
    """
    row = _get_app_or_404(db, app_id)
    base_url = row.url.rstrip("/")

    if validate_app_url(base_url) is not None:
        row.status = "blocked_url"
        db.commit()
        return {"health": {"status": "blocked_url"}, "state": None}

    health: dict[str, Any]
    state: Any = None
    reachable = False
    async with httpx.AsyncClient(timeout=STATUS_PROBE_TIMEOUT_S) as client:
        try:
            health_resp = await client.get(f"{base_url}/health")
            health_resp.raise_for_status()
            health = health_resp.json()
            reachable = True
        except Exception:
            health = {"status": "unreachable"}

        if reachable:
            try:
                state_resp = await client.get(f"{base_url}/state")
                state_resp.raise_for_status()
                state = state_resp.json()
            except Exception:
                state = None

    # SDK /health reports `ready` (spec §03); tolerate its absence on
    # a 200 rather than flagging a healthy app unreachable.
    ready = reachable and bool(health.get("ready", True))
    row.status = "ok" if ready else "unreachable"
    if reachable:
        row.last_seen = datetime.now(UTC)
    db.commit()

    return {"health": health, "state": state}

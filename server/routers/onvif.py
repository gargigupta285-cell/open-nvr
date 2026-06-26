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
ONVIF routes: discovery, profiles, stream URI, PTZ controls.

Supports both WS-Security (via onvif-zeep) and HTTP Digest authentication.
HTTP Digest is more compatible with Hikvision and similar devices.
"""

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.auth import get_current_superuser
from core.database import get_db
from routers.network import get_camera_lan_subnet
from services.onvif_digest_service import (
    connect_and_get_profiles,
    fetch_profiles_digest,
    get_stream_uri_digest,
    get_system_datetime,
    set_system_datetime,
)

# Import both services - digest is the preferred one for Hikvision compatibility
from services.onvif_service import (
    discover_onvif_devices,
    fetch_profiles,
    get_stream_uri,
    ptz_continuous_move,
    ptz_presets,
    ptz_stop,
    scan_onvif_subnet,
)

router = APIRouter(tags=["onvif"])


@router.get("/discover")
async def discover(
    cidr: str | None = Query(
        None,
        description="Subnet CIDR to scan, e.g. 192.168.1.0/24. Must be within the configured Camera LAN subnet.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_superuser),
):
    """Discover ONVIF cameras via unicast subnet scan (works in Docker bridge mode).

    Uses the Camera LAN subnet configured under Network settings as the scan boundary.
    An optional narrower CIDR may be supplied but must fall within that range.
    Multicast WS-Discovery is attempted as a best-effort fallback and merged with results.
    """
    configured_cidr = get_camera_lan_subnet(db)
    if not configured_cidr:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Camera LAN subnet configured. "
                "Set subnet_cidr under Network → Camera LAN settings before running discovery."
            ),
        )

    scan_cidr = configured_cidr
    if cidr:
        # Validate that the requested CIDR is a subnet of (or equal to) the configured range
        try:
            requested = ipaddress.ip_network(cidr, strict=False)
            configured = ipaddress.ip_network(configured_cidr, strict=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid CIDR: {exc}")
        if not requested.subnet_of(configured):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Requested CIDR {cidr} is not within the configured "
                    f"Camera LAN subnet {configured_cidr}."
                ),
            )
        scan_cidr = cidr

    # Unicast scan — works across Docker bridge SNAT
    try:
        devices = await scan_onvif_subnet(scan_cidr)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Best-effort multicast fallback (works only in host-mode or on native LAN)
    try:
        multicast_devices = await discover_onvif_devices()
        seen_ips = {d["ip"] for d in devices}
        for md in multicast_devices:
            if md.get("ip") and md["ip"] not in seen_ips:
                devices.append(md)
                seen_ips.add(md["ip"])
    except Exception:
        pass

    return {"devices": devices, "scan_cidr": scan_cidr}


@router.post("/connect")
async def connect_device(
    ip: str = Query(...),
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
):
    """
    Connect to ONVIF device and get all profiles with stream URIs.

    Uses HTTP Digest authentication which is compatible with Hikvision and most devices.
    Returns device info and all available profiles with their RTSP stream URIs.
    """
    try:
        result = await connect_and_get_profiles(ip, username, password, port)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/{ip}/profiles")
async def camera_profiles(
    ip: str,
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
    use_digest: bool = Query(
        True, description="Use HTTP Digest auth (better Hikvision compatibility)"
    ),
):
    """Get media profiles from camera. Set use_digest=true for Hikvision devices."""
    try:
        if use_digest:
            profiles = await fetch_profiles_digest(ip, username, password, port)
        else:
            profiles = await fetch_profiles(ip, username, password, port)
        return {"ip": ip, "profiles": profiles}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/{ip}/stream-uri")
async def camera_stream_uri(
    ip: str,
    profile_token: str = Query(..., alias="profileToken"),
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
    use_digest: bool = Query(
        True, description="Use HTTP Digest auth (better Hikvision compatibility)"
    ),
):
    """Get stream URI for a profile. Set use_digest=true for Hikvision devices."""
    try:
        if use_digest:
            uri = await get_stream_uri_digest(
                ip, username, password, profile_token, port
            )
        else:
            uri = await get_stream_uri(ip, username, password, profile_token, port)
        return {"ip": ip, "profileToken": profile_token, "uri": uri}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/{ip}/ptz/move")
async def camera_ptz_move(
    ip: str,
    x: float = Query(0.0, ge=-1.0, le=1.0),
    y: float = Query(0.0, ge=-1.0, le=1.0),
    z: float = Query(0.0, ge=-1.0, le=1.0),
    profile_token: str = Query(..., alias="profileToken"),
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
):
    try:
        result = await ptz_continuous_move(
            ip, username, password, profile_token, x, y, z, port
        )
        return {"ip": ip, "profileToken": profile_token, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/{ip}/ptz/stop")
async def camera_ptz_stop(
    ip: str,
    profile_token: str = Query(..., alias="profileToken"),
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
):
    try:
        result = await ptz_stop(ip, username, password, profile_token, port)
        return {"ip": ip, "profileToken": profile_token, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/{ip}/time")
async def camera_get_time(
    ip: str,
    port: int = Query(80, ge=1, le=65535),
):
    """Read the camera clock via GetSystemDateAndTime (no credentials needed)."""
    try:
        result = await get_system_datetime(ip, port)
        return {"ip": ip, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/{ip}/time/sync")
async def camera_sync_time(
    ip: str,
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
    current_user=Depends(get_current_superuser),
):
    """Push the NVR's current UTC time to the camera (superuser only)."""
    try:
        result = await set_system_datetime(ip, username, password, port)
        return {"ip": ip, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/{ip}/ptz/preset")
async def camera_ptz_preset(
    ip: str,
    action: str = Query(...),
    profile_token: str = Query(..., alias="profileToken"),
    name: str | None = None,
    preset_token: str | None = Query(None, alias="presetToken"),
    port: int = Query(80, ge=1, le=65535),
    username: str = Query(...),
    password: str = Query(...),
):
    try:
        result = await ptz_presets(
            ip,
            username,
            password,
            profile_token,
            action,
            name=name,
            preset_token=preset_token,
            port=port,
        )
        return {
            "ip": ip,
            "profileToken": profile_token,
            "action": action,
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

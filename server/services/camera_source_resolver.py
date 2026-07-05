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
Camera source resolver — derive an RTSP URL (and device identity) from just an
IP + credentials, so operators don't have to know their camera's RTSP path.

Order of attempts:
1. **ONVIF direct-connect** (not broadcast discovery) on the common HTTP ports.
   Returns the camera's own advertised stream URI *and* GetDeviceInformation
   (manufacturer/model/firmware/serial). This works for most cameras even when
   broadcast discovery failed.
2. **Vendor RTSP templates + DESCRIBE probe** for non-ONVIF devices
   (Hikvision /Streaming/Channels/101, Dahua/CP Plus /cam/realmonitor?...).

Credentials are embedded into the returned URL so MediaMTX can authenticate to
the source (it pulls the source URL as-is).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import re
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from core.logging_config import main_logger
from services import onvif_digest_service as ods

# ONVIF HTTP control ports to try, in order (RTSP 554 is intentionally excluded).
_ONVIF_PORTS = (80, 8000)

# Vendor RTSP path templates for the main stream (1-based channel 1).
_VENDOR_TEMPLATES = {
    "hikvision": "/Streaming/Channels/101",
    "dahua": "/cam/realmonitor?channel=1&subtype=0",
    "cpplus": "/cam/realmonitor?channel=1&subtype=0",
}
# What we probe when the brand is unknown (covers the bulk of the SMB market).
_FALLBACK_PATHS = (
    "/Streaming/Channels/101",  # Hikvision / Uniview
    "/cam/realmonitor?channel=1&subtype=0",  # Dahua / CP Plus
)


def inject_credentials(url: str | None, username: str | None, password: str | None) -> str | None:
    """Embed ``user:pass@`` into an rtsp(s) URL's authority (no-op if the URL
    already has userinfo, isn't rtsp, or no username is given)."""
    if not url or not username:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.scheme.lower() not in ("rtsp", "rtsps") or "@" in parsed.netloc:
        return url
    userinfo = f"{quote(username, safe='')}:{quote(password or '', safe='')}@"
    return urlunparse(parsed._replace(netloc=userinfo + parsed.netloc))


# --- RTSP DESCRIBE probe (auth-aware) --------------------------------------


def _parse_auth_params(header: str) -> dict[str, str]:
    _scheme, _, rest = header.strip().partition(" ")
    params: dict[str, str] = {}
    for m in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,]+))', rest):
        params[m.group(1).lower()] = (
            m.group(2) if m.group(2) is not None else (m.group(3) or "").strip()
        )
    return params


def _build_rtsp_auth(method: str, uri: str, user: str, pw: str, www: str) -> str | None:
    low = (www or "").strip().lower()
    if low.startswith("basic"):
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    if low.startswith("digest"):
        p = _parse_auth_params(www)
        realm, nonce = p.get("realm", ""), p.get("nonce", "")
        if not nonce:
            return None

        def md5(s: str) -> str:
            return hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()

        ha1, ha2 = md5(f"{user}:{realm}:{pw}"), md5(f"{method}:{uri}")
        if p.get("qop"):
            nc, cnonce = "00000001", "0a4f113b9812"
            resp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                    f'uri="{uri}", qop=auth, nc={nc}, cnonce="{cnonce}", '
                    f'response="{resp}", algorithm=MD5')
        resp = md5(f"{ha1}:{nonce}:{ha2}")
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                f'uri="{uri}", response="{resp}", algorithm=MD5')
    return None


async def _describe(host: str, port: int, uri: str, auth: str | None, timeout: float):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout
        )
    except (TimeoutError, OSError):
        return None, {}
    try:
        lines = [f"DESCRIBE {uri} RTSP/1.0", "CSeq: 1", "Accept: application/sdp",
                 "User-Agent: OpenNVR"]
        if auth:
            lines.append(f"Authorization: {auth}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await asyncio.wait_for(writer.drain(), timeout)
        status = await asyncio.wait_for(reader.readline(), timeout)
        parts = status.decode("latin-1", "replace").split()
        code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line or line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode("latin-1", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        return code, headers
    except (TimeoutError, OSError, ValueError):
        return None, {}
    finally:
        writer.close()
        with contextlib.suppress(TimeoutError, OSError):
            await asyncio.wait_for(writer.wait_closed(), timeout)


async def _rtsp_path_works(host: str, port: int, path: str, user: str, pw: str,
                           timeout: float = 3.0) -> bool:
    """True if a DESCRIBE of ``path`` answers 200 (after one auth challenge)."""
    uri = f"rtsp://{host}:{port}{path}"
    code, headers = await _describe(host, port, uri, None, timeout)
    if code == 401 and user:
        auth = _build_rtsp_auth("DESCRIBE", uri, user, pw or "",
                                headers.get("www-authenticate", ""))
        if auth:
            code, _ = await _describe(host, port, uri, auth, timeout)
    return code == 200


# --- resolver --------------------------------------------------------------


async def resolve_source(
    ip: str, username: str | None, password: str | None, rtsp_port: int = 554
) -> dict[str, Any] | None:
    """Derive ``{rtsp_url, manufacturer, model, firmware_version, serial_number,
    hardware_id}`` from an IP + credentials, or ``None`` if nothing worked.
    ``rtsp_url`` always carries embedded credentials when available."""
    username = username or ""
    password = password or ""

    # 1. ONVIF direct-connect (also yields device identity).
    for onvif_port in _ONVIF_PORTS:
        try:
            info = await ods.connect_and_get_profiles(ip, username, password, onvif_port)
        except Exception:
            continue
        stream_uri = next(
            (p.get("stream_uri") for p in info.get("profiles", []) if p.get("stream_uri")),
            None,
        )
        if not stream_uri:
            continue
        # ONVIF returns the URI XML-escaped (e.g. &amp;) — unescape before use.
        stream_uri = html.unescape(stream_uri)
        dev = info.get("device_info", {}) or {}
        return {
            "rtsp_url": inject_credentials(stream_uri, username, password),
            "manufacturer": dev.get("manufacturer"),
            "model": dev.get("model"),
            "firmware_version": dev.get("firmwareversion"),
            "serial_number": dev.get("serialnumber"),
            "hardware_id": dev.get("hardwareid"),
            "onvif_port": onvif_port,
            "source": "onvif",
        }

    # 2. Vendor RTSP template + DESCRIBE probe (non-ONVIF / ONVIF-off cameras).
    for path in _FALLBACK_PATHS:
        try:
            if await _rtsp_path_works(ip, rtsp_port, path, username, password):
                url = f"rtsp://{ip}:{rtsp_port}{path}"
                return {
                    "rtsp_url": inject_credentials(url, username, password),
                    "manufacturer": None, "model": None, "firmware_version": None,
                    "serial_number": None, "hardware_id": None, "source": "rtsp_probe",
                }
        except Exception as e:
            main_logger.debug("RTSP probe %s failed: %s", path, e)

    return None


async def sync_camera_time(
    ip: str, username: str, password: str, onvif_port: int | None = None
) -> bool:
    """Best-effort: push the server's current UTC to the camera so its clock
    (and the timestamp it burns into the video) is correct. The server itself
    is internet-time-synced by its host, so 'server UTC' is the correct time.

    Uses the ONVIF SetSystemDateAndTime primitive already in mainline. Returns
    True if a port accepted it; never raises."""
    ports = [onvif_port] if onvif_port else list(_ONVIF_PORTS)
    for port in ports:
        try:
            await ods.set_system_datetime(ip, username, password, port)
            main_logger.info("Synced clock on camera %s (onvif:%s)", ip, port)
            return True
        except Exception:
            continue
    return False

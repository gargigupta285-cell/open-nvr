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

"""Redact credentials from URLs before they reach log files.

Camera RTSP/RTSPS URLs and the MediaMTX ``source_url`` derived from them carry
``user:pass@`` basic-auth userinfo (and sometimes ``?token=`` / ``?jwt=`` query
secrets). Those must never be written to ``logs/server.log`` or the audit log.

Call :func:`redact_url_credentials` at every logging site that includes a
stream/source URL. Payloads *sent to* MediaMTX must keep their credentials —
only the copy that goes to a logger should be redacted.

Mirrors the logic in ``services.kai_c_service._redact_rtsp_url_for_log`` so the
two stay consistent; that private copy predates this shared helper.
"""

from urllib.parse import urlparse, urlunparse


def redact_url_credentials(url: str | None) -> str | None:
    """Return ``url`` with basic-auth userinfo and the query string replaced by
    ``<redacted>`` placeholders.

    * ``None`` / empty input passes through unchanged.
    * ``rtsp://admin:pass@1.2.3.4:554/stream?jwt=x`` ->
      ``rtsp://<redacted>@1.2.3.4:554/stream?<redacted>``
    * Best-effort: if the URL can't be parsed we still drop the query string
      (a cheap substring op that cannot raise) rather than risk logging it.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        if "?" in url:
            base, _, _ = url.partition("?")
            return f"{base}?<redacted>"
        return url

    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{netloc}"

    query = "<redacted>" if parsed.query else ""
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, query, "")
    )

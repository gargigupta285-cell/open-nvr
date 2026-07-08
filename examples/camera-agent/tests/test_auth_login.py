# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""OpenNVR-delegated login proxy: the MFA code must reach the server under the
field name its UserLogin schema expects (``code``). Sending ``totp_code`` was
silently dropped by Pydantic, so an MFA-enabled account always got
'Invalid or missing MFA code'."""
from __future__ import annotations

import asyncio

from adapter_clients import OpennvrAuthClient


class _Resp:
    status_code = 200

    def json(self):
        return {"access_token": "T", "refresh_token": "R"}


def _client_capturing(captured):
    class _C:
        async def post(self, url, json=None):
            captured["url"] = url
            captured["body"] = json
            return _Resp()
    return _C()


def test_login_sends_mfa_as_code():
    c = OpennvrAuthClient(base_url="http://srv")
    captured: dict = {}
    c._client = lambda: _client_capturing(captured)

    status, data = asyncio.run(c.login("admin", "pw", totp_code="339175"))

    assert status == 200
    assert captured["url"].endswith("/api/v1/auth/login-json")
    assert captured["body"]["code"] == "339175"      # the server's field name
    assert "totp_code" not in captured["body"]        # the old, dropped key
    assert captured["body"]["username"] == "admin"


def test_login_omits_code_without_mfa():
    c = OpennvrAuthClient(base_url="http://srv")
    captured: dict = {}
    c._client = lambda: _client_capturing(captured)

    asyncio.run(c.login("admin", "pw"))

    assert "code" not in captured["body"]
    assert "totp_code" not in captured["body"]

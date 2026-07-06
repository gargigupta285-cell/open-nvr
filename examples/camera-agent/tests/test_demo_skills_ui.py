# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Static guard on the demo Skills panel wiring (no build system / no node).

The demo (``demo/index.html``) is vanilla no-build JS, so its render path
is only covered indirectly (payload tests + ``node --check`` on the inline
script). This test parses the inline ``<script>`` statically and asserts the
skills panel's load/render handlers and the payload fields they consume are
still wired — so a future edit that drops the ``+`` button, the greyed-skill
on-ramp (``suggested_adapters`` / ``suggested_apps`` / the enable links), or
the app-skill rendering fails a test instead of silently regressing the UI.

It is deliberately whitespace-insensitive (checks for substrings/symbols,
not exact formatting) so cosmetic edits don't make it brittle.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DEMO = Path(__file__).resolve().parent.parent / "demo" / "index.html"


def _inline_script() -> str:
    html = _DEMO.read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S | re.I)
    assert scripts, "demo/index.html has no inline <script> block"
    return "\n".join(scripts)


@pytest.fixture(scope="module")
def script() -> str:
    return _inline_script()


@pytest.fixture(scope="module")
def html() -> str:
    return _DEMO.read_text(encoding="utf-8")


def test_skill_render_handlers_present(script: str) -> None:
    # The three functions that load the panel and render the enabled list +
    # the add-list. Dropping any of them breaks the panel.
    for fn in ("function loadSkills", "function renderSkills", "function renderBrowse"):
        assert fn in script, f"demo skills handler missing: {fn!r}"


def test_skill_panel_element_ids_present(html: str) -> None:
    # The card, the '+' add button, the enabled list, and the browse/add list.
    for eid in ("skillsCard", "skillAdd", "skillsList", "skillBrowseList"):
        assert f'id="{eid}"' in html, f"demo skills element id missing: {eid!r}"


def test_greyed_skill_onramp_fields_consumed(script: str) -> None:
    # The greyed-skill install on-ramp must render both the adapter and the
    # app suggestions, each with its deep-link. If a refactor drops any of
    # these field references, the on-ramp silently disappears.
    for field in (
        "suggested_adapters",
        "suggested_apps",
        "enable_url",       # adapter deep-link
        "app_enable_url",   # app deep-link
    ):
        assert field in script, f"demo no longer consumes greyed-skill field: {field!r}"


def test_app_onramp_labeling_present(script: str) -> None:
    # The greyed-skill app on-ramp turns a suggested_apps id into a readable
    # name (APP_LABELS / appLabel) and deep-links via app_enable_url. Dropping
    # this collapses the "or install the <App> app" path back to a dead end.
    assert "APP_LABELS" in script and "appLabel" in script, (
        "demo no longer maps suggested_apps ids to readable app names"
    )


def test_enable_links_are_guide_only(script: str) -> None:
    # The install links are navigation-only anchors — assert the new-tab
    # anchor pattern is present and (governance boundary, at the UI layer)
    # the skills UI never POSTs to an app enable/disable/config route.
    assert 'target="_blank"' in script, "enable links should open in a new tab"
    assert not re.search(
        r"fetch\([^)]*apps/[^)]*/(enable|disable|config)", script
    ), "skills UI must not POST to an app enable/disable/config route"

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


def test_core_skill_disable_asks_first(script: str) -> None:
    # Removing a core task-shaped skill (alarm/watch/report/task) takes a
    # whole rail panel's capability away, so the ✕ must confirm before
    # calling the disable endpoint. If the confirm gate or the core map
    # goes missing, a stray click silently de-tools the agent.
    assert "CORE_SKILLS" in script, "core-skill map missing"
    for sid in ("alarm", "watch", "report", "task"):
        assert re.search(rf'CORE_SKILLS\s*=\s*{{[^}}]*\b{sid}\b', script), (
            f"core-skill map lost entry {sid!r}"
        )
    assert re.search(r"CORE_SKILLS\[s\.id\][^\n]*confirm", script), (
        "disable path no longer confirms before removing a core skill"
    )


def test_restore_defaults_wired(script: str, html: str) -> None:
    # The Skills header's "Restore defaults" is the one-click undo for an
    # over-pruned agent: the button must exist, call the restore endpoint,
    # and only show when something is actually restorable.
    assert 'id="skillRestore"' in html, "restore-defaults button missing"
    assert "function restoreSkills" in script, "restoreSkills handler missing"
    assert '"/skills/restore"' in script, "restore endpoint call missing"
    assert re.search(r'skillRestore[\s\S]{0,200}hidden\s*=\s*!_skills\.some', script), (
        "restore button visibility no longer keyed to restorable skills"
    )


def test_watch_add_form_wired(script: str, html: str) -> None:
    # The Watching panel's + form (notify/count watches via POST /monitors).
    # Crossing is deliberately absent — placing the line is a conversation,
    # not a text field — so the form must offer exactly the two typed kinds.
    for eid in ("watchAdd", "watchForm", "watchKind", "watchTarget", "watchStart"):
        assert f'id="{eid}"' in html, f"watch form element missing: {eid!r}"
    for kind in ('value="notify"', 'value="count"'):
        assert kind in html, f"watch kind option missing: {kind}"
    assert 'value="crossing"' not in html, (
        "crossing must stay chat-only (needs the agent to place the line)"
    )
    assert '"/monitors"' in script and "cameraParam()" in script, (
        "watch form no longer POSTs /monitors with the camera selection"
    )


def test_report_add_form_wired(script: str, html: str) -> None:
    # The Scheduled-reports panel's + form (POST /reports). One schedule per
    # report: every-N-minutes wins over the daily time; neither → the
    # server's 08:00-daily default.
    for eid in ("reportAdd", "reportForm", "reportName", "reportQuery",
                "reportAt", "reportEvery", "reportCreate"):
        assert f'id="{eid}"' in html, f"report form element missing: {eid!r}"
    assert '"/reports"' in script, "report form no longer POSTs /reports"
    assert re.search(r"every_minutes\s*=\s*every[\s\S]{0,80}reportAt", script), (
        "interval-beats-daily precedence lost in the report form"
    )

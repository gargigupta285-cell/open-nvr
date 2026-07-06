# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later
"""POLISH 2 — the conversational proactive-suggestion hint.

``build_system_prompt()`` injects a compact, dynamic "unavailable
capabilities" note built from ``skills_payload()`` so the model can
PROACTIVELY tell the user a capability isn't available and suggest
installing the named adapter/app — WITHOUT acting on it or claiming a
result. This is guide-only, consistent with the whole branch: no new
action tool is added.

Covered here:
  * a greyed skill (mock ``skills_payload`` with an unavailable entry) puts
    the guidance — with its suggested adapter/app — into the system prompt;
  * all-available ⇒ the guidance block is omitted entirely (no tokens);
  * an available skill is never marked unavailable in the prompt;
  * KAI-C unreachable ⇒ don't over-claim unavailability;
  * no enable/install tool was added to the tool surface.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


def _runtime() -> CameraAgentRuntime:
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg",
                            role="front door")],
    )
    return CameraAgentRuntime(cfg)


class _StubCaps:
    """Canned ``tasks_advertised`` set (None = KAI-C unreachable)."""

    def __init__(self, tasks: set[str] | None) -> None:
        self.tasks = tasks

    @property
    def tasks_advertised(self) -> set[str] | None:
        return self.tasks

    async def refresh(self) -> set[str] | None:
        return self.tasks


_HINT_HEADER = "UNAVAILABLE CAPABILITIES"


# ── mock skills_payload: greyed entry surfaces guidance ────────────────


def test_hint_lists_greyed_skill_with_suggested_adapter(monkeypatch):
    rt = _runtime()
    payload = [
        {"id": "count", "name": "Detect & count people and objects",
         "available": True, "tasks_available": True},
        {"id": "faces", "name": "Recognise & enroll faces",
         "available": False, "tasks_available": False,
         "suggested_adapters": ["insightface"], "suggested_apps": []},
    ]
    monkeypatch.setattr(rt, "skills_payload", lambda: payload)
    prompt = rt.build_system_prompt()

    assert _HINT_HEADER in prompt
    # The greyed skill is named and its suggested adapter surfaced.
    assert "Recognise & enroll faces" in prompt
    assert "insightface" in prompt
    assert "AI Adapters" in prompt
    # The available skill is NOT flagged unavailable.
    assert "Detect & count people and objects: unavailable" not in prompt
    # Guide-only: instructs the model not to claim it performs the capability.
    assert "do NOT claim to perform it" in prompt


def test_hint_uses_suggested_app_when_no_adapter(monkeypatch):
    rt = _runtime()
    payload = [
        {"id": "apps", "name": "Query installed catalog apps",
         "available": False, "tasks_available": False,
         "suggested_adapters": [], "suggested_apps": ["Loitering Detector"]},
    ]
    monkeypatch.setattr(rt, "skills_payload", lambda: payload)
    prompt = rt.build_system_prompt()

    assert _HINT_HEADER in prompt
    assert "Loitering Detector" in prompt
    assert "App Catalog" in prompt


# ── all available ⇒ no hint block at all ───────────────────────────────


def test_no_hint_when_all_available(monkeypatch):
    rt = _runtime()
    payload = [
        {"id": "count", "name": "Detect & count", "available": True,
         "tasks_available": True},
        {"id": "see", "name": "See what's happening", "available": True,
         "tasks_available": True},
    ]
    monkeypatch.setattr(rt, "skills_payload", lambda: payload)
    prompt = rt.build_system_prompt()

    assert _HINT_HEADER not in prompt
    assert rt.unavailable_capabilities_hint() == ""


# ── installed catalog-app entries are never flagged unavailable ────────


def test_app_source_entries_skipped(monkeypatch):
    rt = _runtime()
    payload = [
        # An installed catalog app relayed as a skill: available by definition.
        {"id": "app:loiter", "source": "app", "name": "Loitering Detector",
         "available": True, "tasks_available": True},
    ]
    monkeypatch.setattr(rt, "skills_payload", lambda: payload)
    assert rt.unavailable_capabilities_hint() == ""


# ── KAI-C unreachable ⇒ don't over-claim unavailability ────────────────


def test_kaic_unreachable_does_not_flag_vision_skills():
    """With live tasks unknown (None), skills_payload reports
    tasks_available True for every skill, so the hint must NOT claim the
    always-advertised vision skills (see/count) are unavailable."""
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(None)   # unreachable / not yet fetched
    hint = rt.unavailable_capabilities_hint()
    assert "See what's happening now: unavailable" not in hint
    assert "Detect & count people and objects: unavailable" not in hint


def test_available_skill_never_marked_unavailable_real_runtime():
    """End-to-end on a real runtime with all vision tasks advertised: the
    core always-on vision skills never appear in the hint."""
    rt = _runtime()
    rt.kaic_capabilities = _StubCaps(
        {"object_detection", "image_captioning", "vqa", "face_recognition"})
    hint = rt.unavailable_capabilities_hint()
    # see + count are backed and advertised ⇒ never in the unavailable list.
    assert "See what's happening now: unavailable" not in hint
    assert "Detect & count people and objects: unavailable" not in hint


# ── no enable/install action tool was added ────────────────────────────


def test_no_action_tool_added():
    rt = _runtime()
    tool_names = {t["function"]["name"] for t in rt.tool_definitions}
    # The hint is conversational only — assert nothing that enables/installs
    # an adapter or app leaked into the tool surface.
    forbidden = {
        "enable_adapter", "install_adapter", "install_app", "enable_app",
        "enable_skill", "register_adapter", "add_adapter",
    }
    assert not (tool_names & forbidden)
    # And every advertised tool is a handler-backed one, not an enable path.
    for name in tool_names:
        assert "enable" not in name and "install" not in name

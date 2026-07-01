# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Testing-team feedback: Qwen3 <think> blocks leaked into TTS / consumed the
whole budget; hollow 'I'll check…' deflections; camera-count never answered.
These check the <think> strip, deflection handling, and the deterministic roster
answer."""
from __future__ import annotations

import asyncio

import pytest

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


# ── <think> stripping ───────────────────────────────────────────────────

def test_strip_closed_think_block():
    out = ca._clean_for_speech("<think>let me reason about this</think>2 people at the door.")
    assert out == "2 people at the door."


def test_strip_truncated_think_block_leaves_nothing():
    # all budget went to reasoning, no answer after — strip → empty (caller
    # then falls back to the tool result)
    assert ca._clean_for_speech("<think>reasoning that never finished because") == ""


def test_strip_stray_think_tags_and_markdown():
    assert ca._clean_for_speech("**<think>**hi `there` #now") in ("hithere now", "hi there now") \
        or "think" not in ca._clean_for_speech("**<think>**hi `there` #now").lower()


def test_clean_maps_camera_id_to_location():
    cams = [CameraSpec(camera_id="cam1", frame_url="x", role="the front door")]
    out = ca._clean_for_speech("cam1 appears to be offline.", cams)
    assert "cam1" not in out and "the front door" in out


# ── deflection detection ────────────────────────────────────────────────

@pytest.mark.parametrize("t", [
    "I see... I'll check the camera.",
    "Let me see what's there.",
    "One moment, checking now.",
    "I'll take a look.",
])
def test_deflections_detected(t):
    assert ca._is_deflection(t) is True


@pytest.mark.parametrize("t", [
    "There are 2 people at the front door.",
    "The driveway is clear.",
    "I can see a car and a person.",
])
def test_real_answers_not_deflections(t):
    assert ca._is_deflection(t) is False


# ── deterministic roster answer (camera-count) ──────────────────────────

def test_roster_answer():
    cams = [CameraSpec(camera_id="cam1", frame_url="x", role="the front door"),
            CameraSpec(camera_id="cam2", frame_url="x", role="the driveway")]
    a = ca._roster_answer(cams)
    assert "2 cameras" in a and "front door" in a and "driveway" in a
    assert ca._roster_answer([]).startswith("No cameras")


def test_config_question_answered_deterministically_even_if_model_deflects():
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t", text_mode=True,
                    cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg",
                                        role="the front door")])
    rt = CameraAgentRuntime(cfg)

    class _Defl:
        async def chat(self, **kw):
            return {"message": {"content": "I'll check how many cameras there are.",
                                "tool_calls": []}}
        async def aclose(self): return None
    rt.ollama = _Defl()
    reply = asyncio.run(ca._run_conversation_turn(rt, [], "how many cameras are configured"))
    assert "camera" in reply.lower() and "I'll check" not in reply


def test_tool_narration_is_a_deflection():
    # The exact filler the model emitted in the field: it narrated a tool call
    # it never made (tool_calls=0) instead of answering.
    narration = "I see... calling detect_objects to check the number of configured cameras."
    assert ca._is_deflection(narration) is True


def test_config_question_ignores_narrated_tool_call():
    # Regression: "how many cameras configured?" → the model replied with a
    # narration ("…calling detect_objects…") and tools=0. The config branch must
    # answer from the roster, NOT let that narration through.
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t", text_mode=True,
                    cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg",
                                        role="secureeye")])
    rt = CameraAgentRuntime(cfg)

    class _Narrate:
        async def chat(self, **kw):
            return {"message": {"content": "I see... calling detect_objects to "
                                "check the number of configured cameras.",
                                "tool_calls": []}}
        async def aclose(self): return None
    rt.ollama = _Narrate()
    reply = asyncio.run(ca._run_conversation_turn(
        rt, [], "How many cameras are configured right now?"))
    assert "one camera" in reply and "detect_objects" not in reply
    assert "secureeye" in reply


# ── qwen3 auto-think-off selection ──────────────────────────────────────

def test_runtime_auto_disables_thinking_for_qwen3():
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    llm_model="qwen3:1.7b",
                    cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")])
    assert CameraAgentRuntime(cfg).ollama._think is False


def test_degradation_reasons_classify_issues():
    assert "camera_offline" in ca._degradation_reasons(
        "On cam1 appears to be offline.", "x", "x", True, 1)
    assert "adapter_unavailable" in ca._degradation_reasons(
        "cam1: detector unavailable", "x", "x", True, 1)
    assert "camera_not_configured" in ca._degradation_reasons(
        "cam9 is not configured", "x", "x", False, 0)
    # whole token budget went to <think> → empty after strip
    assert "llm_think_only" in ca._degradation_reasons(
        "", "<think>reasoning…</think>", "", True, 1)
    # a clean, grounded answer has no issues
    assert ca._degradation_reasons(
        "On the door, 2 people.", "2 people", "2 people", True, 1) == []


def test_runtime_sends_no_think_for_qwen25():
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    llm_model="qwen2.5:1.5b",
                    cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")])
    assert CameraAgentRuntime(cfg).ollama._think is None

# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""STARTER EVAL SET — a behavioral spec for the camera agent.

Unit tests check *plumbing*; this file checks *behavior* on representative user
phrasings, so a refactor that quietly breaks tool routing or grounding gets
caught. It's the deterministic layer (routing, grounding gate, noise, end-to-end
on synthetic frames) that runs in CI with no LLM. The LLM-dependent cases (does
the model actually pick create_alarm for "alarm me if…") live in the live-eval
harness — see AGENT_DESIGN.md.

Grow this matrix as you add capabilities — it's the agent's regression net.
"""
from __future__ import annotations

import asyncio

import pytest

import camera_agent as ca
from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


# ── Eval 1: forced-grounding routes the question to the RIGHT tool ──────
# (describe/attribute/activity → caption/VQA ; presence/count → detector)

_ROUTING_CASES = [
    # describe / attribute / activity → describe_camera (VQA)
    ("what is the person wearing", "describe_camera"),
    ("what is he doing", "describe_camera"),
    ("describe the scene at the gate", "describe_camera"),
    ("what's happening on the porch", "describe_camera"),
    ("what colour is the car", "describe_camera"),
    ("what does it look like outside", "describe_camera"),
    ("what do you see", "describe_camera"),
    # presence / count → detect_objects
    ("how many people are at the door", "detect_objects"),
    ("is anyone in the kitchen", "detect_objects"),
    ("count the cars in the driveway", "detect_objects"),
    ("are there any dogs in the yard", "detect_objects"),
    ("is there a package on the porch", "detect_objects"),
]


@pytest.mark.parametrize("query,expected", _ROUTING_CASES)
def test_eval_forced_tool_routing(query, expected):
    assert ca._pick_forced_tool(query) == expected, query


# ── Eval 2: grounding fires on camera questions, not on chit-chat/config ─

_SHOULD_GROUND = [
    "what's at the back gate", "is anyone there", "how many cars",
    "what is he wearing", "did a person walk past",
]
_SHOULD_NOT_GROUND = [
    "hi", "hello there", "thanks, that's all", "how are you", "good morning",
]
_CONFIG_QUESTIONS = [
    "how many cameras are configured", "which cameras do you have",
    "list the cameras",
]


@pytest.mark.parametrize("q", _SHOULD_GROUND)
def test_eval_camera_questions_ground(q):
    assert ca._looks_like_camera_question(q) is True, q


@pytest.mark.parametrize("q", _SHOULD_NOT_GROUND)
def test_eval_chitchat_does_not_ground(q):
    assert ca._looks_like_camera_question(q) is False, q


@pytest.mark.parametrize("q", _CONFIG_QUESTIONS)
def test_eval_config_questions_skip_grounding(q):
    assert ca._is_config_question(q) is True, q


# ── Eval 3: noise/hallucinations are dropped, real questions pass ───────

_NOISE = ["", "you", "Thank you.", "Thanks for watching!", "[music]", "."]
_REAL = ["how many people are there", "is the gate open"]


@pytest.mark.parametrize("t", _NOISE)
def test_eval_noise_dropped(t):
    assert ca.looks_like_noise(t) is True, t


@pytest.mark.parametrize("t", _REAL)
def test_eval_real_questions_pass(t):
    assert ca.looks_like_noise(t) is False, t


# ── Eval 4: end-to-end on synthetic frames (deterministic, no LLM) ──────
# Forces the tool path and checks the spoken answer is correct + natural.

class _FakeOllama:
    """Calls the given tool, then returns empty content (so the turn falls back
    to the humanised tool result — exercises the whole spoken path)."""
    def __init__(self, tool, camera):
        self._r = [
            {"message": {"content": "", "tool_calls": [
                {"id": "1", "type": "function",
                 "function": {"name": tool, "arguments": {"camera_id": camera}}}]}},
            {"message": {"content": "", "tool_calls": []}},
        ]
        self.n = 0
    async def chat(self, **kw):
        r = self._r[min(self.n, len(self._r) - 1)]; self.n += 1; return r
    async def aclose(self):
        return None


def _runtime(spec):
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    text_mode=True, synthetic_detection=True,
                    cameras=[CameraSpec(camera_id="front_door", frame_url=spec,
                                        role="the front door")])
    return CameraAgentRuntime(cfg)


@pytest.mark.parametrize("spec,tool,expect", [
    ("synth:people=2", "detect_objects", "2 people"),
    ("synth:people=0,cars=1", "detect_objects", "car"),
])
def test_eval_end_to_end_synthetic(spec, tool, expect):
    rt = _runtime(spec)
    rt.ollama = _FakeOllama(tool, "front_door")
    reply = asyncio.run(ca._run_conversation_turn(rt, [], "what's there?",
                                                  preferred_camera="front_door"))
    assert expect in reply.lower()
    # spoken-friendly: no raw id, no colon
    assert "front_door" not in reply and ":" not in reply

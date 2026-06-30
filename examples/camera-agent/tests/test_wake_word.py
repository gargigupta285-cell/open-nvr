# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wake-word gating (voice): the agent only answers an utterance addressed to it
by name, so it ignores the TV, side-chatter, and its own echoed reply. Matching
is exact on the name + its known STT spellings, plus a tight fuzzy safety net
(wake_fuzzy 0.85) for close drift. Default persona: Sara."""
from __future__ import annotations

import pytest

import camera_agent as ca
from camera_agent import AppConfig, match_wake, wake_phrases
from context import CameraSpec


def test_wake_phrases_include_name_and_spellings():
    p = wake_phrases("Sara")
    assert "sara" in p and "sarah" in p


# ── exact match on the name + its known STT spellings ──────────────────

@pytest.mark.parametrize("t,q", [
    ("Hey Sara, what's at the door?", "what's at the door?"),
    ("Hey Sara how many people are there", "how many people are there"),
    ("Hey Sarah what do you see", "what do you see"),       # STT spelling
    ("OK Sara, is the gate open?", "is the gate open?"),
    ("Hey Sara", ""),                                       # bare wake word
])
def test_match_wake_invoked_and_strips(t, q):
    invoked, question = match_wake(t, "Sara")
    assert invoked is True
    assert question == q


@pytest.mark.parametrize("t", [
    "what's at the door?",                  # no name at all
    "turn off the lights",
    "I was talking to Sara yesterday",      # name mid-sentence must NOT trigger
    "hey sahara desert",                    # 'sahara' is a word, not a spelling
    "hey sorry about that",
])
def test_match_wake_not_invoked(t):
    assert match_wake(t, "Sara")[0] is False


def test_empty_transcript_not_invoked():
    assert match_wake("", "Sara") == (False, "")


# ── "Hey" is required by default (Hey-Siri model) ──────────────────────

def test_require_prefix_blocks_bare_name_by_default():
    assert match_wake("sara what do you see", "Sara")[0] is False
    assert match_wake("Hey Sara what do you see", "Sara")[0] is True


def test_require_prefix_off_allows_bare_name():
    invoked, q = match_wake("sara what do you see", "Sara",
                            None, 1.0, require_prefix=False)
    assert invoked is True and q == "what do you see"


# ── extra English wake words (exact) ───────────────────────────────────

@pytest.mark.parametrize("t,tail", [
    ("Hey Camera, how many people?", "how many people?"),
    ("hey camera is the gate open", "is the gate open"),
])
def test_extra_wake_word_camera(t, tail):
    invoked, q = match_wake(t, "Sara", ["camera"])
    assert invoked is True and q == tail


@pytest.mark.parametrize("t", [
    "hey calm down", "hey came back", "hey can you help",
    "hey come here", "hey camp out", "how many cameras are there",
])
def test_extra_wake_word_no_false_wake(t):
    assert match_wake(t, "Sara", ["camera"])[0] is False


# ── tight fuzzy safety net (0.85): catches drift, rejects words ────────

def test_tight_fuzzy_catches_drift_rejects_words():
    # Known spellings match exactly; the 0.85 net catches close STT drift
    # ("Saara" ~0.89) but still rejects real words (sahara ~0.67).
    assert match_wake("hey sara how many", "Sara")[0] is True
    assert match_wake("hey saara how many", "Sara")[0] is True     # close drift
    assert match_wake("hey sahara desert", "Sara")[0] is False     # real word
    assert match_wake("hey sorry about it", "Sara")[0] is False


def test_exact_only_when_fuzzy_one():
    # wake_fuzzy=1.0 → exact only; unregistered drift is dropped.
    assert match_wake("hey saara how many", "Sara", None, 1.0)[0] is False
    assert match_wake("hey sara how many", "Sara", None, 1.0)[0] is True


def test_config_defaults():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")],
    )
    assert cfg.wake_word_required is True
    assert cfg.wake_fuzzy == 0.85
    assert cfg.agent_name == "Sara"


def test_load_config_defaults_match_dataclass(tmp_path):
    # Regression: load_config (the production/YAML path) must default to the
    # SAME wake values as the dataclass — a drift here ran prod looser (0.72)
    # than documented while in-code AppConfig tests still passed.
    p = tmp_path / "c.yml"
    p.write_text("kaic_url: http://k\nkaic_api_key: x\nsystem_prompt: t\ncameras: []\n")
    cfg = ca.load_config(p)
    assert cfg.wake_fuzzy == 0.85
    assert cfg.wake_word_required is True
    assert cfg.wake_require_prefix is True
    assert cfg.agent_name == "Sara"

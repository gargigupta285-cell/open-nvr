# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wake-word gating (voice): only answer an utterance addressed to the agent by
name, so it ignores the TV, side-chatter, and its own echoed reply. Matching is
exact on the name + its known STT spellings, plus a tight fuzzy safety net
(wake_fuzzy 0.85) for close drift. The demo uses click-to-talk and doesn't hit
this gate, but the engine is kept for always-on/other surfaces. These tests use
"Kiran" as a fixture name — the one built-in alias example in ``_WAKE_ALIASES``
(STT writes it "Kieran"). The default agent_name is the plain "Camera Agent"."""
from __future__ import annotations

import pytest

import camera_agent as ca
from camera_agent import AppConfig, match_wake, wake_phrases
from context import CameraSpec


def test_wake_phrases_include_name_and_spellings():
    p = wake_phrases("Kiran")
    assert "kiran" in p and "kieran" in p and "keeran" in p


# ── exact match on the name + its known STT spellings ──────────────────

@pytest.mark.parametrize("t,q", [
    ("Hey Kiran, what's at the door?", "what's at the door?"),
    ("Hey Kiran how many people are there", "how many people are there"),
    ("Hey Kieran what do you see", "what do you see"),      # STT spelling
    ("Hey Keeran is anyone there", "is anyone there"),      # STT spelling
    ("OK Kiran, is the gate open?", "is the gate open?"),
    ("Hey Kiran", ""),                                      # bare wake word
])
def test_match_wake_invoked_and_strips(t, q):
    invoked, question = match_wake(t, "Kiran")
    assert invoked is True
    assert question == q


@pytest.mark.parametrize("t", [
    "what's at the door?",                  # no name at all
    "turn off the lights",
    "I was talking to Kiran yesterday",     # name mid-sentence must NOT trigger
    "hey korean food place",               # 'korean' is a word, not a spelling
    "hey clearing the drive",
])
def test_match_wake_not_invoked(t):
    assert match_wake(t, "Kiran")[0] is False


def test_empty_transcript_not_invoked():
    assert match_wake("", "Kiran") == (False, "")


# ── "Hey" is required by default (Hey-Siri model) ──────────────────────

def test_require_prefix_blocks_bare_name_by_default():
    assert match_wake("kiran what do you see", "Kiran")[0] is False
    assert match_wake("Hey Kiran what do you see", "Kiran")[0] is True


def test_require_prefix_off_allows_bare_name():
    invoked, q = match_wake("kiran what do you see", "Kiran",
                            None, 1.0, require_prefix=False)
    assert invoked is True and q == "what do you see"


# ── extra English wake words (exact) ───────────────────────────────────

@pytest.mark.parametrize("t,tail", [
    ("Hey Camera, how many people?", "how many people?"),
    ("hey camera is the gate open", "is the gate open"),
])
def test_extra_wake_word_camera(t, tail):
    invoked, q = match_wake(t, "Kiran", ["camera"])
    assert invoked is True and q == tail


@pytest.mark.parametrize("t", [
    "hey calm down", "hey came back", "hey can you help",
    "hey come here", "hey camp out", "how many cameras are there",
])
def test_extra_wake_word_no_false_wake(t):
    assert match_wake(t, "Kiran", ["camera"])[0] is False


# ── tight fuzzy safety net (0.85): catches drift, rejects words ────────

def test_tight_fuzzy_catches_drift_rejects_words():
    # Known spellings match exactly; the 0.85 net catches close STT drift
    # ("kiraan" ~0.91) but still rejects real words (korean ~0.73).
    assert match_wake("hey kiran how many", "Kiran")[0] is True
    assert match_wake("hey kiraan how many", "Kiran")[0] is True    # close drift
    assert match_wake("hey korean food place", "Kiran")[0] is False  # real word
    assert match_wake("hey clearing this", "Kiran")[0] is False


def test_exact_only_when_fuzzy_one():
    # wake_fuzzy=1.0 → exact only; unregistered drift is dropped.
    assert match_wake("hey kiraan how many", "Kiran", None, 1.0)[0] is False
    assert match_wake("hey kiran how many", "Kiran", None, 1.0)[0] is True


def test_config_defaults():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")],
    )
    assert cfg.wake_word_required is True
    assert cfg.wake_fuzzy == 0.85
    assert cfg.agent_name == "Camera Agent"


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
    assert cfg.agent_name == "Camera Agent"

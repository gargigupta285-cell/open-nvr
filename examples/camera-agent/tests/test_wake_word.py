# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wake-word gating (voice): the agent only answers an utterance addressed to
it by name, so it ignores the TV, side-chatter, and its own echoed reply — the
main source of spurious/looping answers in a live room. Transcript-side, local."""
from __future__ import annotations

import pytest

import camera_agent as ca
from camera_agent import AppConfig, match_wake, wake_phrases
from context import CameraSpec


def test_wake_phrases_include_name_and_aliases():
    p = wake_phrases("Shailaja")
    assert "shailaja" in p and "shailu" in p
    p2 = wake_phrases("Sidhu")
    assert "sidhu" in p2 and "sid" in p2


@pytest.mark.parametrize("t,q", [
    ("Hey Shailaja, what's at the door?", "what's at the door?"),
    ("Shailaja how many people are there", "how many people are there"),
    ("shailu what do you see", "what do you see"),
    ("OK Shailaja, is the gate open?", "is the gate open?"),
    ("Hey Shailaja", ""),            # bare wake word → no question
])
def test_match_wake_invoked_and_strips(t, q):
    invoked, question = match_wake(t, "Shailaja")
    assert invoked is True
    assert question == q


@pytest.mark.parametrize("t", [
    "what's at the door?",                 # no name at all
    "turn off the lights",
    "I was talking to Shailaja yesterday",  # name mid-sentence must NOT trigger
])
def test_match_wake_not_invoked(t):
    invoked, _ = match_wake(t, "Shailaja")
    assert invoked is False


def test_short_name_uses_word_boundaries():
    # 'sid' must not match inside another word like 'consider'.
    invoked, _ = match_wake("consider the front gate", "Sidhu")
    assert invoked is False
    invoked2, q2 = match_wake("hey sid is anyone there", "Sidhu")
    assert invoked2 is True and q2 == "is anyone there"


def test_empty_transcript_not_invoked():
    assert match_wake("", "Shailaja") == (False, "")


def test_config_default_wake_required_true():
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        cameras=[CameraSpec(camera_id="c", frame_url="http://x/1.jpg", role="r")],
    )
    assert cfg.wake_word_required is True

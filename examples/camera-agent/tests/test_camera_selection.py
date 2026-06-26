# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for camera selection: an explicit name in the utterance wins;
otherwise the UI-selected ``preferred`` camera is used; otherwise the
first configured camera (current default)."""
from __future__ import annotations

from camera_agent import _pick_camera

CAMS = ["cam1", "cam2", "cam3"]


def test_explicit_name_in_text_wins_over_preferred():
    assert _pick_camera("what's on camera two?", CAMS, preferred="cam3") == "cam2"


def test_direct_id_in_text_wins():
    assert _pick_camera("show me cam3", CAMS, preferred="cam1") == "cam3"


def test_preferred_used_when_text_has_no_camera():
    assert _pick_camera("is anyone there?", CAMS, preferred="cam2") == "cam2"


def test_falls_back_to_first_when_no_preferred():
    assert _pick_camera("is anyone there?", CAMS) == "cam1"


def test_ignores_preferred_not_in_list():
    assert _pick_camera("anything?", CAMS, preferred="bogus") == "cam1"

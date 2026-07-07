# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistence: alarms, watches, and report schedules survive a restart via
the JSON state file."""
from __future__ import annotations

import asyncio
import json

from camera_agent import AppConfig, CameraAgentRuntime
from context import CameraSpec


def _runtime(state_path, emergency_contacts=None):
    cfg = AppConfig(
        kaic_url="http://k", kaic_api_key="x", system_prompt="t",
        state_path=str(state_path), emergency_contacts=emergency_contacts,
        cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="front"),
                 CameraSpec(camera_id="cam2", frame_url="http://x/2.jpg", role="gate")],
    )
    rt = CameraAgentRuntime(cfg)

    async def fake_get_frame(cam):
        return b"\xff\xd8\xff"

    async def fake_infer(*, frame_jpeg, **kw):
        return {"result": {"detections": []}}

    rt.context.get_frame = fake_get_frame
    rt.detection_client.infer = fake_infer
    return rt


def test_no_persistence_when_state_path_unset(tmp_path):
    cfg = AppConfig(kaic_url="http://k", kaic_api_key="x", system_prompt="t",
                    cameras=[CameraSpec(camera_id="cam1", frame_url="http://x/1.jpg", role="r")])
    rt = CameraAgentRuntime(cfg)
    rt.persist()  # must be a no-op, not raise


def test_definitions_persist_and_restore(tmp_path):
    state = tmp_path / "state.json"

    async def go1():
        rt = _runtime(state, emergency_contacts={"fire": "+1-555-0100"})
        await rt._handle_create_monitor({"kind": "count", "target": "person", "camera_id": "cam2"})
        await rt._handle_create_alarm({"name": "Fire", "target": "fire", "camera_id": "all", "after": "18:00"})
        await rt._handle_create_report({"name": "AM", "query": "overnight", "at": "07:00"})
        rt.monitors.stop_all(); rt.alarms.stop_all(); rt.reports.stop_all()
        return rt
    asyncio.run(go1())

    # file written with all three
    data = json.loads(state.read_text())
    assert len(data["monitors"]) == 1 and data["monitors"][0]["target"] == "person"
    assert len(data["alarms"]) == 1 and data["alarms"][0]["after_min"] == 18 * 60
    assert len(data["reports"]) == 1 and data["reports"][0]["query"] == "overnight"

    # a fresh runtime re-arms everything from disk
    async def go2():
        rt2 = _runtime(state, emergency_contacts={"fire": "+1-555-0100"})
        rt2.load_state()
        await asyncio.sleep(0.05)
        mons, alarms, reps = rt2.monitors.list(), rt2.alarms.list(), rt2.reports.list()
        rt2.monitors.stop_all(); rt2.alarms.stop_all(); rt2.reports.stop_all()
        return mons, alarms, reps
    mons, alarms, reps = asyncio.run(go2())

    assert [m["target"] for m in mons] == ["person"]
    assert alarms[0]["name"] == "Fire" and alarms[0]["window"] == "after 18:00"
    assert alarms[0]["emergency_contact_configured"] is True  # contact re-resolved from config
    assert reps[0]["query"] == "overnight" and reps[0]["schedule"] == "daily at 07:00"


def test_disabled_skill_survives_restart(tmp_path):
    """A skill switched off at runtime stays off after a restart, and its
    tool is not re-advertised to the LLM."""
    state = tmp_path / "skills.json"

    def _advertised(rt):
        return {t["function"]["name"] for t in rt.tool_definitions}

    # disable the always-available "see" skill (tool: describe_camera)
    rt = _runtime(state)
    assert "describe_camera" in _advertised(rt)
    assert rt.set_skill_enabled("see", False) is True
    assert "describe_camera" not in _advertised(rt)
    rt.persist()

    data = json.loads(state.read_text())
    assert data["disabled_skills"] == ["see"]

    # a fresh runtime restores the toggle and keeps the tool unadvertised
    rt2 = _runtime(state)
    assert "describe_camera" in _advertised(rt2)  # default-on before restore
    rt2.load_state()
    assert "see" in rt2.disabled_skills
    assert "describe_camera" not in _advertised(rt2)


def test_stop_updates_persisted_state(tmp_path):
    state = tmp_path / "s.json"

    async def go():
        rt = _runtime(state)
        await rt._handle_create_monitor({"kind": "notify", "target": "car", "camera_id": "cam1"})
        mid = rt.monitors.list()[0]["id"]
        await rt._handle_stop_monitor({"monitor_id": mid})
        rt.monitors.stop_all()
    asyncio.run(go())

    data = json.loads(state.read_text())
    assert data["monitors"] == []  # stopped monitor not persisted


def test_restore_default_skills_clears_toggles_and_persists(tmp_path):
    """Restore-defaults = fresh-boot state: every runtime toggle cleared,
    tools re-advertised, and the empty toggle set written to disk."""
    state = tmp_path / "skills.json"

    def _advertised(rt):
        return {t["function"]["name"] for t in rt.tool_definitions}

    rt = _runtime(state)
    assert rt.set_skill_enabled("alarm", False) is True
    assert rt.set_skill_enabled("watch", False) is True
    assert "create_alarm" not in _advertised(rt)
    assert "create_monitor" not in _advertised(rt)

    assert rt.restore_default_skills() == 2
    assert rt.disabled_skills == set()
    assert "create_alarm" in _advertised(rt)
    assert "create_monitor" in _advertised(rt)
    # durable: the cleared toggle set reached the state file
    data = json.loads(state.read_text())
    assert data["disabled_skills"] == []

    # idempotent: nothing left to restore, and no state churn
    assert rt.restore_default_skills() == 0

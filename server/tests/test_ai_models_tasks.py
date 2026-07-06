# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""
Tests for the canonical task taxonomy (contract §4): the
``server/config/tasks.yml`` registry, ``GET /ai-models/tasks``, and the
two pure helpers ``canonicalize_task`` / ``lint_task_names``.

Run with:

    cd server && pytest tests/test_ai_models_tasks.py -v

Mirrors the /use-cases + apps-registry test style: a real router on a
TestClient with auth overridden for the endpoint, and direct calls for
the pure functions (the surface a future conformance lint reuses).
"""

from __future__ import annotations

# Python 3.10 sandbox polyfill (see test_apps_registry.py).
import datetime as _dt

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # noqa: UP017

import os
import secrets
import sys
import types as _types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("MEDIAMTX_SECRET", secrets.token_hex(32))
os.environ.setdefault("INTERNAL_API_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

# Stub core.logging_config (same pattern as test_apps_registry.py).
_lm = _types.ModuleType("core.logging_config")


class _L:
    def __getattr__(self, name):
        return lambda *a, **kw: None


for _name in (
    "main_logger", "auth_logger", "camera_logger",
    "recording_logger", "cloud_logger", "ai_logger",
):
    setattr(_lm, _name, _L())
sys.modules["core.logging_config"] = _lm

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.auth import get_current_active_user  # noqa: E402
from core.database import get_db  # noqa: E402
from routers import ai_models  # noqa: E402
from routers.ai_models import (  # noqa: E402
    TaskEntry,
    _load_tasks_registry,
    canonicalize_task,
    lint_task_names,
)


class _StubUser:
    id = 1
    username = "tester"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ai_models.router)
    app.dependency_overrides[get_current_active_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = lambda: iter([None])
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def registry() -> list[TaskEntry]:
    return _load_tasks_registry()


# ─── the shipped registry ───────────────────────────────────────────────


def test_registry_seeds_all_tasks_in_use(registry):
    """Every task actually advertised in the v0.1 set is canonicalized."""
    names = {e.task for e in registry}
    for expected in (
        "object_detection", "face_recognition", "image_captioning",
        "vqa", "face_detection", "person_detection",
        "license_plate_recognition", "multi_object_tracking",
        "speech_to_text", "text_to_speech",
    ):
        assert expected in names, expected


def test_scene_caption_is_an_alias_of_image_captioning(registry):
    """The canonical-alias case: scene_caption and image_captioning are
    ONE capability (contract §4 duplication resolution)."""
    cap = next(e for e in registry if e.task == "image_captioning")
    assert "scene_caption" in cap.aliases
    # And not a task in its own right.
    assert "scene_caption" not in {e.task for e in registry}


def test_agent_skill_mapping_present(registry):
    by_task = {e.task: e for e in registry}
    assert by_task["object_detection"].agent_skill == "count"
    assert by_task["face_recognition"].agent_skill == "faces"
    assert by_task["image_captioning"].agent_skill == "see"
    assert by_task["vqa"].agent_skill == "see"
    # Tasks that back no agent skill are null, not absent.
    assert by_task["speech_to_text"].agent_skill is None


# ─── canonicalize_task ──────────────────────────────────────────────────


def test_canonicalize_passthrough_for_canonical(registry):
    assert canonicalize_task("object_detection", registry) == "object_detection"


def test_canonicalize_folds_alias_to_canonical(registry):
    assert canonicalize_task("scene_caption", registry) == "image_captioning"
    assert canonicalize_task("detect_objects", registry) == "object_detection"
    assert canonicalize_task("object-detection", registry) == "object_detection"


def test_canonicalize_is_case_insensitive(registry):
    assert canonicalize_task("Scene_Caption", registry) == "image_captioning"
    assert canonicalize_task("OBJECT_DETECTION", registry) == "object_detection"


def test_canonicalize_unknown_returns_unchanged(registry):
    """Free-text tasks are preserved verbatim (§15.1)."""
    assert canonicalize_task("weapon_detection", registry) == "weapon_detection"
    assert canonicalize_task("", registry) == ""


# ─── lint_task_names ────────────────────────────────────────────────────


def test_lint_clean_for_canonical(registry):
    assert lint_task_names(["object_detection", "vqa"], registry) == []


def test_lint_flags_alias_with_canonical_suggestion(registry):
    warnings = lint_task_names(["scene_caption"], registry)
    assert len(warnings) == 1
    assert "scene_caption" in warnings[0]
    assert "image_captioning" in warnings[0]
    assert "prefer the canonical name" in warnings[0]


def test_lint_flags_unknown_as_uncategorized(registry):
    warnings = lint_task_names(["foo_bar"], registry)
    assert len(warnings) == 1
    assert "foo_bar" in warnings[0]
    assert "uncategorized" in warnings[0]


def test_lint_mixes_clean_alias_and_unknown(registry):
    warnings = lint_task_names(
        ["object_detection", "scene_caption", "foo_bar"], registry
    )
    # Canonical produces nothing; the other two each warn once.
    assert len(warnings) == 2
    assert any("alias" in w for w in warnings)
    assert any("uncategorized" in w for w in warnings)


# ─── lint_and_log_adapter_tasks (advisory logging) ──────────────────────


def test_lint_and_log_warns_for_non_canonical(registry, monkeypatch):
    """A non-canonical (alias) task produces a logged warning naming the
    adapter and nudging toward the canonical spelling."""
    from routers.ai_models import _lint_seen, lint_and_log_adapter_tasks

    _lint_seen.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(
        ai_models.main_logger,
        "warning",
        lambda *a, **kw: calls.append((a, kw)),
    )
    lint_and_log_adapter_tasks("caption-adapter", ["scene_caption"], registry)
    assert len(calls) == 1
    rendered = calls[0][0][0] % calls[0][0][1:]
    assert "caption-adapter" in rendered
    assert "scene_caption" in rendered
    assert "image_captioning" in rendered


def test_lint_and_log_silent_for_canonical(registry, monkeypatch):
    """A fully-canonical taskset logs nothing."""
    from routers.ai_models import _lint_seen, lint_and_log_adapter_tasks

    _lint_seen.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(
        ai_models.main_logger,
        "warning",
        lambda *a, **kw: calls.append((a, kw)),
    )
    lint_and_log_adapter_tasks(
        "clean-adapter", ["object_detection", "vqa"], registry
    )
    assert calls == []


def test_lint_and_log_dedupes_per_adapter_taskset(registry, monkeypatch):
    """The same (adapter, taskset) warns exactly once even across
    repeated polls."""
    from routers.ai_models import _lint_seen, lint_and_log_adapter_tasks

    _lint_seen.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(
        ai_models.main_logger,
        "warning",
        lambda *a, **kw: calls.append((a, kw)),
    )
    for _ in range(3):
        lint_and_log_adapter_tasks("dup-adapter", ["scene_caption"], registry)
    assert len(calls) == 1


# ─── GET /ai-models/tasks ───────────────────────────────────────────────


def test_tasks_endpoint_serves_registry(client):
    resp = client.get("/ai-models/tasks")
    assert resp.status_code == 200
    body = resp.json()
    by_task = {e["task"]: e for e in body}
    assert "object_detection" in by_task
    entry = by_task["image_captioning"]
    # The full validated entry shape rides the wire.
    for key in ("task", "label", "summary", "categories", "tags",
                "agent_skill", "aliases"):
        assert key in entry
    assert "scene_caption" in entry["aliases"]
    assert entry["agent_skill"] == "see"

# Copyright (c) 2026 OpenNVR
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
"""Tests for the App Store submission gate (``scripts/validate_apps_index.py``).

Run with:

    cd server && pytest tests/test_validate_apps_index.py -v

The validator is stdlib + PyYAML only (no server import), so these tests
don't need Postgres, secrets, or the FastAPI app — they import the script
directly and drive its pure functions. Two jobs:

* prove the SHIPPED ``server/config/apps_index.yml`` passes (0 errors), so a
  malformed community submission is caught in CI before merge — the same
  thing ``make validate-apps-index`` runs;
* prove each individual guard actually rejects a bad entry (missing field,
  non-kebab / duplicate id, bad image ref, malformed digest, plaintext
  secret, empty install) and that an unknown task only WARNS.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "validate_apps_index.py"
_SHIPPED_INDEX = REPO_ROOT / "server" / "config" / "apps_index.yml"


def _load_validator():
    """Import validate_apps_index.py by path (it lives outside a package)."""
    spec = importlib.util.spec_from_file_location("validate_apps_index", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


# A minimal, valid entry the negative tests mutate one field at a time.
_GOOD_ENTRY = {
    "id": "sample-app",
    "name": "Sample App",
    "summary": "A sample app for testing.",
    "category": "perimeter",
    "version": "1.0.0",
    "image": "ghcr.io/open-nvr/sample-app:latest",
    "requires_tasks": ["object_detection"],
    "docs_url": "examples/sample-app/README.md",
    "install": {
        "compose": (
            "services:\n"
            "  sample-app:\n"
            "    image: ghcr.io/open-nvr/sample-app:latest\n"
            "    environment:\n"
            "      - OPENNVR_INTERNAL_API_KEY=${INTERNAL_API_KEY}\n"
        ),
        "command": "docker compose up -d sample-app",
    },
}


def _write(tmp_path: Path, entries: list) -> Path:
    path = tmp_path / "apps_index.yml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False))
    return path


# ─── The shipped index passes (this is the CI gate) ─────────────────────


def test_shipped_index_passes():
    """server/config/apps_index.yml must validate clean — 0 errors."""
    errors, _warnings = validator.validate_index(_SHIPPED_INDEX)
    assert errors == [], "shipped apps_index.yml failed validation:\n" + "\n".join(errors)


def test_shipped_index_main_exits_zero():
    """The CLI entry point (what ``make validate-apps-index`` runs) exits 0."""
    assert validator.main(["prog", str(_SHIPPED_INDEX)]) == 0


def test_good_entry_passes(tmp_path):
    """A single well-formed entry has no errors and no warnings."""
    errors, warnings = validator.validate_index(_write(tmp_path, [dict(_GOOD_ENTRY)]))
    assert errors == []
    assert warnings == []


def test_optional_digest_valid_form_passes(tmp_path):
    """A correctly formed sha256 digest is accepted."""
    entry = dict(_GOOD_ENTRY)
    entry["image_digest"] = "sha256:" + "a" * 64
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert errors == []


# ─── Each guard rejects a bad entry ─────────────────────────────────────


@pytest.mark.parametrize("field", ["id", "name", "summary", "image", "docs_url", "install"])
def test_missing_required_field_fails(tmp_path, field):
    entry = dict(_GOOD_ENTRY)
    del entry[field]
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any(field in e for e in errors), errors


def test_non_kebab_id_fails(tmp_path):
    entry = dict(_GOOD_ENTRY, id="Sample_App")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("kebab-case" in e for e in errors), errors


def test_duplicate_id_fails(tmp_path):
    errors, _ = validator.validate_index(
        _write(tmp_path, [dict(_GOOD_ENTRY), dict(_GOOD_ENTRY)])
    )
    assert any("duplicate id" in e for e in errors), errors


def test_bad_image_ref_fails(tmp_path):
    entry = dict(_GOOD_ENTRY, image="docker.io/evil/thing:latest")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("well-formed ref" in e for e in errors), errors


def test_opennvr_image_ref_passes(tmp_path):
    entry = dict(_GOOD_ENTRY, image="opennvr/sample-app:local-build")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert errors == []


def test_malformed_digest_fails(tmp_path):
    entry = dict(_GOOD_ENTRY, image_digest="sha256:not-hex")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("image_digest" in e for e in errors), errors


def test_plaintext_secret_in_compose_fails(tmp_path):
    entry = dict(_GOOD_ENTRY)
    entry["install"] = dict(_GOOD_ENTRY["install"])
    entry["install"]["compose"] = (
        "services:\n"
        "  sample-app:\n"
        "    environment:\n"
        "      - OPENNVR_API_KEY=hunter2literalvalue\n"
    )
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("plaintext secret" in e for e in errors), errors


def test_placeholder_secret_in_compose_passes(tmp_path):
    """A ${VAR} placeholder is the correct way to reference a secret."""
    errors, _ = validator.validate_index(_write(tmp_path, [dict(_GOOD_ENTRY)]))
    assert not any("plaintext secret" in e for e in errors), errors


def test_empty_install_command_fails(tmp_path):
    entry = dict(_GOOD_ENTRY)
    entry["install"] = dict(_GOOD_ENTRY["install"], command="")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("install.command" in e for e in errors), errors


def test_empty_install_compose_fails(tmp_path):
    entry = dict(_GOOD_ENTRY)
    entry["install"] = dict(_GOOD_ENTRY["install"], compose="   ")
    errors, _ = validator.validate_index(_write(tmp_path, [entry]))
    assert any("install.compose" in e for e in errors), errors


# ─── Unknown task only warns (free-text is allowed, §4 of the contract) ─


def test_unknown_task_warns_not_fails(tmp_path):
    entry = dict(_GOOD_ENTRY, requires_tasks=["telepathy"])
    errors, warnings = validator.validate_index(_write(tmp_path, [entry]))
    assert errors == []
    assert any("telepathy" in w for w in warnings), warnings


def test_known_task_alias_does_not_warn(tmp_path):
    """object_tracking is a known alias of multi_object_tracking — no warning."""
    entry = dict(_GOOD_ENTRY, requires_tasks=["object_tracking"])
    _errors, warnings = validator.validate_index(_write(tmp_path, [entry]))
    assert warnings == []


# ─── Structural failures ────────────────────────────────────────────────


def test_missing_file_fails():
    errors, _ = validator.validate_index(Path("/nonexistent/apps_index.yml"))
    assert any("not found" in e for e in errors), errors


def test_top_level_not_a_list_fails(tmp_path):
    path = tmp_path / "apps_index.yml"
    path.write_text("id: not-a-list\n")
    errors, _ = validator.validate_index(path)
    assert any("must be a list" in e for e in errors), errors

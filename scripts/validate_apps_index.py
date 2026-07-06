#!/usr/bin/env python3
# Copyright (c) 2026 OpenNVR
# This file is part of OpenNVR.
#
# OpenNVR is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenNVR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.

"""Validate ``server/config/apps_index.yml`` — the App Store submission gate.

This is the CI / pre-PR check a community app submission has to pass before
it lands in the curated index (see ``docs/CONTRIBUTING_APPS.md``). It is
deliberately **stdlib + PyYAML only** — no server import required — so a
contributor can run it in a clean checkout without booting Postgres or
``uv sync``-ing the backend:

    python3 scripts/validate_apps_index.py            # shipped index
    python3 scripts/validate_apps_index.py path/to/index.yml
    make validate-apps-index

What it enforces (one clear message per offending entry, non-zero exit on
any hard failure):

* every entry has the required fields the ``IndexEntry`` pydantic model
  needs — ``id, name, summary, category, version, image, requires_tasks,
  docs_url, install`` — and ``install`` carries a non-empty ``compose`` and
  ``command``;
* ``id`` is unique across the file and kebab-case (``[a-z0-9-]``);
* ``image`` is a well-formed ref (``ghcr.io/...`` or ``opennvr/...``);
* ``image_digest``, when present, is ``sha256:`` + 64 lowercase hex;
* NO plaintext secrets — the compose block may reference ``${VAR}``
  placeholders but must not bake a literal key/password/token.

What it only WARNS about (free-text is allowed by the adapter contract §4,
so an unknown task is a nudge, not a rejection):

* a ``requires_tasks`` entry that is not one of the known canonical task
  names (or a known alias) curated in ``server/config/use_case_map.yml``.

Warnings go to stderr and do NOT change the exit code; hard failures do.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX = REPO_ROOT / "server" / "config" / "apps_index.yml"
USE_CASE_MAP = REPO_ROOT / "server" / "config" / "use_case_map.yml"

# Required top-level fields, mirroring the IndexEntry pydantic model in
# server/routers/apps.py (build_context / emits / image_digest are optional).
REQUIRED_FIELDS = (
    "id",
    "name",
    "summary",
    "category",
    "version",
    "image",
    "requires_tasks",
    "docs_url",
    "install",
)

# id must be kebab-case: lowercase alnum words joined by single hyphens.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# image ref: a ghcr.io/... path or an opennvr/... path, optionally :tagged.
# (The digest lives in image_digest, not here.)
_IMAGE_RE = re.compile(r"^(?:ghcr\.io/[a-z0-9._/-]+|opennvr/[a-z0-9._/-]+)(?::[a-zA-Z0-9._-]+)?$")

# A published-image digest is sha256 + exactly 64 lowercase hex chars.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# A ${VAR} / $VAR placeholder — allowed anywhere a value could hold a secret.
_PLACEHOLDER_RE = re.compile(r"\$\{?[A-Z_][A-Z0-9_]*\}?")

# Heuristic secret sniff: a KEY/TOKEN/PASSWORD/SECRET assignment whose value
# is NOT a ${VAR} placeholder. Catches a baked literal without flagging the
# legitimate ``- OPENNVR_INTERNAL_API_KEY=${INTERNAL_API_KEY}`` line.
_SECRETISH_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|passwd|token|access[_-]?key|private[_-]?key)"
)

# Canonical task aliases: the index historically uses object_tracking where
# use_case_map curates multi_object_tracking; ocr where the LPR use case is
# license_plate_recognition. Both sides are accepted (alias -> canonical).
_TASK_ALIASES = {
    "object_tracking": "multi_object_tracking",
    "multi_object_tracking": "multi_object_tracking",
    "ocr": "ocr",
}


def _load_canonical_tasks() -> set[str]:
    """The known-good task vocabulary, curated in use_case_map.yml.

    Reads every ``needs_capability`` + ``also_needs`` string from the
    product-owned use-case map and folds in the known aliases. Missing /
    unreadable map ⇒ the alias set alone (validator still runs, warnings
    just widen). Never raises — the task check is advisory.
    """
    tasks: set[str] = set(_TASK_ALIASES) | set(_TASK_ALIASES.values())
    try:
        rows = yaml.safe_load(USE_CASE_MAP.read_text()) or []
    except Exception:
        return tasks
    for row in rows:
        if not isinstance(row, dict):
            continue
        cap = row.get("needs_capability")
        if isinstance(cap, str):
            tasks.add(cap)
        for extra in row.get("also_needs") or []:
            if isinstance(extra, str):
                tasks.add(extra)
    return tasks


def _entry_label(entry: object, index: int) -> str:
    """Human-readable handle for an entry in error messages."""
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return f"entry '{entry['id']}'"
    return f"entry #{index} (no valid id)"


def _looks_like_baked_secret(compose: str) -> list[str]:
    """Return offending lines where a secret-ish key has a literal value.

    A value that is exactly a ``${VAR}`` / ``$VAR`` placeholder (optionally
    quoted / empty) is fine. Anything else assigned to a secret-ish key is a
    hard failure — no plaintext secret ever ships in the curated index.
    """
    offenders: list[str] = []
    for raw in compose.splitlines():
        line = raw.strip().lstrip("-").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not _SECRETISH_KEY_RE.search(key):
            continue
        # Drop a trailing inline `# comment` (compose keeps it as literal
        # text in a `|` block, but it isn't part of the value) before the
        # placeholder check, so a documented `${VAR}  # note` line is fine.
        value = value.split("#", 1)[0].strip().strip('"').strip("'")
        if value == "" or _PLACEHOLDER_RE.fullmatch(value):
            continue
        offenders.append(raw.strip())
    return offenders


def validate_entry(
    entry: object,
    index: int,
    seen_ids: set[str],
    canonical_tasks: set[str],
) -> tuple[list[str], list[str]]:
    """Validate one index entry.

    Returns ``(errors, warnings)`` — errors fail the build, warnings only
    print. Each string is prefixed with the entry label so the caller can
    emit them verbatim.
    """
    errors: list[str] = []
    warnings: list[str] = []
    label = _entry_label(entry, index)

    if not isinstance(entry, dict):
        return [f"{label}: not a mapping (got {type(entry).__name__})"], warnings

    # Required fields present + non-empty.
    for field in REQUIRED_FIELDS:
        if field not in entry:
            errors.append(f"{label}: missing required field '{field}'")
        elif field != "requires_tasks" and not entry[field]:
            errors.append(f"{label}: field '{field}' is empty")

    # id: kebab-case + unique.
    app_id = entry.get("id")
    if isinstance(app_id, str):
        if not _KEBAB_RE.match(app_id):
            errors.append(
                f"{label}: id '{app_id}' is not kebab-case "
                "(lowercase letters, digits, single hyphens)"
            )
        if app_id in seen_ids:
            errors.append(f"{label}: duplicate id '{app_id}'")
        seen_ids.add(app_id)

    # image: well-formed ghcr.io/... or opennvr/... ref.
    image = entry.get("image")
    if isinstance(image, str) and image and not _IMAGE_RE.match(image):
        errors.append(
            f"{label}: image '{image}' is not a well-formed ref "
            "(expected ghcr.io/... or opennvr/...)"
        )

    # image_digest: optional, but must be sha256:<64 hex> when present.
    digest = entry.get("image_digest")
    if digest is not None:
        if not (isinstance(digest, str) and _DIGEST_RE.match(digest)):
            errors.append(
                f"{label}: image_digest '{digest}' must match "
                "sha256:<64 lowercase hex chars>"
            )

    # requires_tasks: must be a list; unknown tasks warn (free-text allowed).
    tasks = entry.get("requires_tasks", [])
    if not isinstance(tasks, list):
        errors.append(f"{label}: requires_tasks must be a list")
    else:
        for task in tasks:
            if not isinstance(task, str):
                errors.append(f"{label}: requires_tasks entry {task!r} is not a string")
            elif task not in canonical_tasks:
                warnings.append(
                    f"{label}: requires_tasks '{task}' is not a known canonical "
                    "task (see server/config/use_case_map.yml). Free-text is "
                    "allowed, but prefer a canonical name if one fits."
                )

    # install: compose + command both present and non-empty.
    install = entry.get("install")
    if not isinstance(install, dict):
        errors.append(f"{label}: install must be a mapping with compose + command")
    else:
        compose = install.get("compose")
        command = install.get("command")
        if not (isinstance(compose, str) and compose.strip()):
            errors.append(f"{label}: install.compose is missing or empty")
        if not (isinstance(command, str) and command.strip()):
            errors.append(f"{label}: install.command is missing or empty")
        # No plaintext secrets anywhere in the compose block.
        if isinstance(compose, str):
            for offender in _looks_like_baked_secret(compose):
                errors.append(
                    f"{label}: install.compose contains a plaintext secret "
                    f"(use a ${{VAR}} placeholder): {offender!r}"
                )

    return errors, warnings


def validate_index(path: Path) -> tuple[list[str], list[str]]:
    """Validate an entire apps index file. Returns ``(errors, warnings)``."""
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return [f"index file not found: {path}"], []
    except yaml.YAMLError as exc:
        return [f"{path}: YAML parse error: {exc}"], []

    if raw is None:
        return [f"{path}: index is empty"], []
    if not isinstance(raw, list):
        return [f"{path}: top level must be a list of entries, got {type(raw).__name__}"], []

    canonical_tasks = _load_canonical_tasks()
    seen_ids: set[str] = set()
    errors: list[str] = []
    warnings: list[str] = []
    for i, entry in enumerate(raw):
        entry_errors, entry_warnings = validate_entry(entry, i, seen_ids, canonical_tasks)
        errors.extend(entry_errors)
        warnings.extend(entry_warnings)
    return errors, warnings


def main(argv: list[str]) -> int:
    path = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_INDEX
    errors, warnings = validate_index(path)

    for warning in warnings:
        print(f"WARN  {warning}", file=sys.stderr)

    if errors:
        print(f"\napps_index validation FAILED ({len(errors)} error(s)) — {path}", file=sys.stderr)
        for error in errors:
            print(f"  ERROR {error}", file=sys.stderr)
        return 1

    n = 0
    try:
        n = len(yaml.safe_load(path.read_text()) or [])
    except Exception:
        pass
    warn_note = f" ({len(warnings)} warning(s))" if warnings else ""
    print(f"apps_index OK — {n} entr{'y' if n == 1 else 'ies'} validated{warn_note}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

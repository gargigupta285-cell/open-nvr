#!/usr/bin/env python3
# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
create_opennvr_app — scaffold a new OpenNVR Detector app.

Copies ``templates/opennvr-app`` into ``<dest>/<app-id>/``, substitutes
the placeholder tokens, renames the app module, and prints the next
steps. Stdlib only — no network, no side effects beyond writing the new
directory.

Usage::

    python scripts/create_opennvr_app.py <app-id> [--task object_detection] [--dest examples/]

Example::

    python scripts/create_opennvr_app.py package-watch --task object_detection

Tokens substituted in every template file (and in file names):

    __APP_ID__      kebab-case id            package-watch
    __APP_MODULE__  snake_case module name   package_watch
    __APP_CLASS__   PascalCase class name    PackageWatch
    __APP_NAME__    Title-cased human name   Package Watch
    __TASK__        adapter task (--task)    object_detection

The generated app's smoke test passes against the real SDK out of the
box (``cd <dest>/<app-id> && uv sync && uv run pytest -q``).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Repo root is the parent of scripts/. Templates + default dest are
# resolved relative to it so the generator works from any CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_DIR = _REPO_ROOT / "templates" / "opennvr-app"
_SDK_DIR = _REPO_ROOT / "sdk" / "opennvr-app-sdk"

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The template module file carries the __APP_MODULE__ token in its name.
_MODULE_FILE_TOKEN = "__APP_MODULE__"


def kebab_to_snake(app_id: str) -> str:
    return app_id.replace("-", "_")


def kebab_to_pascal(app_id: str) -> str:
    return "".join(part.capitalize() for part in app_id.split("-"))


def kebab_to_title(app_id: str) -> str:
    return " ".join(part.capitalize() for part in app_id.split("-"))


def sdk_path_for(app_dir: Path) -> str:
    """The editable SDK path written into the generated pyproject.

    Relative when the app lives inside the repo (keeps in-tree apps
    portable — ``examples/<id>/`` → ``../../sdk/opennvr-app-sdk``);
    absolute for an out-of-tree ``--dest`` so ``uv sync`` still resolves
    it. Always forward-slashed so the TOML is platform-neutral."""
    try:
        rel = os.path.relpath(_SDK_DIR, app_dir)
        # Only prefer the relative form when it actually stays a tidy
        # ``../`` walk; a relpath that bounces through the filesystem
        # root (different drive on Windows) raises ValueError below.
        return Path(rel).as_posix()
    except ValueError:
        return _SDK_DIR.as_posix()


def build_tokens(app_id: str, task: str, app_dir: Path) -> dict[str, str]:
    """The token → replacement map applied to file contents and names."""
    return {
        "__APP_ID__": app_id,
        "__APP_MODULE__": kebab_to_snake(app_id),
        "__APP_CLASS__": kebab_to_pascal(app_id),
        "__APP_NAME__": kebab_to_title(app_id),
        "__TASK__": task,
        "__SDK_PATH__": sdk_path_for(app_dir),
    }


def substitute(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def rename_path_part(part: str, tokens: dict[str, str]) -> str:
    """Substitute tokens that appear in a path component (e.g. the app
    module file name ``__APP_MODULE__.py``)."""
    return substitute(part, tokens)


def generate(app_id: str, task: str, dest_dir: Path) -> Path:
    """Render the template into ``dest_dir/<app-id>/``. Returns the path
    to the created app directory. Raises on validation failures."""
    if not _KEBAB_RE.match(app_id):
        raise ValueError(
            f"app-id {app_id!r} is not kebab-case — use lowercase letters, "
            f"digits, and single hyphens (e.g. 'package-watch')"
        )
    if not _TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(
            f"template directory not found: {_TEMPLATE_DIR}"
        )

    app_dir = dest_dir / app_id
    if app_dir.exists():
        raise FileExistsError(f"destination already exists: {app_dir}")

    tokens = build_tokens(app_id, task, app_dir)

    # Walk the template tree, copying every file with tokens substituted
    # in both its path and its contents. Skip transient dirs.
    _SKIP = {"__pycache__", ".venv", ".pytest_cache", "uv.lock"}
    for src in sorted(_TEMPLATE_DIR.rglob("*")):
        rel_parts = src.relative_to(_TEMPLATE_DIR).parts
        if any(p in _SKIP for p in rel_parts):
            continue
        dst_parts = [rename_path_part(p, tokens) for p in rel_parts]
        dst = app_dir.joinpath(*dst_parts)
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_text(encoding="utf-8")
        dst.write_text(substitute(content, tokens), encoding="utf-8")

    return app_dir


def _print_next_steps(app_id: str, app_dir: Path) -> None:
    module = kebab_to_snake(app_id)
    try:
        rel = app_dir.relative_to(Path.cwd())
    except ValueError:
        rel = app_dir
    print(f"\nScaffolded {app_id!r} at {app_dir}\n")
    print("Next steps:")
    print(f"  cd {rel}")
    print("  uv sync                 # install the SDK (editable) + pytest")
    print("  uv run pytest -q        # the smoke test — should be GREEN")
    print(f"  # open {module}.py and fill in on_detections — that's the rule")
    print(f"  cp config.example.yml config.yml   # then edit it")
    print(f"  uv run python {module}.py --config config.yml --once")
    print("\nRun it against the stack + publish to the App Store:")
    print("  docs/FIRST_DETECTOR.md      # the 15-minute walkthrough")
    print("  docs/CONTRIBUTING_APPS.md   # add it to the curated app index")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="create_opennvr_app",
        description="Scaffold a new OpenNVR Detector app from the template.",
    )
    parser.add_argument(
        "app_id",
        help="kebab-case app id, e.g. 'package-watch' (matches AppManifest.id)",
    )
    parser.add_argument(
        "--task",
        default="object_detection",
        help="adapter task the app requires (requires_tasks). "
             "Default: object_detection.",
    )
    parser.add_argument(
        "--dest",
        default=str(_REPO_ROOT / "examples"),
        help="parent directory to create <app-id>/ under. Default: examples/.",
    )
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest).expanduser().resolve()
    try:
        app_dir = generate(args.app_id, args.task, dest_dir)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_next_steps(args.app_id, app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

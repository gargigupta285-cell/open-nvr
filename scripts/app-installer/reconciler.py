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

"""OpenNVR App Installer — the single privileged reconciler.

This is the ONLY OpenNVR component that holds the Docker socket. The web
app never runs Docker; it only writes desired-state rows into the
``app_install_intents`` table (see server/routers/apps.py). This
reconciler polls that table and drives the actual ``docker compose``
up/down for each intent, then writes back the reconcile ``status``.

Design (desired-state + reconciler):

* Read every intent row (id, image, image_digest, desired, status).
* For a ``desired="installed"`` row that isn't already applied → run
  ``docker compose ... up -d <id>`` and, when a digest is present, pin
  the image to ``image@sha256:...``; on success mark ``status="applied"``,
  on non-zero exit mark ``status="failed"`` with the stderr in
  ``message``.
* For a ``desired="absent"`` row → run ``docker compose ... down`` /
  ``stop+rm`` for that service and mark ``status="applied"`` (removed).
* An ``image_digest`` of ``None`` means UNPINNED — a loud warning is
  logged and the run is documented as dev-only (do not run unpinned in
  production).

Testability: the docker/subprocess call is injected as a ``runner``
callable so unit tests pass a fake runner and never touch real Docker.
The DB access is likewise injected as a ``store`` so tests use an
in-memory fake. Nothing here is network-facing.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger("opennvr.app-installer")

# The compose files the reconciler drives, in overlay order. The apps
# overlay carries the per-app service blocks; the base file carries the
# core services they depend on / share a network with.
DEFAULT_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.apps.yml")
# Compose profile the app service blocks live under (see
# docker-compose.apps.yml — every app service is ``profiles: [apps]``).
DEFAULT_PROFILE = "apps"


# ── Injected seams ─────────────────────────────────────────────────────


@dataclass
class RunResult:
    """The outcome of one injected command run."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


# A ``runner`` takes the argv list plus an optional env override and
# returns a RunResult. The real runner shells out to ``docker`` with the
# override merged over ``os.environ``; tests pass a fake that records
# argv + env and returns a canned RunResult — no Docker involved. The
# env carries the per-service digest-pin (``<ID>_IMAGE=image@sha256:…``)
# so pinning actually reaches ``docker compose``.
Runner = Callable[[list[str], "dict[str, str] | None"], RunResult]


@dataclass
class Intent:
    """One desired-state row, decoupled from SQLAlchemy so the reconciler
    core is trivially unit-testable."""

    id: str
    image: str
    image_digest: str | None
    desired: str  # "installed" | "absent"
    status: str  # "pending" | "applied" | "failed"


class IntentStore(Protocol):
    """The persistence seam. The production impl is a thin SQLAlchemy
    reader/writer over ``app_install_intents``; tests use a fake dict."""

    def list_intents(self) -> list[Intent]:
        ...

    def set_status(self, intent_id: str, status: str, message: str) -> None:
        ...


# ── The real docker runner (production only; never imported by tests) ──


def docker_runner(
    argv: list[str], env: dict[str, str] | None = None
) -> RunResult:
    """Shell out to ``docker`` (or any argv). Production runner only.

    Kept dead simple and side-effect-only so the interesting logic stays
    in the pure reconcile functions below, which take an injected runner.

    ``env`` (when given) is the per-service image-override the installer
    sets for digest pinning (``<ID>_IMAGE=image@sha256:…``). It is MERGED
    OVER ``os.environ`` — not a replacement — so ``docker compose`` keeps
    the operator's PATH / DOCKER_HOST / .env context and only the pin is
    added. ``argv`` stays a plain list (no shell), so nothing here is
    injectable regardless of the env values.
    """
    run_env: dict[str, str] | None = None
    if env:
        run_env = {**os.environ, **env}
    proc = subprocess.run(  # noqa: S603 — argv is built from curated index data
        argv,
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )
    return RunResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


# ── Compose argv builders ──────────────────────────────────────────────


def _compose_base(compose_files: tuple[str, ...], profile: str) -> list[str]:
    argv = ["docker", "compose"]
    for f in compose_files:
        argv += ["-f", f]
    argv += ["--profile", profile]
    return argv


def build_up_argv(
    intent: Intent,
    *,
    compose_files: tuple[str, ...] = DEFAULT_COMPOSE_FILES,
    profile: str = DEFAULT_PROFILE,
) -> list[str]:
    """``docker compose ... up -d <id>`` for one app service.

    Digest pinning: when the intent carries an ``image_digest``, the
    pinned ref ``image@sha256:...`` is passed to compose via the
    per-service image override env the app compose blocks read
    (``<ID>_IMAGE``), so the reconciler deploys the exact bytes the
    curated index vouched for. When absent, the service's own ``image:``
    default is used (unpinned — dev only).
    """
    return _compose_base(compose_files, profile) + ["up", "-d", intent.id]


def build_down_argv(
    intent: Intent,
    *,
    compose_files: tuple[str, ...] = DEFAULT_COMPOSE_FILES,
    profile: str = DEFAULT_PROFILE,
) -> list[str]:
    """``docker compose ... rm -s -f <id>`` — stop and remove exactly the
    one app service, leaving the rest of the stack untouched."""
    return _compose_base(compose_files, profile) + ["rm", "-s", "-f", intent.id]


def image_env_key(app_id: str) -> str:
    """The compose image-override env var name for one app id.

    The SINGLE source of truth for the ``app-id → ENV`` transform: the id
    is upper-snake-cased and suffixed ``_IMAGE`` (e.g.
    ``license-plate-recognition`` → ``LICENSE_PLATE_RECOGNITION_IMAGE``).
    docker-compose.apps.yml reads exactly this var
    (``image: ${<KEY>:-opennvr/<id>:local-build}``), so this transform
    and the compose file MUST stay in lock-step — it is unit-tested."""
    return app_id.upper().replace("-", "_") + "_IMAGE"


def pinned_image_ref(intent: Intent) -> str | None:
    """The digest-pinned image ref (``image@sha256:...``) or None when
    unpinned. Exposed so the runner/env wiring and tests agree on the
    exact pin string."""
    if not intent.image_digest:
        return None
    digest = intent.image_digest
    # Accept both "sha256:abc..." and a bare "abc..." digest.
    if not digest.startswith("sha256:"):
        digest = f"sha256:{digest}"
    # Strip any existing tag before appending the digest.
    base = intent.image.split("@", 1)[0]
    base = base.rsplit(":", 1)[0] if ":" in base.split("/")[-1] else base
    return f"{base}@{digest}"


# ── Reconcile core (pure logic + injected runner/store) ────────────────


def _run_env(intent: Intent) -> dict[str, str]:
    """The per-service image-override env the runner should apply. Only
    set when the intent is pinned; the key mirrors the app compose
    block's ``${<ID>_IMAGE}`` override slot."""
    pin = pinned_image_ref(intent)
    if pin is None:
        return {}
    return {image_env_key(intent.id): pin}


def reconcile_intent(
    intent: Intent,
    runner: Runner,
    *,
    compose_files: tuple[str, ...] = DEFAULT_COMPOSE_FILES,
    profile: str = DEFAULT_PROFILE,
) -> tuple[str, str]:
    """Reconcile ONE intent. Returns ``(status, message)``.

    Pure except for the injected ``runner`` — no Docker, no DB. This is
    the function the unit tests drive with a fake runner.
    """
    if intent.desired == "installed":
        pin = pinned_image_ref(intent)
        # The env override is what actually pins the deploy: when a digest
        # is present ``_run_env`` yields ``{<ID>_IMAGE: image@sha256:…}``,
        # which the app's compose ``image:`` line reads; when absent it is
        # empty (``{}``) so compose falls back to the local-build default
        # AND we log the loud UNPINNED warning below.
        override = _run_env(intent)
        if pin is None:
            logger.warning(
                "UNPINNED — dev only: app %r has no image_digest; deploying "
                "%r without supply-chain pinning. Do NOT run unpinned images "
                "in production — add an image_digest to the curated index.",
                intent.id,
                intent.image,
            )
        else:
            logger.info("Pinning app %r to %s", intent.id, pin)
        argv = build_up_argv(
            intent, compose_files=compose_files, profile=profile
        )
        result = runner(argv, override or None)
        if result.returncode == 0:
            return "applied", (result.stdout or "compose up succeeded").strip()
        return "failed", (
            result.stderr or f"compose up exited {result.returncode}"
        ).strip()

    if intent.desired == "absent":
        argv = build_down_argv(
            intent, compose_files=compose_files, profile=profile
        )
        # No image override on teardown — ``rm`` doesn't resolve the image.
        result = runner(argv, None)
        if result.returncode == 0:
            return "applied", (result.stdout or "compose down succeeded").strip()
        return "failed", (
            result.stderr or f"compose down exited {result.returncode}"
        ).strip()

    return "failed", f"unknown desired state {intent.desired!r}"


def reconcile_once(
    store: IntentStore,
    runner: Runner,
    *,
    compose_files: tuple[str, ...] = DEFAULT_COMPOSE_FILES,
    profile: str = DEFAULT_PROFILE,
) -> list[tuple[str, str, str]]:
    """One reconcile sweep over every pending intent.

    Skips rows already in their terminal ``applied`` state for the
    current desired value (the server resets ``status`` to ``pending`` on
    every new request, so a re-request is always re-applied). Returns a
    list of ``(id, status, message)`` for logging/observability.
    """
    outcomes: list[tuple[str, str, str]] = []
    for intent in store.list_intents():
        if intent.status == "applied":
            continue  # nothing to do until the server flips it to pending
        status_, message = reconcile_intent(
            intent, runner, compose_files=compose_files, profile=profile
        )
        store.set_status(intent.id, status_, message)
        outcomes.append((intent.id, status_, message))
    return outcomes

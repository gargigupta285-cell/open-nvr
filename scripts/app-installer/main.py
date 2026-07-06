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

"""Entrypoint for the OpenNVR app installer — poll + reconcile loop.

This is the single privileged, NON-network-facing component. It:

  1. reads the desired-state table (``app_install_intents``) via the
     SQLAlchemy store, and
  2. reconciles each pending intent by shelling out to ``docker
     compose`` through the real ``docker_runner``.

Both the store (DB) and the runner (Docker) are injected into the pure
``reconcile_once`` core, which is what the unit tests exercise with
fakes — this module is the thin production wiring only.

Config (env):

  DATABASE_URL          — required; points at the app_install_intents DB
                          (ideally a least-privilege SELECT/UPDATE role).
  INSTALLER_POLL_SECONDS — poll interval, default 10.
  INSTALLER_COMPOSE_FILES — comma-separated compose files, default
                          "docker-compose.yml,docker-compose.apps.yml".
  INSTALLER_PROFILE     — compose profile, default "apps".
"""

from __future__ import annotations

import logging
import os
import sys
import time

from reconciler import (
    DEFAULT_COMPOSE_FILES,
    DEFAULT_PROFILE,
    docker_runner,
    reconcile_once,
)
from store import SqlIntentStore

logging.basicConfig(
    level=os.environ.get("INSTALLER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("opennvr.app-installer")


def _compose_files() -> tuple[str, ...]:
    raw = os.environ.get("INSTALLER_COMPOSE_FILES")
    if not raw:
        return DEFAULT_COMPOSE_FILES
    return tuple(f.strip() for f in raw.split(",") if f.strip())


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL is required")
        return 2

    poll_seconds = float(os.environ.get("INSTALLER_POLL_SECONDS", "10"))
    compose_files = _compose_files()
    profile = os.environ.get("INSTALLER_PROFILE", DEFAULT_PROFILE)

    store = SqlIntentStore(database_url)
    logger.info(
        "app-installer starting: poll=%ss compose_files=%s profile=%s",
        poll_seconds,
        list(compose_files),
        profile,
    )

    while True:
        try:
            outcomes = reconcile_once(
                store,
                docker_runner,
                compose_files=compose_files,
                profile=profile,
            )
            for app_id, status_, message in outcomes:
                logger.info(
                    "reconciled %s -> %s (%s)", app_id, status_, message
                )
        except Exception:  # keep the loop alive; log and retry next tick
            logger.exception("reconcile sweep failed; retrying next poll")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    sys.exit(main())

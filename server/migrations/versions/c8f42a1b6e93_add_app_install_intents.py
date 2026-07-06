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

"""add app_install_intents table

Desired-state backing table for the opt-in one-click app installer.

The web app never runs Docker. The ``POST /apps/index/{id}/install`` and
``/uninstall`` endpoints only upsert a row here (validate id → write
desired state → audit); a separate, minimally-privileged reconciler
(``scripts/app-installer``) is the sole component that holds the docker
socket and applies each intent with ``docker compose`` up/down, writing
back ``status``/``message``.

The primary key is the curated app id (must exist in apps_index.yml), so
re-requesting install/uninstall is an upsert — mirroring installed_apps.

Revision ID: c8f42a1b6e93
Revises: a7c31e9f4d28
Create Date: 2026-07-06 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f42a1b6e93"
down_revision: str | None = "a7c31e9f4d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_install_intents",
        sa.Column(
            "id",
            sa.String(length=100),
            primary_key=True,
            comment='The curated app id (must exist in apps_index.yml).',
        ),
        sa.Column(
            "image",
            sa.String(length=500),
            nullable=False,
            comment="Canonical image ref, copied from the index entry.",
        ),
        sa.Column(
            "image_digest",
            sa.String(length=100),
            nullable=True,
            comment="sha256:... the reconciler pins to, or NULL (unpinned).",
        ),
        sa.Column(
            "desired",
            sa.String(length=20),
            nullable=False,
            server_default="installed",
            comment="installed | absent",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
            comment="pending | applied | failed",
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=True,
            comment="Last-reconcile note (compose stderr on failure, etc).",
        ),
        sa.Column("requested_by", sa.String(length=100), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_app_install_intents_id", "app_install_intents", ["id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_app_install_intents_id", table_name="app_install_intents"
    )
    op.drop_table("app_install_intents")

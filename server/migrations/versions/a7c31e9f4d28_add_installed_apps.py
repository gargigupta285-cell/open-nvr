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

"""add installed_apps table

Backing table for the app registry (App SDK spec §05). Apps built on
the OpenNVR App SDK self-register on boot via
``POST /api/v1/apps/register`` — the same shape adapters already use
against KAI-C — and this table stores the manifest snapshot, the
validated operator config, and the health bookkeeping the catalog UI
reads (``enabled`` / ``status`` / ``last_seen``).

The primary key is the manifest id (e.g. ``loitering-detection``), so
re-registration on app restart is an upsert that preserves the
operator's ``enabled`` flag and ``config_json``.

Revision ID: a7c31e9f4d28
Revises: b4e2a9c7f1d0
Create Date: 2026-07-03 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31e9f4d28"
down_revision: str | None = "b4e2a9c7f1d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installed_apps",
        sa.Column(
            "id",
            sa.String(length=100),
            primary_key=True,
            comment='The manifest id, e.g. "loitering-detection".',
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column(
            "url",
            sa.String(length=500),
            nullable=False,
            comment="Base URL the app serves /health, /manifest, /state on.",
        ),
        sa.Column(
            "manifest_json",
            sa.JSON(),
            nullable=False,
            comment="AppManifest.to_dict() snapshot from the last register.",
        ),
        sa.Column(
            "config_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="Operator config, validated against manifest_json.params.",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="registered",
            comment="registered | ok | unreachable",
        ),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_installed_apps_id", "installed_apps", ["id"])


def downgrade() -> None:
    op.drop_index("ix_installed_apps_id", table_name="installed_apps")
    op.drop_table("installed_apps")

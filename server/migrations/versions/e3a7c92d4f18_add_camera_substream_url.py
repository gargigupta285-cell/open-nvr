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

"""add camera substream_url

Adds an optional per-camera ``substream_url`` (a low-res secondary RTSP
profile) to ``cameras``. When set and AGENT_LIVE_USE_SUBSTREAM is on, the
camera-agent's live view uses it instead of the vendor-derived default so
a WebRTC decode costs a fraction of the CPU — covering cameras whose
substream path isn't a known Hikvision/Dahua convention.

Nullable, no default: existing rows keep NULL and fall back to the derived
substream (or the main stream when nothing is derivable).

Revision ID: e3a7c92d4f18
Revises: c8f42a1b6e93
Create Date: 2026-07-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c92d4f18"
down_revision: str | None = "c8f42a1b6e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "substream_url",
            sa.String(length=500),
            nullable=True,
            comment=(
                "Optional low-res secondary RTSP profile used by the "
                "camera-agent live view (AGENT_LIVE_USE_SUBSTREAM)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "substream_url")

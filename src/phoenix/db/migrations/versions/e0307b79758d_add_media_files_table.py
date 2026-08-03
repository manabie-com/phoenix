"""add media files table

Revision ID: e0307b79758d
Revises: c9d0e1f2a3b4
Create Date: 2026-07-30 15:01:39.541521

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0307b79758d"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_files",
        # Content-addressed: the digest is the identity, so storing the same bytes
        # twice costs nothing and a prompt version can reference media by digest.
        sa.Column("sha256", sa.String, primary_key=True),
        # Determined from the bytes at upload time, never from what the client claimed.
        sa.Column("media_type", sa.String, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        # No column for the bytes. Media lives in object storage — see
        # `phoenix.server.api.helpers.media_storage`. It would be the only large binary
        # in the schema, and bytes in the database inflate every backup and snapshot
        # while never returning their space on delete.
        #
        # Nullable: a name is not always known — a URL import may not supply one —
        # and only the providers that require a name to carry a document need it.
        # Because rows are keyed by digest, identical bytes keep the first name given.
        sa.Column("file_name", sa.String, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # When the media was first used by a run, or NULL if it never has been.
        #
        # Stands in for scanning every span that records a media reference. A media
        # digest is persisted in four places — a prompt version's template, a span's
        # attributes, an experiment task's template, and a dataset example's input — and
        # `MediaSweeper` scans the first, third and fourth directly. It cannot afford an
        # hourly scan of `spans`, so a run stamps the media it resolves and the sweeper
        # reclaims only rows that were never stamped at all.
        sa.Column("referenced_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # The sweep filters on `created_at`.
    op.create_index("ix_media_files_created_at", "media_files", ["created_at"])


def downgrade() -> None:
    # Drops the index with the table.
    op.drop_table("media_files")

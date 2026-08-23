"""songs remember when they were last sung

Revision ID: e83b5f2a1d47
Revises: d4a91c07be15
Create Date: 2026-08-23

T-3.8 needs it and T-3.9 is built on it. D-30 asks an over-quota message to
offer "the least played" songs to remove, and chapter 9 deletes a song nobody
has played for six months - both of which are the same fact, and neither of
which the database knew.

Nullable, and null means "never": a song that has been uploaded and not yet sung
is exactly the one those two rules are about, so the absence is the signal
rather than a gap to fill in.
"""

import sqlalchemy as sa
from alembic import op

revision = "e83b5f2a1d47"
down_revision = "d4a91c07be15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True))
    # The auto-deletion in T-3.9 scans "this user's songs, oldest play first",
    # which is this index; the account screen's candidate list is the same query
    # with a limit on it.
    op.create_index("ix_songs_user_played", "songs", ["user_id", "last_played_at"])


def downgrade() -> None:
    op.drop_index("ix_songs_user_played", table_name="songs")
    op.drop_column("songs", "last_played_at")

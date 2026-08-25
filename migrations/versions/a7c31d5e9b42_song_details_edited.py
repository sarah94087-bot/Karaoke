"""a song remembers that a person named it

Revision ID: a7c31d5e9b42
Revises: b58d0c9a3e77
Create Date: 2026-08-25

T-4.2 fills the title and the artist automatically, from three sources that
arrive at different times: the file's tags at ingestion, what an importer read
off a page, and - minutes later, once the job has run - the open lyrics
database, which is the only one that can tell that `ריטה - שביר.mp3` is `שביר`
by `ריטה`.

That last one is why this column exists. The automatic write happens *after* the
song is already on the screen, so a person can perfectly well have corrected the
name in between - and having their correction overwritten by a machine a minute
later is the kind of thing that makes somebody stop trusting the field. Null
means nobody has typed here yet; a timestamp means every automatic write is off
for this song, for good.

Nullable and with no default, so the upgrade rewrites nothing: every existing
song is one nobody has edited, which is true.
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c31d5e9b42"
down_revision = "b58d0c9a3e77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "songs", sa.Column("details_edited_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("songs", "details_edited_at")

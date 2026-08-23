"""songs belong to a user

Revision ID: d4a91c07be15
Revises: c6782423e7f2
Create Date: 2026-08-23

T-3.7. Until now a song had no owner: `jobs.user_id` recorded who started the
work, but the song itself belonged to everyone, and `GET /songs` returned the
lot. With one user that was the same thing (T-1.10 said so at the time); with
two it is a library that shows other people's music.

**`content_hash` stops being globally unique and becomes unique per user**, and
that is the decision worth explaining. Global dedup is cheaper - the same song
is separated once, ever - but it means the second person to upload a song is
handed the *first person's row*: their title, their artist, their stems, and the
knowledge that somebody else has that song. Chapter 9 counts quota per user and
D-31 keeps the door open to opening this up, so ownership wins over the saving.
At 30 songs a month against a 10GB bucket, the saving was never the constraint.

`user_id` has no foreign key, for the reason it never had one on `jobs`: users
live in a managed auth provider, not in this database.
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a91c07be15"
down_revision = "c6782423e7f2"
branch_labels = None
depends_on = None

# The local development user from `KARUKI_DEV_USER_ID`. Rows that predate
# ownership were all created by whoever was running it locally, and that is who
# this attributes them to; a NULL owner would be a song nobody can ever open.
DEV_USER = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column("songs", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.execute(sa.text(f"UPDATE songs SET user_id = '{DEV_USER}' WHERE user_id IS NULL"))
    op.alter_column("songs", "user_id", nullable=False)

    op.drop_constraint("uq_songs_content_hash", "songs", type_="unique")
    op.create_unique_constraint("uq_songs_owner_content", "songs", ["user_id", "content_hash"])
    # The library is "this user's songs, newest first", which is this index.
    op.create_index("ix_songs_user_created", "songs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_songs_user_created", table_name="songs")
    op.drop_constraint("uq_songs_owner_content", "songs", type_="unique")
    # Going back means one owner again, so duplicate hashes across users would
    # break the constraint being restored. Keeping the oldest row of each is the
    # only reading that does not lose somebody's song silently - and a downgrade
    # that cannot run is a downgrade nobody can use in an incident.
    op.execute(
        sa.text(
            "DELETE FROM songs a USING songs b "
            "WHERE a.content_hash IS NOT NULL "
            "AND a.content_hash = b.content_hash AND a.created_at > b.created_at"
        )
    )
    op.create_unique_constraint("uq_songs_content_hash", "songs", ["content_hash"])
    op.drop_column("songs", "user_id")

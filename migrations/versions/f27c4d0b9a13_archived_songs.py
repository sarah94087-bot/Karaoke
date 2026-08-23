"""songs can be archived

Revision ID: f27c4d0b9a13
Revises: e83b5f2a1d47
Create Date: 2026-08-23

T-3.9. Chapter 9 removes the audio of a song nobody has played for six months
and keeps everything else, so the song needs a state that says exactly that:
the row is real, the words and the settings are intact, and there is nothing to
play until it is uploaded again.

This is the migration T-1.4 predicted when it chose CHECK constraints over
native enum types - "adding a state later stays an ordinary migration with a
working downgrade". It is: drop the constraint, add the value, put it back.
"""

from alembic import op

revision = "f27c4d0b9a13"
down_revision = "e83b5f2a1d47"
branch_labels = None
depends_on = None

WITHOUT = "status IN ('pending', 'processing', 'ready', 'failed')"
WITH = "status IN ('pending', 'processing', 'ready', 'failed', 'archived')"


def upgrade() -> None:
    # `op.f` means "this name is already final": the naming convention in
    # packages/core/db.py would otherwise prefix it again and ask the database
    # to drop `ck_songs_ck_songs_status`, which is not a thing.
    op.drop_constraint(op.f("ck_songs_status"), "songs", type_="check")
    op.create_check_constraint("status", "songs", WITH)


def downgrade() -> None:
    # A row in the new state would violate the constraint being restored, and
    # its audio is already gone - `failed` is the honest reading of "there is
    # nothing to play here", and the row survives either way.
    op.execute("UPDATE songs SET status = 'failed' WHERE status = 'archived'")
    op.drop_constraint(op.f("ck_songs_status"), "songs", type_="check")
    op.create_check_constraint("status", "songs", WITHOUT)

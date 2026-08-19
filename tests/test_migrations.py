"""T-1.4's acceptance criterion: the migration runs up and down cleanly.

These need a real Postgres and are skipped without one, because a schema is one
of the few things you cannot check against a mock: CHECK constraints, ON DELETE
CASCADE and unique indexes are enforced by the database or not at all.

    docker compose -f infra/docker/compose.yaml up -d
    set DATABASE_URL=postgresql://karuki:karuki@localhost:5432/karuki
    pytest tests/test_migrations.py

They drop every table, so they refuse to run against anything but a local
database.
"""

from collections.abc import Callable

import psycopg
import pytest


def public_tables(connect: Callable[[], "object"]) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "select tablename from pg_tables where schemaname = 'public'"
        ).fetchall()
    # alembic_version is Alembic's own bookkeeping and outlives `downgrade base`
    # by design - it is how the next `upgrade` knows where it stands.
    return {name for (name,) in rows} - {"alembic_version"}


def test_migration_runs_up_and_down_cleanly(connect, alembic):
    """The acceptance criterion, end to end and in that order."""
    alembic("downgrade", "base")
    assert public_tables(connect) == set()

    alembic("upgrade", "head")
    assert public_tables(connect) == {"songs", "stems", "jobs"}

    alembic("downgrade", "base")
    assert public_tables(connect) == set(), "downgrade left tables behind"

    alembic("upgrade", "head")


def test_downgrade_leaves_no_indexes_or_sequences_behind(connect, alembic):
    """A table drop takes its indexes with it, but a hand-written migration can
    still orphan one. This is the check that catches that."""
    alembic("downgrade", "base")
    with connect() as conn:
        indexes = conn.execute(
            "select count(*) from pg_indexes "
            "where schemaname = 'public' and tablename <> 'alembic_version'"
        ).fetchone()[0]
        sequences = conn.execute(
            "select count(*) from information_schema.sequences where sequence_schema = 'public'"
        ).fetchone()[0]

    assert (indexes, sequences) == (0, 0)
    alembic("upgrade", "head")


def test_the_models_and_the_migration_agree(schema, alembic):
    """`alembic check` fails if the models have drifted from the migrations.

    Chapter 10 runs migrations as a separate deploy step ahead of the new code,
    so drift here is a production-only failure, discovered at the worst moment.
    """
    result = alembic("check")

    assert "No new upgrade operations detected" in result.stdout + result.stderr


# --- the constraints the database is there to enforce -----------------------


def insert_song(conn, **overrides) -> str:
    values = {
        "title": "song",
        "source_type": "file",
        "status": "pending",
        "is_playable": False,
        "lyrics_status": "pending",
        **overrides,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    return conn.execute(
        f"insert into songs ({columns}) values ({placeholders}) returning id",
        list(values.values()),
    ).fetchone()[0]


def test_a_song_row_round_trips_with_hebrew_text(connect, schema):
    """The whole product is Hebrew; a client_encoding surprise here would be
    found much later and much more expensively."""
    with connect() as conn:
        song_id = insert_song(conn, title="הכל דבש")
        (title,) = conn.execute("select title from songs where id = %s", [song_id]).fetchone()
        conn.execute("delete from songs where id = %s", [song_id])

    assert title == "הכל דבש"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "almost_ready"),
        ("lyrics_status", "perfect"),
        ("source_type", "telepathy"),
    ],
)
def test_songs_reject_a_value_outside_the_vocabulary(connect, schema, column, value):
    with connect() as conn, pytest.raises(psycopg.errors.CheckViolation):
        insert_song(conn, **{column: value})


def test_a_song_longer_than_the_cap_is_rejected(connect, schema):
    """Chapter 9 caps song length at 8 minutes."""
    with connect() as conn:
        song_id = insert_song(conn, duration_sec=480)
        conn.execute("delete from songs where id = %s", [song_id])

        with pytest.raises(psycopg.errors.CheckViolation):
            insert_song(conn, duration_sec=481)


def test_two_songs_cannot_share_a_content_hash(connect, schema):
    """Dedup depends on this: the cheapest song to process is one already
    processed."""
    with connect() as conn:
        song_id = insert_song(conn, content_hash="a" * 64)
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                insert_song(conn, content_hash="a" * 64)
        finally:
            conn.execute("delete from songs where id = %s", [song_id])


def test_a_song_cannot_have_two_stems_of_the_same_kind(connect, schema):
    with connect() as conn:
        song_id = insert_song(conn)
        try:
            for kind in ("vocals", "drums", "bass", "other"):
                conn.execute(
                    "insert into stems (id, song_id, kind, storage_key, format, bytes) "
                    "values (gen_random_uuid(), %s, %s, %s, 'opus', 1)",
                    [song_id, kind, f"{song_id}/{kind}.opus"],
                )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into stems (id, song_id, kind, storage_key, format, bytes) "
                    "values (gen_random_uuid(), %s, 'vocals', 'other.opus', 'opus', 1)",
                    [song_id],
                )
        finally:
            conn.execute("delete from songs where id = %s", [song_id])


def test_deleting_a_song_deletes_its_stems_and_jobs(connect, schema):
    with connect() as conn:
        song_id = insert_song(conn)
        conn.execute(
            "insert into stems (id, song_id, kind, storage_key, format, bytes) "
            "values (gen_random_uuid(), %s, 'vocals', 'k', 'opus', 1)",
            [song_id],
        )
        conn.execute(
            "insert into jobs (id, song_id, user_id, state, progress, attempts) "
            "values (gen_random_uuid(), %s, gen_random_uuid(), 'queued', 0, 0)",
            [song_id],
        )

        conn.execute("delete from songs where id = %s", [song_id])

        for table in ("stems", "jobs"):
            left = conn.execute(
                f"select count(*) from {table} where song_id = %s", [song_id]
            ).fetchone()[0]
            assert left == 0, f"{table} rows outlived their song"


def test_progress_stays_within_0_and_100(connect, schema):
    with connect() as conn:
        song_id = insert_song(conn)
        try:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into jobs (id, song_id, user_id, state, progress, attempts) "
                    "values (gen_random_uuid(), %s, gen_random_uuid(), 'running', 101, 0)",
                    [song_id],
                )
        finally:
            conn.execute("delete from songs where id = %s", [song_id])

"""T-1.4: the songs / stems / jobs models, checked without a database.

These assert the decisions chapter 5 is explicit about, so that a later
"tidy-up" has to argue with a failing test rather than a comment.
"""

from packages.core.db import NAMING_CONVENTION, Base
from packages.core.enums import JobState, JobStep, LyricsStatus, SongStatus, StemKind
from packages.core.models import Job, Song, Stem


def columns(model: type) -> set[str]:
    return {c.name for c in model.__table__.columns}


def test_only_the_tables_with_a_task_behind_them_exist():
    """Chapter 5 lists eight tables, and they arrive with the task that uses
    them: songs/stems/jobs in T-1.4, user_song_settings in T-1.16. Creating one
    early means migrating it twice."""
    assert set(Base.metadata.tables) == {"songs", "stems", "jobs", "user_song_settings"}


def test_songs_has_the_fields_chapter_5_lists():
    expected = {
        "id",
        "title",
        "artist",
        "duration_sec",
        "source_type",
        "source_ref",
        "content_hash",
        "original_key",
        "bpm",
        "status",
        "is_playable",
        "lyrics_status",
    }

    assert expected <= columns(Song)


def test_stems_has_the_fields_chapter_5_lists():
    assert {"id", "song_id", "kind", "storage_key", "format", "bytes"} <= columns(Stem)


def test_jobs_has_the_fields_chapter_5_lists():
    expected = {
        "id",
        "song_id",
        "user_id",
        "state",
        "current_step",
        "progress",
        "remote_call_id",
        "gpu_seconds",
        "error_code",
        "attempts",
        "started_at",
        "finished_at",
    }

    assert expected <= columns(Job)


def test_is_playable_is_its_own_column():
    """The heart of staged readiness (D-28): a song is playable well before it
    is READY. Deriving one from the other collapses the distinction."""
    assert "is_playable" in columns(Song)
    assert "status" in columns(Song)


def test_gpu_seconds_is_recorded_on_the_job():
    """Chapter 7: the only way to know how much free credit is left. Phase 0
    found the credit was $1 rather than the advertised $30."""
    assert Job.__table__.c.gpu_seconds is not None


def test_remote_call_id_is_nullable_but_present():
    """It only exists once the remote function has been invoked, but without it
    a running job cannot be followed at all."""
    assert Job.__table__.c.remote_call_id.nullable


def test_jobs_has_no_foreign_key_to_users():
    """Auth is a managed provider and D-16 is still open, so there is no users
    table here to point at."""
    referenced = {fk.column.table.name for fk in Job.__table__.foreign_keys}

    assert referenced == {"songs"}


def test_stems_are_one_of_each_kind_per_song():
    """A second `vocals` row means a re-run wrote alongside the old one."""
    uniques = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in Stem.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("kind", "song_id") in uniques


def test_deleting_a_song_takes_its_stems_and_jobs_with_it():
    """Chapter 9's deletion policy removes files and rows together."""
    for table in (Stem.__table__, Job.__table__):
        assert all(fk.ondelete == "CASCADE" for fk in table.foreign_keys)


def test_enum_vocabularies_match_the_spec():
    assert [str(k) for k in StemKind] == ["vocals", "drums", "bass", "other"]
    assert "missing" in list(LyricsStatus), "a failed transcription is not a failed job"
    assert "failed" in list(SongStatus)
    assert "failed" in list(JobState)


def test_transcription_steps_are_distinguishable():
    """D-29 runs transcription twice - on the mix and on the vocals - and picks
    the better one. A single `transcribing` step cannot say which is in flight."""
    assert JobStep.TRANSCRIBING_MIX != JobStep.TRANSCRIBING_VOCALS


def test_constraints_are_named():
    """Alembic's downgrade drops constraints by name. Without a naming
    convention Postgres picks the names and the downgrade is not portable."""
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}

    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name, f"unnamed constraint on {table.name}"
        for index in table.indexes:
            assert index.name, f"unnamed index on {table.name}"

"""T-1.7: the job state machine, and the state surviving a restart.

The restart is simulated the way it actually happens: the rows stay, the process
does not. Every test that says "after a restart" throws away its session and
engine and builds new ones, because a test that reuses the session would be
testing SQLAlchemy's identity map rather than Postgres.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core import jobs
from packages.core.db import create_engine, session_factory
from packages.core.enums import JobState, JobStep, LyricsStatus, SongStatus, SourceType
from packages.core.jobs import INTERRUPTED, STEP_PROGRESS, InvalidTransition
from packages.core.models import Job, Song

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def sessions(database_url: str, schema: None) -> AsyncIterator[async_sessionmaker]:
    engine = create_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Jobs cascade with their song."""


async def a_song_and_job(session: AsyncSession) -> tuple[Song, Job]:
    song = Song(
        id=uuid.uuid4(),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        title="שיר",
        source_type=SourceType.FILE,
        status=SongStatus.PENDING,
        duration_sec=100,
    )
    session.add(song)
    await session.flush()
    job = await jobs.create_job(session, song, USER)
    return song, job


# --- the state machine ------------------------------------------------------


def test_the_progress_map_covers_every_step_and_only_goes_forward():
    """A bar that goes backwards reads as a bug even when nothing is wrong."""
    assert set(STEP_PROGRESS) == set(JobStep)

    values = [STEP_PROGRESS[step] for step in JobStep]
    assert values == sorted(values)
    assert values[-1] == 100


def test_the_player_opens_before_the_job_is_done():
    """D-28 is the largest single win in perceived wait in the whole spec."""
    assert STEP_PROGRESS[jobs.PLAYABLE_AFTER] < 100


async def test_a_new_job_starts_queued_at_zero(sessions):
    async with sessions() as session:
        _, job = await a_song_and_job(session)

    assert job.state == JobState.QUEUED
    assert job.progress == 0
    assert job.attempts == 0
    assert job.current_step is None


async def test_starting_a_job_counts_an_attempt(sessions):
    async with sessions() as session:
        _, job = await a_song_and_job(session)

        await jobs.start(session, job)

    assert job.state == JobState.RUNNING
    assert job.attempts == 1
    assert job.started_at is not None


async def test_advancing_sets_the_progress_for_the_step(sessions):
    """Progress is derived from the step, never passed in: a caller free to
    choose its own number will eventually report 90% twice."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)

        await jobs.advance(session, job, JobStep.SEPARATING, song)

    assert job.current_step == JobStep.SEPARATING
    assert job.progress == STEP_PROGRESS[JobStep.SEPARATING]
    assert song.status == SongStatus.PROCESSING


async def test_finishing_completes_the_song(sessions):
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)

        await jobs.finish(session, job, song)

    assert (job.state, job.progress) == (JobState.READY, 100)
    assert job.current_step is None
    assert job.finished_at is not None
    assert song.status == SongStatus.READY


async def test_finishing_without_lyrics_stops_promising_them(sessions):
    """`pending` is what GET /songs/{id}/lyrics answers 202 on. A finished job
    that wrote no lyrics has to say `missing`, or the player waits for words
    that are never coming - which is the pipeline's state until T-2.3."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)

        await jobs.finish(session, job, song)

    assert song.lyrics_status == LyricsStatus.MISSING


async def test_a_failure_records_a_code(sessions):
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)

        await jobs.fail(session, job, "separation_failed", song)

    assert job.state == JobState.FAILED
    assert job.error_code == "separation_failed"
    assert song.status == SongStatus.FAILED


async def test_a_playable_song_is_not_marked_failed(sessions):
    """Chapter 7: a transcription or alignment failure is not a job failure, and
    a song you can already sing over is not a broken song."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.mark_playable(session, job, song)

        await jobs.fail(session, job, "alignment_failed", song)

    assert job.state == JobState.FAILED
    assert song.status != SongStatus.FAILED
    assert song.is_playable is True


@pytest.mark.parametrize(
    ("start_state", "target"),
    [
        (JobState.READY, JobState.RUNNING),
        (JobState.READY, JobState.FAILED),
        (JobState.QUEUED, JobState.READY),
        (JobState.FAILED, JobState.RUNNING),
    ],
)
def test_impossible_transitions_are_refused(start_state, target):
    assert not jobs.can_move(str(start_state), target)


async def test_a_finished_job_cannot_be_restarted(sessions):
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.finish(session, job, song)

        with pytest.raises(InvalidTransition):
            await jobs.start(session, job)


async def test_retrying_requeues_and_the_next_start_counts_again(sessions):
    """Chapter 7 forbids an automatic retry on a GPU step because it costs
    double credit; `attempts` counts the ones a user asked for."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.fail(session, job, "separation_failed", song)

        await jobs.retry(session, job)
        assert (job.state, job.progress, job.error_code) == (JobState.QUEUED, 0, None)

        await jobs.start(session, job)

    assert job.attempts == 2


async def test_gpu_seconds_accumulate_across_attempts(sessions):
    """Two attempts really did cost two runs of GPU time, and the credit is $1."""
    async with sessions() as session:
        _, job = await a_song_and_job(session)

        await jobs.record_gpu_seconds(session, job, 7.5)
        await jobs.record_gpu_seconds(session, job, 6.25)

    assert float(job.gpu_seconds) == 13.75


async def test_only_one_job_per_user_is_counted_as_active(sessions):
    """Chapter 9 allows one concurrent job per user."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await session.commit()

        assert await jobs.active_job_for_user(session, USER) is not None

        await jobs.start(session, job)
        await jobs.finish(session, job, song)
        await session.commit()

        assert await jobs.active_job_for_user(session, USER) is None


# --- surviving a restart ----------------------------------------------------


async def test_progress_is_durable_the_moment_it_is_shown(database_url, sessions):
    """The acceptance criterion: state is in the database, not in a process."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.advance(session, job, JobStep.SEPARATING, song)
        await session.commit()
        job_id = job.id

    # A new engine, as a restarted process would have.
    engine = create_engine(database_url)
    try:
        async with session_factory(engine)() as fresh:
            reloaded = await jobs.get_job(fresh, job_id)

            assert reloaded is not None
            assert reloaded.current_step == JobStep.SEPARATING
            assert reloaded.progress == STEP_PROGRESS[JobStep.SEPARATING]
    finally:
        await engine.dispose()


async def test_a_job_interrupted_by_a_restart_does_not_stay_running(database_url, sessions):
    """A bar that never moves again is the worst of the possible outcomes: the
    user cannot tell it from a slow song."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.advance(session, job, JobStep.SEPARATING, song)
        await session.commit()
        job_id = job.id

    engine = create_engine(database_url)
    try:
        async with session_factory(engine)() as fresh:
            recovered = await jobs.recover_interrupted(fresh)

            assert job_id in recovered

        async with session_factory(engine)() as fresh:
            reloaded = await jobs.get_job(fresh, job_id)
            assert reloaded.state == JobState.FAILED
            assert reloaded.error_code == INTERRUPTED
    finally:
        await engine.dispose()


async def test_recovery_does_not_touch_finished_jobs(database_url, sessions):
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await jobs.finish(session, job, song)
        await session.commit()
        job_id = job.id

    engine = create_engine(database_url)
    try:
        async with session_factory(engine)() as fresh:
            assert await jobs.recover_interrupted(fresh) == []

        async with session_factory(engine)() as fresh:
            assert (await jobs.get_job(fresh, job_id)).state == JobState.READY
    finally:
        await engine.dispose()


async def test_an_interrupted_job_is_not_requeued_automatically(database_url, sessions):
    """Re-queueing would be an automatic retry of a step that may have cost GPU
    credit. Chapter 7 says the user decides."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await session.commit()
        job_id = job.id

    engine = create_engine(database_url)
    try:
        async with session_factory(engine)() as fresh:
            await jobs.recover_interrupted(fresh)

        async with session_factory(engine)() as fresh:
            reloaded = await jobs.get_job(fresh, job_id)
            assert reloaded.state != JobState.QUEUED
            assert reloaded.attempts == 1, "recovery must not consume an attempt"
    finally:
        await engine.dispose()


async def test_a_recovered_job_can_be_retried_by_the_user(database_url, sessions):
    """Interrupted is a dead end only until somebody asks for another go."""
    async with sessions() as session:
        song, job = await a_song_and_job(session)
        await jobs.start(session, job)
        await session.commit()
        job_id = job.id

    engine = create_engine(database_url)
    try:
        async with session_factory(engine)() as fresh:
            await jobs.recover_interrupted(fresh)

        async with session_factory(engine)() as fresh:
            reloaded = await jobs.get_job(fresh, job_id)
            await jobs.retry(fresh, reloaded)
            await fresh.commit()

            assert reloaded.state == JobState.QUEUED
    finally:
        await engine.dispose()

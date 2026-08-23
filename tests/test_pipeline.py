"""The job runner: a queued job becomes four stems and a ready song, or a
failure with a code somebody can act on.

The separator is a stub. Whether Demucs works is settled elsewhere; what is
under test here is that the steps are reported in the order chapter 7 gives, that
`is_playable` lights up before the job finishes, and that a failure lands on the
row rather than escaping as a traceback.
"""

import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core import jobs
from packages.core.db import create_engine, session_factory
from packages.core.enums import JobState, JobStep, LyricsStatus, SongStatus, SourceType
from packages.core.lyrics import get_lyrics
from packages.core.models import Job, Song
from packages.core.pipeline import run_job
from packages.core.stems import normalised_key, stems_for
from packages.providers.lyrics_catalogue import Candidate, CatalogueError
from packages.providers.separation import (
    STEM_NAMES,
    Separated,
    SeparationError,
    SeparationUnavailable,
)
from packages.providers.storage import LocalStorage

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")


class StubSeparator:
    name = "stub"

    def __init__(self, gpu_seconds: float | None = None) -> None:
        self.gpu_seconds = gpu_seconds

    def separate(
        self, storage, source_key: str, targets: dict[str, str], on_started=None
    ) -> Separated:
        with tempfile.TemporaryDirectory(prefix="stub-stems-") as tmp:
            stems = {}
            for name in STEM_NAMES:
                path = Path(tmp) / f"{name}.mp3"
                path.write_bytes(f"{name}".encode())
                stems[name] = storage.put(targets[name], path)
        return Separated(stems=stems, backend=self.name, gpu_seconds=self.gpu_seconds)


class FailingSeparator:
    name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def separate(
        self, storage, source_key: str, targets: dict[str, str], on_started=None
    ) -> Separated:
        raise self.error


LRC = "[00:05.00]שורה ראשונה\n[00:09.00]שורה שנייה\n"


class StubCatalogue:
    """A lyrics database that always knows the song, or always fails."""

    name = "stub"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        if self.error is not None:
            raise self.error
        return [
            Candidate(
                title="שיר",
                artist=None,
                album=None,
                duration_sec=100.0,
                synced_lyrics=LRC,
                instrumental=False,
                remote_id="1",
                provider=self.name,
            )
        ]


@pytest.fixture
async def sessions(database_url: str, schema: None) -> AsyncIterator[async_sessionmaker]:
    engine = create_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Jobs and stems cascade with their song."""


async def ingested(
    session: AsyncSession, storage: LocalStorage, tmp_path: Path, *, with_audio: bool = True
) -> tuple[Song, Job]:
    song = Song(
        id=uuid.uuid4(),
        title="שיר",
        source_type=SourceType.FILE,
        status=SongStatus.PENDING,
        duration_sec=100,
    )
    session.add(song)
    await session.flush()
    if with_audio:
        source = tmp_path / "normalised.wav"
        source.write_bytes(b"pretend normalised audio")
        storage.put(normalised_key(song.id), source)
    job = await jobs.create_job(session, song, USER)
    await session.commit()
    return song, job


async def test_a_job_runs_through_to_ready(sessions, storage, tmp_path):
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, StubSeparator(), job, song)

    assert job.state == JobState.READY
    assert job.progress == 100
    assert song.status == SongStatus.READY


async def test_the_song_becomes_playable_before_the_job_finishes(sessions, storage, tmp_path):
    """D-28: the player opens on the stems alone, without waiting for lyrics."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, StubSeparator(), job, song)

    assert song.is_playable is True
    assert jobs.STEP_PROGRESS[jobs.PLAYABLE_AFTER] < 100, (
        "playable must be reachable before the bar is full, or D-28 buys nothing"
    )


async def test_the_four_stems_are_recorded(sessions, storage, tmp_path):
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, StubSeparator(), job, song)

    async with sessions() as session:
        found = await stems_for(session, song.id)

    assert {stem.kind for stem in found} == set(STEM_NAMES)


async def test_a_song_the_database_knows_arrives_already_timed(sessions, storage, tmp_path):
    """T-2.2 inside the pipeline: D-08 asks the open database before anything is
    transcribed, so a well-known song is ready without spending a transcription."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, StubSeparator(), job, song, catalogue=StubCatalogue())

        lyrics = await get_lyrics(session, song.id)

    assert lyrics is not None
    assert [line.text for line in lyrics.lines] == ["שורה ראשונה", "שורה שנייה"]
    assert lyrics.source == "db"
    assert song.lyrics_status == LyricsStatus.LINE
    assert job.state == JobState.READY


async def test_a_lyrics_database_that_fails_does_not_fail_the_job(sessions, storage, tmp_path):
    """Chapter 7 is explicit, and this is the case it was written for: the stems
    are done, the user can sing, and a stranger's service being down is not a
    reason to tell them their song failed."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(
            session,
            storage,
            StubSeparator(),
            job,
            song,
            catalogue=StubCatalogue(error=CatalogueError("down")),
        )

    assert job.state == JobState.READY
    assert song.lyrics_status == LyricsStatus.MISSING


async def test_gpu_seconds_land_on_the_job(sessions, storage, tmp_path):
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, StubSeparator(gpu_seconds=6.2), job, song)

    assert float(job.gpu_seconds) == 6.2


async def test_a_song_that_was_never_ingested_fails_early(sessions, storage, tmp_path):
    """Cheaply, at the ingest check, rather than three minutes into a separation."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path, with_audio=False)

        await run_job(session, storage, StubSeparator(), job, song)

    assert job.state == JobState.FAILED
    assert job.error_code == "not_ingested"
    assert job.current_step == JobStep.INGESTING


async def test_a_separation_failure_is_recorded_not_raised(sessions, storage, tmp_path):
    """The user gets a screen, not a 500."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        result = await run_job(
            session, storage, FailingSeparator(SeparationError("the GPU went away")), job, song
        )

    assert result.state == JobState.FAILED
    assert result.error_code == "separation_failed"
    assert song.status == SongStatus.FAILED


async def test_a_missing_backend_is_reported_as_unavailable_not_as_a_bad_song(
    sessions, storage, tmp_path
):
    """The API image has no torch on purpose. Telling the user their file could
    not be separated would be a lie about whose problem it is."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        result = await run_job(
            session,
            storage,
            FailingSeparator(SeparationUnavailable("no demucs here")),
            job,
            song,
        )

    assert result.error_code == "separation_unavailable"


async def test_a_failed_job_leaves_no_stems(sessions, storage, tmp_path):
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)
        await run_job(session, storage, FailingSeparator(SeparationError("nope")), job, song)

    async with sessions() as session:
        assert await stems_for(session, song.id) == []


async def test_a_retried_job_can_succeed(sessions, storage, tmp_path):
    """The point of not auto-retrying is that retrying still has to work."""
    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)
        await run_job(session, storage, FailingSeparator(SeparationError("nope")), job, song)

        await jobs.retry(session, job)
        await session.commit()

        await run_job(session, storage, StubSeparator(), job, song)

    assert job.state == JobState.READY
    assert job.attempts == 2


async def test_the_remote_call_id_is_durable_before_the_work_finishes(
    database_url, sessions, storage, tmp_path
):
    """T-3.4, and the reason the id is reported through a callback rather than
    in the result.

    A job whose process dies mid-call is exactly the one that needs the handle
    on the call still running out there. So the row is read over a separate
    connection from inside the separation, after the id has been announced and
    while the work is still notionally going - only committed data is visible,
    which is what a restarted process would see.
    """
    import psycopg

    observed: list[str | None] = []

    class Spawning(StubSeparator):
        def __init__(self, job_id: uuid.UUID) -> None:
            super().__init__()
            self.job_id = job_id

        def separate(self, storage, source_key: str, targets: dict[str, str], on_started=None):
            assert on_started is not None
            on_started("fc-abc123")
            with psycopg.connect(database_url, autocommit=True) as conn:
                observed.append(
                    conn.execute(
                        "select remote_call_id from jobs where id = %s", [str(self.job_id)]
                    ).fetchone()[0]
                )
            return super().separate(storage, source_key, targets)

    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, Spawning(job.id), job, song)

    assert observed == ["fc-abc123"], "the call id was not durable while the call was running"
    async with sessions() as session:
        assert (await session.get(Job, job.id)).remote_call_id == "fc-abc123"


async def test_a_failed_gpu_run_still_records_what_it_spent(sessions, storage, tmp_path):
    """The seconds came off the same $1 credit as a successful run. Counting
    only the successes is how a credit runs out without warning."""
    burned = SeparationError("the GPU gave up", gpu_seconds=16.0)

    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, FailingSeparator(burned), job, song)

    async with sessions() as session:
        failed = await session.get(Job, job.id)
        assert failed.state == JobState.FAILED
        assert float(failed.gpu_seconds) == 16.0


async def test_progress_is_committed_as_it_happens(database_url, sessions, storage, tmp_path):
    """ "Survives a restart" is only true if each step is durable when it is
    shown, not when the job ends.

    The row is read over a separate connection from inside the separation, so
    only committed data is visible - which is the same thing a restarted process
    would see.
    """
    import psycopg

    observed: list[tuple[str, int] | None] = []

    class Observing(StubSeparator):
        def __init__(self, job_id: uuid.UUID) -> None:
            super().__init__()
            self.job_id = job_id

        def separate(
            self, storage, source_key: str, targets: dict[str, str], on_started=None
        ) -> Separated:
            with psycopg.connect(database_url, autocommit=True) as conn:
                observed.append(
                    conn.execute(
                        "select current_step, progress from jobs where id = %s", [str(self.job_id)]
                    ).fetchone()
                )
            return super().separate(storage, source_key, targets)

    async with sessions() as session:
        song, job = await ingested(session, storage, tmp_path)

        await run_job(session, storage, Observing(job.id), job, song)

    assert observed == [("separating", jobs.STEP_PROGRESS[JobStep.SEPARATING])], (
        "the separating step was not durable before the work began"
    )

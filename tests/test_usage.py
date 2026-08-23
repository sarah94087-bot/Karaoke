"""What the GPU has cost this month (T-3.4).

Chapter 7 calls gpu_seconds the only way to know how much free credit is left,
and phase 0 found the credit was $1 rather than $30. T-3.3 then roughly doubled
the per-song cost by moving the transfers inside the billed window, so the sum
is a number that has to be readable without anyone remembering to look.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from packages.core.db import create_engine, session_factory
from packages.core.enums import JobState, SongStatus, SourceType
from packages.core.models import Job, Song
from packages.core.usage import gpu_seconds_this_month, start_of_month, usd

pytestmark = pytest.mark.anyio

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
async def session(database_url: str, schema: None, empty_songs: None) -> AsyncIterator:
    engine = create_engine(database_url)
    factory = session_factory(engine)
    async with factory() as opened:
        yield opened
    await engine.dispose()


async def a_job(
    session, seconds: float | None, *, created: datetime | None = None, user: uuid.UUID = USER
) -> Job:
    song = Song(
        id=uuid.uuid4(),
        title="שיר",
        source_type=SourceType.FILE,
        content_hash=uuid.uuid4().hex,
        status=SongStatus.READY,
    )
    session.add(song)
    await session.flush()
    job = Job(
        id=uuid.uuid4(),
        song_id=song.id,
        user_id=user,
        state=JobState.READY,
        gpu_seconds=seconds,
    )
    if created is not None:
        job.created_at = created
    session.add(job)
    await session.flush()
    return job


async def test_the_month_adds_up(session):
    await a_job(session, 42.6)
    await a_job(session, 13.5)

    assert await gpu_seconds_this_month(session) == pytest.approx(56.1)


async def test_a_failed_run_counts_too(session):
    """It spent what it spent. A total that only adds up the successes is the
    one that runs out without warning."""
    failed = await a_job(session, 16.0)
    failed.state = str(JobState.FAILED)
    failed.error_code = "separation_failed"
    await session.flush()

    assert await gpu_seconds_this_month(session) == pytest.approx(16.0)


async def test_last_month_is_not_this_month(session):
    """The credit resets with the calendar month, so the sum has to."""
    await a_job(session, 100.0, created=start_of_month() - timedelta(days=1))
    await a_job(session, 5.0)

    assert await gpu_seconds_this_month(session) == pytest.approx(5.0)


async def test_a_job_with_no_gpu_time_is_not_a_hole(session):
    """The local backend records nothing, and nothing is zero, not an error."""
    await a_job(session, None)

    assert await gpu_seconds_this_month(session) == 0.0


async def test_it_can_be_asked_per_user(session):
    """Chapter 9 budgets per user, and D-16 is coming."""
    await a_job(session, 30.0)
    await a_job(session, 70.0, user=OTHER)

    assert await gpu_seconds_this_month(session, USER) == pytest.approx(30.0)


def test_seconds_become_money_at_the_measured_rate():
    """The 4:30 song measured in T-3.3, priced at the workspace's own T4 rate."""
    assert usd(42.6) == pytest.approx(0.007, abs=0.0005)
    # 30 songs a month at that rate is a fifth of the $1 credit.
    assert usd(42.6 * 30) == pytest.approx(0.21, abs=0.01)


def test_the_month_starts_at_midnight_on_the_first():
    start = start_of_month(datetime(2026, 8, 23, 14, 30, tzinfo=UTC))

    assert start == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)

"""Chapter 9's limits, and where a user stands against them (T-3.8).

The numbers are a safety net, not a daily constraint - phase 0 measured the real
volume at about a tenth of them. They exist so that a mistake or an experiment
cannot burn a month of free credit in an afternoon, which on a tier with no
payment method means the service simply stops.

**D-30 is the rule that shapes this file.** Going over is never a silent stop
that looks like a fault: what is returned says which limit it was, how much of
it is used, and - because "you are out of space" with no way forward is not an
answer - which songs are the ones to remove. That is why `crowding_out` exists
here rather than in a screen: the offer is part of the refusal.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.enums import JobState
from packages.core.models import Job, Song, Stem

# Chapter 9's table, in one place.
SONGS_PER_MONTH = 10
MAX_SONG_SECONDS = 8 * 60
CONCURRENT_JOBS = 1
STORAGE_BYTES = 300 * 1024 * 1024

# How many songs to offer as candidates when the storage is full. Enough to
# choose from, few enough to read.
CANDIDATES = 5


class QuotaExceeded(RuntimeError):
    """Over one of chapter 9's limits.

    Carries the code the web app turns into Hebrew, plus the numbers, because
    D-30 asks for "what ran out, and how much is left" rather than "no".
    """

    def __init__(self, code: str, message: str, used: int, limit: int) -> None:
        super().__init__(message)
        self.code = code
        self.used = used
        self.limit = limit


@dataclass(frozen=True)
class Candidate:
    """A song worth removing, and why it is the one being suggested."""

    song_id: uuid.UUID
    title: str
    bytes: int
    last_played_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Usage:
    """Everything the account screen shows, in one query set."""

    songs_this_month: int
    songs_per_month: int
    storage_bytes: int
    storage_limit_bytes: int
    running_jobs: int
    concurrent_jobs: int

    @property
    def songs_left(self) -> int:
        return max(0, self.songs_per_month - self.songs_this_month)

    @property
    def storage_left_bytes(self) -> int:
        return max(0, self.storage_limit_bytes - self.storage_bytes)


def start_of_month(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def storage_used(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Bytes this user's songs occupy, counted from the stem rows.

    The stems are the whole of it in practice: the normalised wav is deleted
    once the stems exist, and the original is a few megabytes against their
    fifteen. Counted from rows rather than by asking the bucket, because a
    listing per screen load is a round trip to Amsterdam for a number that is
    already in the database.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Stem.bytes), 0))
        .select_from(Stem)
        .join(Song, Song.id == Stem.song_id)
        .where(Song.user_id == user_id)
    )
    return int(total or 0)


async def usage(session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None) -> Usage:
    songs = await session.scalar(
        select(func.count())
        .select_from(Song)
        .where(Song.user_id == user_id, Song.created_at >= start_of_month(now))
    )
    running = await session.scalar(
        select(func.count())
        .select_from(Job)
        .join(Song, Song.id == Job.song_id)
        .where(
            Song.user_id == user_id,
            Job.state.in_([str(JobState.QUEUED), str(JobState.RUNNING)]),
        )
    )
    return Usage(
        songs_this_month=int(songs or 0),
        songs_per_month=SONGS_PER_MONTH,
        storage_bytes=await storage_used(session, user_id),
        storage_limit_bytes=STORAGE_BYTES,
        running_jobs=int(running or 0),
        concurrent_jobs=CONCURRENT_JOBS,
    )


async def check_can_add(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
    concurrency: bool = True,
) -> Usage:
    """Raise if this user cannot start another song. Returns where they stand.

    Checked before the work rather than after: the point of a quota on a free
    tier is to not spend the credit, and a song refused after its separation has
    already run has cost exactly what the limit exists to protect.

    `concurrency=False` is for the upload *ticket*, which is issued a minute
    before the job it leads to and is not itself a job. A song still separating
    when the ticket is asked for will often be finished by the time the bytes
    have arrived, and refusing the upload for it would be refusing on a fact
    that has already changed. The monthly count and the storage do not move like
    that, so those are checked at both ends.
    """
    standing = await usage(session, user_id, now=now)

    if concurrency and standing.running_jobs >= standing.concurrent_jobs:
        # Chapter 9 allows one at a time per user. Two songs separating at once
        # is two GPU calls, and on one API instance it is also two Demucs runs
        # competing for the same CPU locally.
        raise QuotaExceeded(
            "job_already_running",
            "a song is already being processed",
            standing.running_jobs,
            standing.concurrent_jobs,
        )
    if standing.songs_this_month >= standing.songs_per_month:
        raise QuotaExceeded(
            "monthly_songs_exhausted",
            f"{standing.songs_per_month} new songs a month is the limit",
            standing.songs_this_month,
            standing.songs_per_month,
        )
    if standing.storage_bytes >= standing.storage_limit_bytes:
        raise QuotaExceeded(
            "storage_full",
            "there is no room for another song",
            standing.storage_bytes,
            standing.storage_limit_bytes,
        )
    return standing


async def crowding_out(
    session: AsyncSession, user_id: uuid.UUID, limit: int = CANDIDATES
) -> list[Candidate]:
    """The songs to suggest removing: least played first, oldest to break ties.

    D-30 asks for "the least played candidates" by name. A song never played
    sorts first - it is the one whose deletion costs its owner the least - and
    among those the oldest goes first, because a song uploaded this morning and
    not yet sung is not a candidate for anything.
    """
    rows = await session.execute(
        select(
            Song.id,
            Song.title,
            Song.last_played_at,
            Song.created_at,
            func.coalesce(func.sum(Stem.bytes), 0).label("bytes"),
        )
        .outerjoin(Stem, Stem.song_id == Song.id)
        .where(Song.user_id == user_id)
        .group_by(Song.id)
        .order_by(Song.last_played_at.asc().nulls_first(), Song.created_at.asc())
        .limit(limit)
    )
    return [
        Candidate(
            song_id=row.id,
            title=row.title,
            bytes=int(row.bytes or 0),
            last_played_at=row.last_played_at,
            created_at=row.created_at,
        )
        for row in rows
    ]

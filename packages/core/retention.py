"""Removing the audio of songs nobody sings any more (T-3.9).

Chapter 9: *"a song not played for 6 months - the metadata stays, the files are
deleted"*. Both halves of that sentence are the design.

**The files go.** They are the whole cost: fifteen megabytes a song against a
10GB bucket, and phase 0 measured that filling up in under two years of ordinary
use. A song nobody has opened since the spring is the cheapest thing to let go.

**The row stays.** Deleting it would delete the title somebody typed, the lyrics
they corrected line by line in T-2.9, the key and tempo measured for them, and
the settings they left the song in - none of which cost anything to keep and all
of which would have to be done again by hand. So the song stays in the library,
marked `archived`, and re-uploading the same audio restores it rather than
starting from nothing.

Never played counts from when it was added: a song uploaded eight months ago and
never opened is exactly what this is for, and treating a null as "recently
played" would exempt the clearest case.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.enums import SongStatus
from packages.core.models import Song, Stem
from packages.providers.storage import Storage

log = logging.getLogger("karuki.retention")

# Chapter 9's number.
UNPLAYED_MONTHS = 6
UNPLAYED_DAYS = UNPLAYED_MONTHS * 30


@dataclass(frozen=True)
class Reapable:
    """A song whose audio has outstayed its welcome, and what it holds."""

    song_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    bytes: int
    last_played_at: datetime | None
    created_at: datetime

    @property
    def idle_since(self) -> datetime:
        """The date the six months are counted from - played, or added."""
        return self.last_played_at or self.created_at


def cutoff(now: datetime | None = None, days: int = UNPLAYED_DAYS) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=days)


async def reapable(
    session: AsyncSession, *, now: datetime | None = None, days: int = UNPLAYED_DAYS
) -> list[Reapable]:
    """Songs whose audio can go, oldest idle first.

    Only songs that still *have* audio: a song archived last month has nothing
    left to remove, and a job that is still running is not idle at all - it is
    being processed right now, and its stems are about to appear.
    """
    line = cutoff(now, days)
    idle = func.coalesce(Song.last_played_at, Song.created_at)
    rows = await session.execute(
        select(
            Song.id,
            Song.user_id,
            Song.title,
            Song.last_played_at,
            Song.created_at,
            func.coalesce(func.sum(Stem.bytes), 0).label("bytes"),
        )
        .join(Stem, Stem.song_id == Song.id)
        .where(idle < line, Song.status != str(SongStatus.ARCHIVED))
        .group_by(Song.id)
        .order_by(idle.asc())
    )
    return [
        Reapable(
            song_id=row.id,
            user_id=row.user_id,
            title=row.title,
            bytes=int(row.bytes or 0),
            last_played_at=row.last_played_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def archive(session: AsyncSession, storage: Storage, song_id: uuid.UUID) -> int:
    """Remove one song's audio, keep everything else. Returns bytes freed.

    Storage first, then the rows, for the reason `DELETE /songs/{id}` has: an
    object with no row pointing at it is invisible to every screen and every
    user, and the only way to find it again is to go looking in the bucket by
    hand.
    """
    song = await session.get(Song, song_id)
    if song is None:  # pragma: no cover - it was listed a moment ago
        return 0
    title = song.title

    freed = int(
        await session.scalar(
            select(func.coalesce(func.sum(Stem.bytes), 0)).where(Stem.song_id == song_id)
        )
        or 0
    )
    storage.delete_prefix(f"songs/{song_id}")
    await session.execute(delete(Stem).where(Stem.song_id == song_id))

    song.status = str(SongStatus.ARCHIVED)
    # It cannot be opened in the player any more, and `is_playable` is the field
    # every screen already reads to decide that (D-28).
    song.is_playable = False
    await session.flush()
    log.info("archived %s (%s): freed %d bytes", song_id, title, freed)
    return freed

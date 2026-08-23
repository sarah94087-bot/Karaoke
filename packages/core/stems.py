"""The internal service T-1.6 asks for: one call, a song in, four stems out.

Everything above this - the API in T-1.7, the job state machine, eventually the
web app - calls `separate_song` and does not know whether the work happened on
this machine's CPU or on a rented GPU.

Re-running is safe on purpose. Chapter 7 requires every stage to be repeatable
on its own from saved intermediates, so calling this twice for the same song
replaces its stems rather than adding a second set alongside them - which the
unique constraint on (song_id, kind) would refuse anyway.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.audio.encode import STEM_FORMAT
from packages.core.enums import StemKind
from packages.core.models import Song, Stem
from packages.providers.separation import Separated, SeparationError, Separator
from packages.providers.storage import Storage

NORMALISED_KEY = "songs/{song_id}/normalised.wav"
STEM_KEY = "songs/{song_id}/stems/{kind}.{format}"


@dataclass(frozen=True)
class SeparationOutcome:
    """What the caller needs to record on the job and show to the user."""

    song_id: uuid.UUID
    stems: list[Stem]
    backend: str
    gpu_seconds: float | None
    timings: dict[str, float]


def stem_key(song_id: uuid.UUID, kind: str, fmt: str) -> str:
    return STEM_KEY.format(song_id=song_id, kind=kind, format=fmt)


def normalised_key(song_id: uuid.UUID) -> str:
    return NORMALISED_KEY.format(song_id=song_id)


def source_for(storage: Storage, song: Song) -> str:
    """The key of the normalised audio, or a clear failure if ingestion has not run.

    A key rather than a path since T-3.3: on the remote backend nothing on this
    machine ever opens it, and asking for a local path would download 40MB to
    prove it exists.
    """
    key = normalised_key(song.id)
    if not storage.exists(key):
        raise SeparationError(f"song {song.id} has no normalised audio; it has not been ingested")
    return key


def targets_for(song: Song) -> dict[str, str]:
    """Where the four stems belong. Decided here, never by the separator."""
    return {str(kind): stem_key(song.id, str(kind), STEM_FORMAT) for kind in StemKind}


def separate(
    storage: Storage,
    separator: Separator,
    song: Song,
    on_started: Callable[[str], None] | None = None,
) -> Separated:
    """Just the separation. Writes the stems, writes no rows.

    Split from `record_stems` so that the job runner can report `separating` and
    `encoding` as the distinct steps chapter 7 lists, instead of showing one long
    stall and inventing a step boundary that does not exist.
    """
    return separator.separate(storage, source_for(storage, song), targets_for(song), on_started)


async def record_stems(session: AsyncSession, song: Song, result: Separated) -> list[Stem]:
    """Write the rows for stems that are already in storage. Does not commit."""
    return await _record(session, song, result)


async def separate_song(
    session: AsyncSession,
    storage: Storage,
    separator: Separator,
    song: Song,
) -> SeparationOutcome:
    """Separate, store and record in one call - T-1.6's internal service.

    The order matters: separate (which stores), then write rows, and commit only
    at the end. A stem row that points at an object which is not there would
    make the player fail on a song the library says is ready.
    """
    result = separate(storage, separator, song)
    stems = await record_stems(session, song, result)

    await session.commit()
    return SeparationOutcome(
        song_id=song.id,
        stems=stems,
        backend=result.backend,
        gpu_seconds=result.gpu_seconds,
        timings=result.timings,
    )


async def _record(session: AsyncSession, song: Song, result: Separated) -> list[Stem]:
    # Drop any previous set first. Chapter 7 makes every stage re-runnable, and
    # (song_id, kind) is unique, so a re-run has to replace rather than add.
    await session.execute(delete(Stem).where(Stem.song_id == song.id))

    stems: list[Stem] = []
    for kind in StemKind:
        stored = result.stems[str(kind)]
        stems.append(
            Stem(
                song_id=song.id,
                kind=str(kind),
                storage_key=stored.key,
                format=result.format,
                bytes=stored.bytes,
            )
        )

    session.add_all(stems)
    await session.flush()
    return stems


async def stems_for(session: AsyncSession, song_id: uuid.UUID) -> list[Stem]:
    result = await session.scalars(select(Stem).where(Stem.song_id == song_id))
    return list(result)

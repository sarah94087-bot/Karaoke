"""The internal service: a song in, four stored and recorded stems out.

The separator is a stub here on purpose. Whether Demucs produces good stems is
settled in tests/test_separation.py and was measured in phase 0; what this file
is about is the wiring around it - that the stems reach storage, that the rows
match what is on disk, and that re-running replaces rather than duplicates.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.db import create_engine, session_factory
from packages.core.enums import SongStatus, SourceType
from packages.core.models import Song
from packages.core.stems import normalised_key, separate_song, stems_for
from packages.providers.separation import STEM_NAMES, Separated, SeparationError
from packages.providers.storage import LocalStorage


class StubSeparator:
    """Writes four small files, like the real thing but instantly."""

    name = "stub"

    def __init__(self, gpu_seconds: float | None = None) -> None:
        self.gpu_seconds = gpu_seconds
        self.calls: list[Path] = []

    def separate(self, source: Path, destination: Path) -> Separated:
        self.calls.append(source)
        destination.mkdir(parents=True, exist_ok=True)
        stems = {}
        for name in STEM_NAMES:
            path = destination / f"{name}.mp3"
            path.write_bytes(f"{name} for {source.name}".encode())
            stems[name] = path
        return Separated(
            stems=stems,
            backend=self.name,
            gpu_seconds=self.gpu_seconds,
            timings={"separation_s": 6.2},
        )


class ExplodingSeparator:
    name = "exploding"

    def separate(self, source: Path, destination: Path) -> Separated:
        raise SeparationError("the GPU went away")


@pytest.fixture
async def sessions(database_url: str, schema: None) -> Iterator[async_sessionmaker]:
    engine = create_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Leave the library as it was found; stems cascade with their song."""


def a_song() -> Song:
    return Song(
        id=uuid.uuid4(),
        title="שיר לדוגמה",
        source_type=SourceType.FILE,
        status=SongStatus.PROCESSING,
        duration_sec=120,
    )


async def given_an_ingested_song(
    session: AsyncSession, storage: LocalStorage, tmp_path: Path
) -> Song:
    song = a_song()
    session.add(song)
    await session.flush()

    source = tmp_path / "normalised.wav"
    source.write_bytes(b"pretend normalised audio")
    storage.put(normalised_key(song.id), source)
    return song


async def test_one_call_produces_four_stored_stems(sessions, storage, tmp_path):
    """T-1.6's acceptance criterion, through the service."""
    separator = StubSeparator()
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)

        outcome = await separate_song(session, storage, separator, song)

    assert {stem.kind for stem in outcome.stems} == set(STEM_NAMES)
    for stem in outcome.stems:
        assert storage.exists(stem.storage_key), f"{stem.kind} never reached storage"


async def test_the_rows_describe_what_is_actually_on_disk(sessions, storage, tmp_path):
    """A byte count that disagrees with the object is how a quota drifts."""
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)

        outcome = await separate_song(session, storage, StubSeparator(), song)

    for stem in outcome.stems:
        on_disk = storage.local_path(stem.storage_key)
        assert stem.bytes == on_disk.stat().st_size
        assert stem.format == "mp3"


async def test_the_stems_are_reachable_from_the_song(sessions, storage, tmp_path):
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)
        await separate_song(session, storage, StubSeparator(), song)

    async with sessions() as session:
        found = await stems_for(session, song.id)

    assert {stem.kind for stem in found} == set(STEM_NAMES)


async def test_running_it_twice_replaces_the_stems(sessions, storage, tmp_path):
    """Chapter 7 makes every stage re-runnable on its own, and (song_id, kind)
    is unique - so a second run has to replace, not add."""
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)
        await separate_song(session, storage, StubSeparator(), song)
        await separate_song(session, storage, StubSeparator(), song)

    async with sessions() as session:
        found = await stems_for(session, song.id)

    assert len(found) == 4, f"expected four stems, found {len(found)}"


async def test_gpu_seconds_are_carried_out_of_the_service(sessions, storage, tmp_path):
    """Chapter 7: the only way to know how much of the $1 credit is left."""
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)

        outcome = await separate_song(session, storage, StubSeparator(gpu_seconds=7.5), song)

    assert outcome.gpu_seconds == 7.5
    assert outcome.backend == "stub"


async def test_a_song_that_was_never_ingested_is_refused(sessions, storage):
    """No normalised audio means T-1.5 has not run; separating would be
    separating nothing."""
    async with sessions() as session:
        song = a_song()
        session.add(song)
        await session.flush()

        with pytest.raises(SeparationError):
            await separate_song(session, storage, StubSeparator(), song)


async def test_a_failed_separation_leaves_no_stem_rows(sessions, storage, tmp_path):
    """A row pointing at an object that is not there would make the player fail
    on a song the library calls ready."""
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)

        with pytest.raises(SeparationError):
            await separate_song(session, storage, ExplodingSeparator(), song)
        await session.rollback()

    async with sessions() as session:
        assert await stems_for(session, song.id) == []


async def test_the_separator_is_given_the_normalised_audio(sessions, storage, tmp_path):
    """Not the original upload: everything downstream is entitled to assume
    44.1kHz stereo, which is what T-1.5 guarantees."""
    separator = StubSeparator()
    async with sessions() as session:
        song = await given_an_ingested_song(session, storage, tmp_path)

        await separate_song(session, storage, separator, song)

    assert separator.calls[0].name == "normalised.wav"

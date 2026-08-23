"""T-2.4: both runs happen, and `source` says which one the words came from.

The spec's D-29 says "transcribe both and keep the better one". Phase 0 measured
that competition and found there is none - the vocals stem won 3 of 3 and the mix
returned 39% of the words - and it found the trap in measuring it: ranked by
average confidence the mix won 2 of 3, because high confidence over 17% of a song
is skipping, not quality. So what is under test here is not a scoring function.
It is that both runs happen, that the vocals run replaces the stand-in, and that
the row says which one is on the screen.

**No test spends a request**: the transcriber is a stub, the same way no test has
ever spent GPU credit.
"""

import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core import jobs
from packages.core.db import create_engine, session_factory
from packages.core.enums import JobState, LyricsStatus, SongStatus, SourceType
from packages.core.lyrics import get_lyrics, list_versions
from packages.core.models import Job, Song
from packages.core.pipeline import run_job
from packages.core.stems import normalised_key
from packages.providers.lyrics_catalogue import Candidate
from packages.providers.separation import STEM_NAMES, Separated
from packages.providers.storage import LocalStorage
from packages.providers.transcription import (
    Segment,
    Transcript,
    TranscriptionError,
    TranscriptionUnavailable,
)

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")


class StubSeparator:
    name = "stub"

    def separate(self, storage, source_key: str, targets: dict[str, str]) -> Separated:
        with tempfile.TemporaryDirectory(prefix="stub-stems-") as tmp:
            stems = {}
            for name in STEM_NAMES:
                path = Path(tmp) / f"{name}.mp3"
                path.write_bytes(name.encode())
                stems[name] = storage.put(targets[name], path)
        return Separated(stems=stems, backend=self.name)


def transcript(text: str, language: str = "hebrew") -> Transcript:
    return Transcript(
        segments=[Segment(text=text, start_ms=1_000, end_ms=4_000)],
        text=text,
        language=language,
        duration_sec=100.0,
        model="stub",
        backend="stub",
        elapsed_sec=1.0,
    )


class StubTranscriber:
    """Answers differently for the mix and for the vocals, and remembers what it
    was asked to transcribe - which run happened is half of what T-2.4 is."""

    name = "stub"

    def __init__(self, vocals: Transcript | Exception | None = None) -> None:
        self.vocals = vocals if vocals is not None else transcript("מהשירה")
        self.asked: list[str] = []

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
        self.asked.append(audio.name)
        if "vocals" in audio.name:
            if isinstance(self.vocals, Exception):
                raise self.vocals
            return self.vocals
        return transcript("מהתערובת")


class StubCatalogue:
    name = "stub"

    def __init__(self, lrc: str | None = None) -> None:
        self.lrc = lrc

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        if self.lrc is None:
            return []
        return [
            Candidate(
                title="שיר",
                artist=None,
                album=None,
                duration_sec=100.0,
                synced_lyrics=self.lrc,
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
    """Lyrics, stems and jobs all cascade with their song."""


async def ingested(
    session: AsyncSession, storage: LocalStorage, tmp_path: Path
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
    source = tmp_path / "normalised.wav"
    source.write_bytes(b"pretend normalised audio")
    storage.put(normalised_key(song.id), source)
    job = await jobs.create_job(session, song, USER)
    await session.commit()
    return song, job


async def run(session, storage, tmp_path, transcriber, catalogue=None):
    song, job = await ingested(session, storage, tmp_path)
    await run_job(
        session,
        storage,
        StubSeparator(),
        job,
        song,
        catalogue=catalogue,
        transcriber=transcriber,
    )
    return song, job


async def test_both_runs_happen(sessions, storage, tmp_path):
    """D-29's first half, and the acceptance criterion: the mix and the vocals
    are both transcribed."""
    transcriber = StubTranscriber()

    async with sessions() as session:
        await run(session, storage, tmp_path, transcriber)

    assert [name.split(".")[0] for name in transcriber.asked] == ["normalised", "vocals"]


async def test_the_vocals_run_is_what_ends_up_on_the_screen(sessions, storage, tmp_path):
    """T-0.4.2: the vocals stem won 3 of 3 and the mix returned 39% of the
    words. There is no competition to run, so the vocals always replace."""
    async with sessions() as session:
        song, _ = await run(session, storage, tmp_path, StubTranscriber())

        newest = await get_lyrics(session, song.id)

    assert newest is not None
    assert newest.source == "vocals_asr"
    assert [line.text for line in newest.lines] == ["מהשירה"]


async def test_the_stand_in_is_kept_as_the_version_before_it(sessions, storage, tmp_path):
    """The mix run is not thrown away: chapter 6 never overwrites, and a user
    whose vocals transcript is worse can go back to it."""
    async with sessions() as session:
        song, _ = await run(session, storage, tmp_path, StubTranscriber())

        versions = await list_versions(session, song.id)

    assert [(version.version, version.source) for version in versions] == [
        (2, "vocals_asr"),
        (1, "mix_asr"),
    ]


async def test_a_vocals_run_that_fails_leaves_the_stand_in_in_place(sessions, storage, tmp_path):
    """Deleting words we have for words we do not is not an improvement."""
    async with sessions() as session:
        song, job = await run(
            session, storage, tmp_path, StubTranscriber(vocals=TranscriptionError("boom"))
        )

        newest = await get_lyrics(session, song.id)

    assert newest is not None
    assert newest.source == "mix_asr"
    assert job.state == JobState.READY


async def test_a_vocals_run_that_says_nothing_leaves_the_stand_in_in_place(
    sessions, storage, tmp_path
):
    """A vocals stem that came out silent is the case this guards. It is a
    sanity check, not a quality comparison - the question is "did it produce
    anything at all", not "which is better"."""
    silent = Transcript(
        segments=[],
        text="",
        language="hebrew",
        duration_sec=100.0,
        model="stub",
        backend="stub",
        elapsed_sec=1.0,
    )

    async with sessions() as session:
        song, _ = await run(session, storage, tmp_path, StubTranscriber(vocals=silent))

        newest = await get_lyrics(session, song.id)

    assert newest is not None
    assert newest.source == "mix_asr"


async def test_no_transcription_service_is_not_a_failed_job(sessions, storage, tmp_path):
    """Chapter 7: a transcription failure is not a job failure. Nobody
    configuring a key is even less of one."""

    class Unavailable:
        name = "none"

        def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
            raise TranscriptionUnavailable("no key here")

    async with sessions() as session:
        song, job = await run(session, storage, tmp_path, Unavailable())

    assert job.state == JobState.READY
    assert song.is_playable is True
    assert song.lyrics_status == LyricsStatus.MISSING


async def test_a_song_the_open_database_already_knows_is_not_transcribed(
    sessions, storage, tmp_path
):
    """D-08's order, and a request out of the daily quota not spent on a song
    somebody has already timed by hand."""
    transcriber = StubTranscriber()
    catalogue = StubCatalogue("[00:05.00]מהמאגר\n")

    async with sessions() as session:
        song, _ = await run(session, storage, tmp_path, transcriber, catalogue)

        newest = await get_lyrics(session, song.id)

    assert transcriber.asked == [], "the database already had the words"
    assert newest is not None
    assert newest.source == "db"


async def test_the_language_comes_back_from_the_run(sessions, storage, tmp_path):
    """Whisper is not told what language to expect - a Hebrew speaker's library
    has English songs in it - so what it detects is what gets stored."""

    class English(StubTranscriber):
        def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
            super().transcribe(audio)
            return transcript("a line of english", language="english")

    async with sessions() as session:
        song, _ = await run(session, storage, tmp_path, English())

        newest = await get_lyrics(session, song.id)

    assert newest is not None
    assert newest.language == "en"


async def test_the_words_arrive_after_the_song_is_playable(sessions, storage, tmp_path):
    """D-28, and the reason the mix is transcribed at all: the player opens on
    the stems, and the words land while the user is already singing."""
    async with sessions() as session:
        song, job = await run(session, storage, tmp_path, StubTranscriber())

    assert song.is_playable is True
    assert job.state == JobState.READY
    assert song.lyrics_status == LyricsStatus.LINE

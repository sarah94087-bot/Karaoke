"""Transcribing a song, twice, and recording which run the words came from.

D-29 as the spec wrote it says: transcribe the mix and the vocals in parallel and
keep the better one. Phase 0 measured that competition and found there is none -
the vocals stem won 3 of 3, and the mix returned **39% of the words on average**
(`docs/phase0/phase0-findings.md`, T-0.4.2). It also found the trap in measuring
it: ranked by average confidence the mix won 2 of 3, because high confidence over
17% of a song is not quality, it is skipping. The finding restates D-29:

> The mix is a **temporary stand-in** that shows partial text early, not a
> candidate competing on quality. When the vocals stem is ready it replaces it,
> always, without comparison.

So this file does not score transcripts against each other. The one comparison
it makes is a sanity check with a different question behind it - did the vocals
run produce anything at all - because a vocals stem that came out silent should
not delete words we already have.

`source` is where the answer is recorded: `mix_asr` while the stand-in is what
the user sees, `vocals_asr` once the real one lands.
"""

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from packages.audio.decode import decode
from packages.audio.silence import Silence, silences
from packages.core.enums import LyricsSource, StemKind
from packages.core.lyrics import LyricsError, save_lyrics
from packages.core.models import Lyrics, Song
from packages.core.stems import normalised_key, stems_for
from packages.lyrics.transcript import lines_from
from packages.providers.storage import Storage, StorageError
from packages.providers.transcription import (
    Transcriber,
    Transcript,
    TranscriptionError,
)

log = logging.getLogger("karuki.transcribe")

# Whisper reports a language name rather than a code, and the words are stored
# with the two-letter one T-2.5's aligner will be given.
LANGUAGES = {"hebrew": "he", "he": "he", "english": "en", "en": "en"}


def language_code(reported: str | None) -> str:
    """`hebrew` -> `he`. Anything unrecognised is Hebrew, which is the project."""
    return LANGUAGES.get((reported or "").strip().casefold(), "he")


def mix_audio(storage: Storage, song: Song) -> Path | None:
    """The normalised mix, which exists from the moment ingestion finishes.

    That is the whole point of transcribing it: it is ready before the
    separation has started, so the run costs no wall-clock time at all.

    **Blocking, and on the object store expensively so** - `local_path` there is
    a download of the whole normalised wav, 47MB for a four-minute song. Call it
    from a worker thread, never from the event loop; T-3.5 measured 39.5 seconds
    of a frozen API when this ran inline, which is long enough for a keep-alive
    ping to time out and for the platform to call the service unhealthy while it
    is working perfectly.
    """
    key = normalised_key(song.id)
    if not storage.exists(key):
        return None
    return storage.local_path(key)


async def vocals_audio(session: AsyncSession, storage: Storage, song: Song) -> Path | None:
    """The separated vocals, once T-1.6 has written them.

    The database read is on the loop and the *fetch* is not: on the object store
    that fetch is a download, and a download on the event loop stops every other
    request in the process (T-3.5).
    """
    for stem in await stems_for(session, song.id):
        if stem.kind == StemKind.VOCALS:
            return await asyncio.to_thread(_fetch, storage, stem.storage_key)
    return None


def _fetch(storage: Storage, key: str) -> Path | None:
    try:
        return storage.local_path(key)
    except StorageError:
        return None


def transcribe(
    transcriber: Transcriber, audio: Path, language: str | None = None
) -> Transcript | None:
    """One run. Blocking, and never raises - see chapter 7.

    The language is a hint, and which run gets one is the point. Nothing is
    forced on the **mix**: a Hebrew speaker's library has English songs in it,
    and telling the model they are Hebrew produces Hebrew-shaped nonsense, so
    the mix is where the language is detected. The **vocals** run is then told
    what the mix found, because an isolated vocals stem is exactly where
    detection goes wrong - see `_transcribe` in packages/core/pipeline.py for
    the run that proved it.
    """
    try:
        return transcriber.transcribe(audio, language=language)
    except TranscriptionError as exc:
        # Including TranscriptionUnavailable: from here they are the same
        # outcome, which is "no words from this run".
        log.info("transcription of %s failed: %s", audio.name, exc)
        return None
    except Exception:  # noqa: BLE001 - a transcript must never fail a job
        log.exception("transcription of %s crashed", audio.name)
        return None


def silences_in(audio: Path) -> list[Silence]:
    """Where nobody is singing, for the aligner to break lines at. Never raises.

    Only ever asked about the **vocals** stem: silence in a mix means "no
    instruments either", which is a different question and a much rarer answer.
    """
    try:
        return silences(decode(audio))
    except Exception:  # noqa: BLE001 - splitting on length alone is a fine fallback
        log.exception("could not measure the silences in %s", audio.name)
        return []


async def save_transcript(
    session: AsyncSession,
    song_id: uuid.UUID,
    transcript: Transcript,
    source: LyricsSource,
    gaps: list[Silence] | None = None,
) -> Lyrics | None:
    """Store one run as a new version, or nothing if it said nothing.

    An empty transcript is not stored at all. A version with no lines would move
    the song from `pending` to `missing` and tell the user the words are not
    coming, which - with the second run still to happen - is not true yet.
    """
    lines = lines_from(transcript, gaps)
    if not lines:
        log.info("the %s run produced no usable lines for song %s", source, song_id)
        return None

    try:
        saved = await save_lyrics(
            session,
            song_id,
            lines=lines,
            source=source,
            language=language_code(transcript.language),
        )
    except LyricsError as exc:
        log.info("the %s transcript for song %s was rejected: %s", source, song_id, exc)
        return None

    log.info(
        "song %s: %s produced %d lines in %.1fs (%s)",
        song_id,
        source,
        len(lines),
        transcript.elapsed_sec,
        transcript.backend,
    )
    return saved

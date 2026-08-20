"""Running a job through the steps, and recording where it got to.

What exists today is the left-hand branch of chapter 7's diagram: ingest,
separate, encode, playable, ready. Transcription and alignment are the other
branch and arrive with their own tasks; when they do, `ready` moves to after
them and `is_playable` stays exactly where it is. That is the whole point of
D-28 having them as separate fields.

Separation is CPU-bound and blocking - Demucs is not going to await anything -
so it runs in a worker thread. Doing it inline would freeze the event loop of
the single API instance chapter 9 budgets for, which means the keep-alive ping
would time out and the platform would decide the service is unhealthy while it
is in fact working perfectly.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core import jobs
from packages.core.analysis import analyse_song
from packages.core.enums import JobStep, LyricsSource
from packages.core.events import EventBus, EventType, JobEvent
from packages.core.lyrics import get_lyrics
from packages.core.lyrics_lookup import lookup_lyrics
from packages.core.models import Job, Song
from packages.core.stems import record_stems, separate, source_for
from packages.core.transcribe import (
    language_code,
    mix_audio,
    save_transcript,
    transcribe,
    vocals_audio,
)
from packages.providers.lyrics_catalogue import LyricsCatalogue
from packages.providers.separation import (
    SeparationError,
    SeparationUnavailable,
    Separator,
)
from packages.providers.storage import Storage
from packages.providers.transcription import Transcriber, Transcript

log = logging.getLogger("karuki.pipeline")


class PipelineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def announce(bus: EventBus | None, job: Job, song: Song, kind: EventType) -> None:
    """Tell the watchers, after the change is committed and not before.

    Order matters: a client told about a step that is then rolled back has seen
    something that never happened, and it has no way to find that out.
    """
    if bus is None:
        return
    bus.publish(
        JobEvent(
            job_id=job.id,
            song_id=song.id,
            type=kind,
            state=job.state,
            progress=job.progress,
            is_playable=song.is_playable,
            current_step=job.current_step,
            error_code=job.error_code,
        )
    )


async def run_job(
    session: AsyncSession,
    storage: Storage,
    separator: Separator,
    job: Job,
    song: Song,
    bus: EventBus | None = None,
    catalogue: LyricsCatalogue | None = None,
    transcriber: Transcriber | None = None,
) -> Job:
    """Take a queued job to `ready`, or to `failed` with a code.

    Committing at every step is deliberate and is what "survives a restart"
    means in practice: the progress a user is watching has to be durable at the
    moment they see it, not at the end.
    """
    await jobs.start(session, job)
    await session.commit()
    announce(bus, job, song, "progress")

    mix_run: asyncio.Task[Transcript | None] | None = None
    try:
        await _ingest(session, storage, job, song, bus)

        # D-08's order: the open database first, because a song somebody has
        # already timed by hand beats anything a model will produce, and it
        # costs one HTTP call. Moved ahead of the separation in T-2.4 so that
        # its answer can decide whether to transcribe at all.
        if catalogue is not None:
            await lookup_lyrics(session, song, catalogue)
            await session.commit()

        # D-29, and the only reason the mix is transcribed at all: it starts now
        # and runs while Demucs works, so by the time the stems exist there are
        # already words. Started as a task rather than awaited - the separation
        # is the long pole and nothing should wait on this.
        if transcriber is not None and await _needs_words(session, song):
            mix_run = _start_mix_transcription(storage, song, transcriber)

        await _separate(session, storage, separator, job, song, bus)
    except PipelineError as exc:
        log.warning("job %s failed at %s: %s", job.id, job.current_step, exc)
        await jobs.fail(session, job, exc.code, song)
        await session.commit()
        announce(bus, job, song, "failed")
        return job
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is still a failed job
        log.exception("job %s crashed at %s", job.id, job.current_step)
        await jobs.fail(session, job, "internal_error", song)
        await session.commit()
        announce(bus, job, song, "failed")
        raise PipelineError("internal_error", str(exc)) from exc

    # Everything from here is lyrics, and chapter 7 is explicit that none of it
    # can fail the job: the stems are written, the song is playable, and the
    # worst outcome is words the user types themselves.
    if transcriber is not None:
        await _transcribe(session, storage, job, song, transcriber, mix_run, bus)

    await jobs.finish(session, job, song)
    await session.commit()
    announce(bus, job, song, "ready")
    return job


async def _needs_words(session: AsyncSession, song: Song) -> bool:
    """False when something already wrote lyrics for this song.

    Normally that is the open database (T-2.2). Transcribing anyway would spend
    a request out of the daily quota to produce something worse than what is
    already stored.
    """
    return await get_lyrics(session, song.id) is None


def _start_mix_transcription(
    storage: Storage, song: Song, transcriber: Transcriber
) -> asyncio.Task[Transcript | None] | None:
    audio = mix_audio(storage, song)
    if audio is None:  # pragma: no cover - _ingest has already checked this
        return None
    # The HTTP call blocks, so it goes to a thread; the task only ever returns a
    # value, and never touches the session - which is not safe to share.
    return asyncio.create_task(asyncio.to_thread(transcribe, transcriber, audio))


async def _transcribe(
    session: AsyncSession,
    storage: Storage,
    job: Job,
    song: Song,
    transcriber: Transcriber,
    mix_run: asyncio.Task[Transcript | None] | None,
    bus: EventBus | None = None,
) -> None:
    """The two runs of D-29, in the order the measurements settled.

    The mix transcript is a stand-in shown early, not a candidate: T-0.4.2 found
    it returns 39% of the words, and the vocals stem won every song. So the
    vocals run replaces it whenever it produces anything at all, and no scoring
    happens here. The one thing that would keep the stand-in is a vocals run
    that came back with nothing - deleting words we have for words we do not is
    not an improvement.
    """
    if not await _needs_words(session, song):
        if mix_run is not None:
            mix_run.cancel()
        return

    stand_in = None
    if mix_run is not None:
        if not mix_run.done():
            # Only now is this worth naming: the job really is waiting on it.
            await jobs.advance(session, job, JobStep.TRANSCRIBING_MIX, song)
            await session.commit()
            announce(bus, job, song, "progress")
        stand_in = await mix_run
        if stand_in is not None:
            await save_transcript(session, song.id, stand_in, LyricsSource.MIX_ASR)
            await session.commit()
            # Chapter 8's "lyrics on the way" state ends here for most songs:
            # there are words on the screen while the better ones are made.
            announce(bus, job, song, "progress")

    audio = await vocals_audio(session, storage, song)
    if audio is None:
        log.info("song %s has no vocals stem to transcribe", song.id)
        return

    await jobs.advance(session, job, JobStep.TRANSCRIBING_VOCALS, song)
    await session.commit()
    announce(bus, job, song, "progress")

    # The language the mix settled on, handed to the second run. This is not
    # tidiness, it is a measured fix: on a real Hebrew song the isolated vocals
    # stem was detected as English and came back transliterated into Latin
    # letters - `Me'onecha, deros na'ador she'cha` - and replaced a perfectly
    # good Hebrew stand-in. The mix, which still has the instruments in it, got
    # the language right.
    hint = language_code(stand_in.language) if stand_in is not None else None

    vocals = await asyncio.to_thread(transcribe, transcriber, audio, hint)
    if vocals is None:
        return

    if stand_in is not None and language_code(vocals.language) != language_code(stand_in.language):
        # Even with the hint. A run that changed language mid-song is a run that
        # went wrong, not a song that did, so the stand-in stays.
        log.info(
            "song %s: the vocals run came back as %s where the mix heard %s; keeping the mix",
            song.id,
            vocals.language,
            stand_in.language,
        )
        return

    await save_transcript(session, song.id, vocals, LyricsSource.VOCALS_ASR)
    await session.commit()


async def _ingest(
    session: AsyncSession,
    storage: Storage,
    job: Job,
    song: Song,
    bus: EventBus | None = None,
) -> None:
    """The upload already normalised the audio (T-1.5); this confirms it is there.

    It is a real step rather than a formality: a song whose object went missing
    should fail here, cheaply, rather than three minutes into a separation.
    """
    await jobs.advance(session, job, JobStep.INGESTING, song)
    await session.commit()
    announce(bus, job, song, "progress")
    try:
        source_for(storage, song)
    except SeparationError as exc:
        raise PipelineError("not_ingested", str(exc)) from exc


async def _separate(
    session: AsyncSession,
    storage: Storage,
    separator: Separator,
    job: Job,
    song: Song,
    bus: EventBus | None = None,
) -> None:
    await jobs.advance(session, job, JobStep.SEPARATING, song)
    await session.commit()
    announce(bus, job, song, "progress")

    with tempfile.TemporaryDirectory(prefix="karuki-job-") as tmp:
        try:
            # to_thread, not inline: see the module docstring.
            result = await asyncio.to_thread(separate, storage, separator, song, Path(tmp))
        except SeparationUnavailable as exc:
            # Not the song's fault: this process has no separation backend.
            raise PipelineError("separation_unavailable", str(exc)) from exc
        except SeparationError as exc:
            raise PipelineError("separation_failed", str(exc)) from exc

        await jobs.record_gpu_seconds(session, job, result.gpu_seconds)

        # Encoding happens inside the separator, so this step covers storing the
        # encoded stems and writing their rows - the part that is genuinely
        # still to do when separation returns.
        await jobs.advance(session, job, JobStep.ENCODING, song)
        await session.commit()
        announce(bus, job, song, "progress")
        await record_stems(session, storage, song, result)

        # Tempo and key. Deliberately not its own step: chapter 7's pipeline
        # does not have one, it takes about two seconds, and it cannot fail the
        # job. It runs here rather than during ingest so that it does not delay
        # the moment the song becomes playable.
        await analyse_song(session, storage, song)

    # D-28: four stems on disk is everything the player needs. The lyrics can
    # keep the user waiting; the singing does not have to.
    await jobs.mark_playable(session, job, song)
    await session.commit()
    # Chapter 6 names this event specifically: it must arrive before `ready`,
    # because it is the moment the user is allowed to start singing.
    announce(bus, job, song, "playable")

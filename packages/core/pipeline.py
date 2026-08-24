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
    silences_in,
    transcribe,
    vocals_audio,
)
from packages.core.usage import gpu_seconds_this_month, usd
from packages.providers.lyrics_catalogue import LyricsCatalogue
from packages.providers.monitoring import capture
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
        # A job that crashed is the report worth having: it happened without a
        # request to carry it, to somebody who is looking at a failed bar, and
        # nobody is reading the log of a free instance (T-3.12). A *handled*
        # failure - PipelineError above - is not reported: those are the file's
        # problem or the operator's, and they already have a code the screen
        # explains in Hebrew.
        capture(exc, job_id=str(job.id), step=str(job.current_step))
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
    def fetch_and_transcribe() -> Transcript | None:
        # Both halves are in the thread on purpose. The HTTP call to the
        # transcription service blocks, and so does getting the audio: on the
        # object store `mix_audio` downloads the whole normalised wav. T-3.5
        # measured that download holding the event loop for 39.5 seconds, during
        # which the SSE stream sent nothing and /system/health could not answer.
        audio = mix_audio(storage, song)
        if audio is None:  # pragma: no cover - _ingest has already checked this
            return None
        return transcribe(transcriber, audio)

    # The task only ever returns a value and never touches the session, which is
    # not safe to share.
    return asyncio.create_task(asyncio.to_thread(fetch_and_transcribe))


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

    # T-2.5. A step of its own because chapter 7 gives it one, and because it
    # is the one place that opens the audio again: the vocals stem is decoded to
    # find where the singing stops, which is what a segment gets split on.
    # Whisper's times are never moved by any of it - T-0.5.3 measured the
    # alternative and it was worse.
    await jobs.advance(session, job, JobStep.ALIGNING, song)
    await session.commit()
    announce(bus, job, song, "progress")

    gaps = await asyncio.to_thread(silences_in, audio)
    await save_transcript(session, song.id, vocals, LyricsSource.VOCALS_ASR, gaps)
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

    loop = asyncio.get_running_loop()

    def note_remote_call(call_id: str) -> None:
        """Record the call id from the worker thread, durably, before the wait.

        The separation runs in `to_thread`, so this runs there too and cannot
        touch the session directly. Handing the write back to the loop is safe
        precisely because the main flow is parked in that `to_thread` and
        nothing else is using the session.
        """
        asyncio.run_coroutine_threadsafe(_save_call_id(session, job, call_id), loop).result(30)

    try:
        # to_thread, not inline: see the module docstring.
        result = await asyncio.to_thread(separate, storage, separator, song, note_remote_call)
    except SeparationUnavailable as exc:
        # Not the song's fault: this process has no separation backend.
        raise PipelineError("separation_unavailable", str(exc)) from exc
    except SeparationError as exc:
        # A run that failed still spent what it spent, and those seconds come
        # off the same monthly credit as the successful ones.
        await jobs.record_gpu_seconds(session, job, exc.gpu_seconds)
        await session.commit()
        raise PipelineError("separation_failed", str(exc)) from exc

    await jobs.record_remote_call(session, job, result.remote_call_id)
    await jobs.record_gpu_seconds(session, job, result.gpu_seconds)
    spent = await gpu_seconds_this_month(session)
    # The one line that says what the run actually cost and where the time
    # went. On the remote backend it is the only place the fetch and upload
    # times exist at all - the GPU reports them and nothing else keeps them
    # until T-3.4 gives them a column.
    log.info(
        "job %s separated on %s (call %s): %.1fs gpu, %.0fs this month (~$%.2f), %s",
        job.id,
        result.backend,
        result.remote_call_id or "local",
        result.gpu_seconds or 0.0,
        spent,
        usd(spent),
        ", ".join(f"{name}={value}" for name, value in sorted(result.timings.items())),
    )

    # Encoding and storing both happen inside the separator - on the GPU since
    # T-3.3, which writes the stems to storage itself and never sends them
    # here. What is left for this step is the rows, and the moment they exist
    # the song is playable, which is what the step is really announcing.
    await jobs.advance(session, job, JobStep.ENCODING, song)
    await session.commit()
    announce(bus, job, song, "progress")
    await record_stems(session, song, result)

    # D-28: four stems in storage is everything the player needs. The lyrics can
    # keep the user waiting; the singing does not have to.
    await jobs.mark_playable(session, job, song)
    await session.commit()
    # Chapter 6 names this event specifically: it must arrive before `ready`,
    # because it is the moment the user is allowed to start singing.
    announce(bus, job, song, "playable")

    # Tempo and key, *after* the song is playable and not before (T-3.5).
    #
    # This used to run first, with a comment claiming it did not delay the
    # playable moment. On disk that was nearly true - two seconds of numpy. With
    # the object store it stopped being true at all: the analysis opens the
    # normalised audio, which for a four-minute song is a 47MB download, and the
    # user was waiting through it with four finished stems already sitting in
    # the bucket. Nothing failed; the singing just started later for no reason.
    #
    # It still cannot fail the job, and it is still not a JobStep: chapter 7 has
    # no step for it. The player picks the numbers up on its next poll.
    await analyse_song(session, storage, song)
    await session.commit()
    announce(bus, job, song, "progress")


async def _save_call_id(session: AsyncSession, job: Job, call_id: str) -> None:
    """The coroutine `note_remote_call` hands back to the loop."""
    await jobs.record_remote_call(session, job, call_id)
    await session.commit()

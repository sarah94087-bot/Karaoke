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
from packages.core.enums import JobStep
from packages.core.events import EventBus, EventType, JobEvent
from packages.core.lyrics_lookup import lookup_lyrics
from packages.core.models import Job, Song
from packages.core.stems import record_stems, separate, source_for
from packages.providers.lyrics_catalogue import LyricsCatalogue
from packages.providers.separation import (
    SeparationError,
    SeparationUnavailable,
    Separator,
)
from packages.providers.storage import Storage

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
) -> Job:
    """Take a queued job to `ready`, or to `failed` with a code.

    Committing at every step is deliberate and is what "survives a restart"
    means in practice: the progress a user is watching has to be durable at the
    moment they see it, not at the end.
    """
    await jobs.start(session, job)
    await session.commit()
    announce(bus, job, song, "progress")

    try:
        await _ingest(session, storage, job, song, bus)
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

    # D-08's first source of lyrics: a song somebody has already timed by hand.
    # Deliberately outside the try - and not a JobStep - for the same reasons
    # the analysis in T-1.15 is neither: chapter 7's pipeline has no step for
    # it, it is one HTTP call, and it cannot fail the job. It runs after the
    # song is playable, so the singing never waits for it.
    if catalogue is not None:
        await lookup_lyrics(session, song, catalogue)
        await session.commit()

    await jobs.finish(session, job, song)
    await session.commit()
    announce(bus, job, song, "ready")
    return job


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

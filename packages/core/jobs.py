"""The job state machine.

D-25 is the decision this file exists to honour: there is no Celery and no
Redis. The GPU platform is the queue, and the status lives in Postgres. That
makes this table the single source of truth about what is happening to a song,
and it is why "survives a restart" is a property of the schema rather than of a
process that has to be kept alive.

Two states and a step, not one field. `state` is coarse - queued, running,
ready, failed - and is what a library row is coloured by and what the "one
concurrent job per user" quota counts. `current_step` is the fine detail the
progress screen names in Hebrew. Chapter 5 gives a job both.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.enums import JobState, JobStep, LyricsStatus, SongStatus
from packages.core.models import Job, Song

# Chapter 7's pipeline, with the progress each step has reached when it
# finishes. The numbers follow the measured timings there rather than dividing
# the bar evenly: separation is the long pole.
#
# The measured shape is PLAYABLE at 0:48-2:15 against READY at 1:08-2:55, so
# roughly three quarters of the wait is over when the player can open - which is
# what 78 below is saying. Both transcription steps sit after it because that is
# where they are *reported*: the mix run starts during the separation (D-29) and
# is only named when the job ends up waiting for it, and the vocals run cannot
# start before there are vocals to run on.
STEP_PROGRESS: dict[JobStep, int] = {
    JobStep.INGESTING: 10,
    JobStep.SEPARATING: 50,
    JobStep.ENCODING: 78,
    JobStep.TRANSCRIBING_MIX: 84,
    JobStep.TRANSCRIBING_VOCALS: 92,
    JobStep.ALIGNING: 100,
}

# D-28, staged readiness: the player opens once the stems are encoded, which is
# well before the lyrics are aligned. This is the step that lights it up, and
# `Song.is_playable` is a separate column precisely so it can.
PLAYABLE_AFTER = JobStep.ENCODING

ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.FAILED}),
    JobState.RUNNING: frozenset({JobState.RUNNING, JobState.READY, JobState.FAILED}),
    # A failed job can be tried again, but only because somebody asked: chapter 7
    # forbids an automatic retry on a GPU step, since it costs double credit.
    JobState.FAILED: frozenset({JobState.QUEUED}),
    JobState.READY: frozenset(),
}

# What a job that was interrupted mid-flight is marked with. Not a retry: see
# `recover_interrupted`.
INTERRUPTED = "interrupted"


class InvalidTransition(RuntimeError):
    """An attempt to move the job somewhere the state machine does not go."""


def _now() -> datetime:
    return datetime.now(UTC)


def can_move(current: str, target: JobState) -> bool:
    return target in ALLOWED.get(JobState(current), frozenset())


def _move(job: Job, target: JobState) -> None:
    if not can_move(job.state, target):
        raise InvalidTransition(f"a {job.state} job cannot become {target}")
    job.state = str(target)


async def create_job(session: AsyncSession, song: Song, user_id: uuid.UUID) -> Job:
    """Queue a job for a song. Returns immediately, as chapter 6 requires."""
    job = Job(
        id=uuid.uuid4(),
        song_id=song.id,
        user_id=user_id,
        state=str(JobState.QUEUED),
        progress=0,
        attempts=0,
    )
    session.add(job)
    await session.flush()
    return job


async def start(session: AsyncSession, job: Job) -> Job:
    _move(job, JobState.RUNNING)
    job.attempts += 1
    job.started_at = _now()
    job.error_code = None
    await session.flush()
    return job


async def advance(session: AsyncSession, job: Job, step: JobStep, song: Song | None = None) -> Job:
    """Record that the job is now on `step`, and move the bar.

    Progress is derived from the step rather than passed in. A caller that can
    choose its own number will eventually report 90% twice, or go backwards.
    """
    _move(job, JobState.RUNNING)
    job.current_step = str(step)
    job.progress = STEP_PROGRESS[step]
    if song is not None:
        song.status = str(SongStatus.PROCESSING)
    await session.flush()
    return job


async def mark_playable(session: AsyncSession, job: Job, song: Song) -> Song:
    """D-28: the player can open now, before the lyrics are ready."""
    song.is_playable = True
    await session.flush()
    return song


async def finish(session: AsyncSession, job: Job, song: Song) -> Job:
    _move(job, JobState.READY)
    job.progress = 100
    job.current_step = None
    job.finished_at = _now()
    song.status = str(SongStatus.READY)
    if song.lyrics_status == LyricsStatus.PENDING:
        # The pipeline is done and nothing wrote any lyrics - which is exactly
        # where the pipeline stands until T-2.3 adds transcription. `pending`
        # has to mean "still coming", because that is what GET /songs/{id}/lyrics
        # answers 202 on; leaving it set on a finished song would promise words
        # that are never going to arrive.
        song.lyrics_status = str(LyricsStatus.MISSING)
    await session.flush()
    return job


async def fail(session: AsyncSession, job: Job, code: str, song: Song | None = None) -> Job:
    """Record a failure with a code the web app maps to Hebrew.

    The song is not marked failed if it is already playable: chapter 7 is
    explicit that a transcription or alignment failure is not a job failure, and
    a song you can sing over is not a broken song.
    """
    _move(job, JobState.FAILED)
    job.error_code = code
    job.finished_at = _now()
    if song is not None and not song.is_playable:
        song.status = str(SongStatus.FAILED)
    await session.flush()
    return job


async def retry(session: AsyncSession, job: Job) -> Job:
    """Put a failed job back in the queue. Deliberately manual.

    Chapter 7: no automatic retry on a GPU step, because a retry costs double
    credit and the user is the one who should decide to spend it. `attempts`
    counts the deliberate ones.
    """
    _move(job, JobState.QUEUED)
    job.progress = 0
    job.current_step = None
    job.error_code = None
    job.finished_at = None
    await session.flush()
    return job


async def record_remote_call(session: AsyncSession, job: Job, call_id: str | None) -> Job:
    """The handle on the work once it has left this process (chapter 5).

    Written the moment the call is handed over, before it is waited on: a job
    whose process dies mid-call is exactly the one that needs the id, and an id
    recorded after the call returns is an id you have only when you do not need
    it.
    """
    if call_id:
        job.remote_call_id = call_id
        await session.flush()
    return job


async def record_gpu_seconds(session: AsyncSession, job: Job, seconds: float | None) -> Job:
    """Chapter 7 calls this the only way to know how much credit is left."""
    if seconds is not None:
        job.gpu_seconds = (float(job.gpu_seconds) if job.gpu_seconds else 0.0) + seconds
        await session.flush()
    return job


async def recover_interrupted(session: AsyncSession) -> list[uuid.UUID]:
    """Called at startup: nothing is running, whatever the table says.

    Chapter 9 budgets for a single backend instance - 750 free hours a month is
    enough for one service running 24/7 and not two - and jobs run inside it. So
    a row still marked `running` when the process starts is a job whose process
    died, not one belonging to a peer.

    They are marked failed rather than re-queued. Re-queueing would be an
    automatic retry of a GPU step, which chapter 7 forbids because it costs
    double credit; the user is told what happened and decides.
    """
    interrupted = list(
        await session.scalars(select(Job.id).where(Job.state == str(JobState.RUNNING)))
    )
    if interrupted:
        await session.execute(
            update(Job)
            .where(Job.id.in_(interrupted))
            .values(
                state=str(JobState.FAILED),
                error_code=INTERRUPTED,
                finished_at=_now(),
            )
        )
        await session.commit()
    return interrupted


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def jobs_for_song(session: AsyncSession, song_id: uuid.UUID) -> Iterable[Job]:
    return await session.scalars(
        select(Job).where(Job.song_id == song_id).order_by(Job.created_at.desc())
    )


async def active_job_for_user(session: AsyncSession, user_id: uuid.UUID) -> Job | None:
    """Chapter 9 allows one concurrent job per user. This is what counts it."""
    return await session.scalar(
        select(Job).where(
            Job.user_id == user_id,
            Job.state.in_([str(JobState.QUEUED), str(JobState.RUNNING)]),
        )
    )

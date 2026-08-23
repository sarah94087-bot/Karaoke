"""Job status (chapter 6's `GET /jobs/{id}`), and asking for a job to be re-run.

The SSE stream that pushes these same changes is T-1.8; this is the polled form,
and it stays because a stream that drops has to have something to fall back to.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from packages.core import jobs as job_service
from packages.core.enums import JobState
from packages.core.events import JobEvent
from packages.core.models import Job, Song

from .. import sse
from ..deps import RunnerDep, SessionDep, SessionsDep, UserDep
from ..errors import ApiError

router = APIRouter(tags=["jobs"])

# How long the stream will sit on a silent queue before checking the row itself.
# Separation legitimately produces nothing for a minute or more, so this is not
# a poll in any meaningful sense - it is the safety net that stops a client
# hanging forever if an event is ever missed.
RECONCILE_SECONDS = 5.0


class JobStatus(BaseModel):
    """What the progress screen needs, in one poll."""

    id: uuid.UUID
    song_id: uuid.UUID
    state: str = Field(examples=["queued", "running", "ready", "failed"])
    current_step: str | None = Field(
        description="The stage of chapter 7's pipeline the job is on. Null when queued or done."
    )
    progress: int = Field(ge=0, le=100)
    is_playable: bool = Field(
        description="D-28: true as soon as the stems are encoded, which is before the job is "
        "ready. The player can open on this alone."
    )
    error_code: str | None = Field(
        description="What went wrong, for the web app to render in Hebrew."
    )
    attempts: int
    gpu_seconds: float | None
    remote_call_id: str | None = Field(
        default=None,
        description="The handle on the remote GPU call (T-3.4). Here so a job can be traced to "
        "the run that did the work without opening the database - the one thing worth having "
        "when a paid call goes wrong.",
    )


def as_status(job: Job, song: Song) -> JobStatus:
    return JobStatus(
        id=job.id,
        song_id=job.song_id,
        state=job.state,
        current_step=job.current_step,
        progress=job.progress,
        is_playable=song.is_playable,
        error_code=job.error_code,
        attempts=job.attempts,
        gpu_seconds=float(job.gpu_seconds) if job.gpu_seconds is not None else None,
        remote_call_id=job.remote_call_id,
    )


async def _load(session: SessionDep, job_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Job, Song]:
    """The job and its song, if the song is this user's.

    Checked through the song rather than through `jobs.user_id`: the song is
    what ownership is recorded on since T-3.7, and two places to ask the same
    question is one place to get a different answer. Somebody else's job is the
    same 404 a missing one gets - see `ownership.py`.
    """
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise ApiError("job_not_found", "no such job", status_code=status.HTTP_404_NOT_FOUND)
    song = await session.get(Song, job.song_id)
    if song is None:  # pragma: no cover - the foreign key makes this unreachable
        raise ApiError("song_not_found", "the job's song is gone", status_code=404)
    if song.user_id != user_id:
        raise ApiError("job_not_found", "no such job", status_code=status.HTTP_404_NOT_FOUND)
    return job, song


@router.get("/jobs/{job_id}", response_model=JobStatus, summary="Where a job has got to")
async def get_job(session: SessionDep, user_id: UserDep, job_id: uuid.UUID) -> JobStatus:
    job, song = await _load(session, job_id, user_id)
    return as_status(job, song)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobStatus,
    summary="Run a failed job again",
)
async def retry_job(
    session: SessionDep, runner: RunnerDep, user_id: UserDep, job_id: uuid.UUID
) -> JobStatus:
    """Manual on purpose.

    Chapter 7: no automatic retry on a GPU step, because a retry costs double
    credit. This endpoint is the user deciding to spend it.
    """
    job, song = await _load(session, job_id, user_id)
    if job.state != str(JobState.FAILED):
        raise ApiError(
            "job_not_retryable",
            f"a {job.state} job cannot be retried",
            status_code=status.HTTP_409_CONFLICT,
        )

    await job_service.retry(session, job)
    await session.commit()
    runner.schedule(job.id)
    return as_status(job, song)


@router.get(
    "/jobs/{job_id}/events",
    summary="Live progress (SSE)",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {sse.MEDIA_TYPE: {}},
            "description": (
                "A stream of `snapshot`, `progress`, `playable`, `ready` and `failed` events. "
                "The first message is always the current state: `snapshot` while the job is "
                "still going, or `ready`/`failed` if it has already finished - so a client that "
                "connects late, or reconnects, fires the same handler as one that watched "
                "throughout. `playable` always arrives before `ready`: it is the moment the "
                "user may start singing (D-28). The stream closes once the job is finished. "
                "`GET /jobs/{job_id}` is the fallback for a client that cannot use SSE."
            ),
        }
    },
)
async def job_events(
    request: Request,
    runner: RunnerDep,
    sessions: SessionsDep,
    user_id: UserDep,
    job_id: uuid.UUID,
) -> StreamingResponse:
    """D-18. The first message is always the current state, then live changes.

    The snapshot matters more than it looks: a client that connects after the
    job is already running - or after it has finished, having reconnected - has
    to be told where things stand rather than waiting for a change that may
    never come.
    """
    async with sessions() as session:
        # Checked before a single byte of the stream: an SSE response that
        # opened and then refused would be a 200 the client has to interpret.
        job = await job_service.get_job(session, job_id)
        song = await session.get(Song, job.song_id) if job is not None else None
        if job is None or song is None or song.user_id != user_id:
            raise ApiError("job_not_found", "no such job", status_code=status.HTTP_404_NOT_FOUND)

    async def stream() -> AsyncIterator[str]:
        yield sse.opening()

        # Subscribe *before* reading the state, not after. The other order
        # leaves a gap in which the job can finish unobserved: the snapshot
        # would say "running", the `ready` event would be published to nobody,
        # and the client would wait for a change that had already happened.
        async with runner.events.subscribe(job_id) as queue:
            snapshot = await _snapshot(sessions, job_id)
            if snapshot is None:
                return
            yield sse.frame(snapshot.type, snapshot.payload())
            if snapshot.is_final:
                return

            while not await request.is_disconnected():
                try:
                    event: JobEvent | None = await asyncio.wait_for(
                        queue.get(), timeout=RECONCILE_SECONDS
                    )
                except TimeoutError:
                    # Nothing for a while. Almost always a long separation, but
                    # it is also what a lost event would look like, so check the
                    # row rather than trust the silence forever.
                    event = await _snapshot(sessions, job_id)
                    if event is None or not event.is_final:
                        continue
                assert event is not None
                yield sse.frame(event.type, event.payload())
                if event.is_final:
                    return

    return StreamingResponse(
        sse.with_heartbeat(stream()),
        media_type=sse.MEDIA_TYPE,
        headers=sse.HEADERS,
    )


async def _snapshot(sessions: SessionsDep, job_id: uuid.UUID) -> JobEvent | None:
    """Read the current state, holding a connection for as short a time as possible.

    One database connection per watching browser, held for as long as the tab is
    open, is not something a free-tier Postgres has to spare.
    """
    async with sessions() as session:
        job = await job_service.get_job(session, job_id)
        if job is None:
            return None
        song = await session.get(Song, job.song_id)
        if song is None:  # pragma: no cover - the foreign key prevents this
            return None
        kind = "snapshot"
        if job.state == JobState.FAILED:
            kind = "failed"
        elif job.state == JobState.READY:
            kind = "ready"
        return _event(job, song, kind)


def _event(job: Job, song: Song, kind: str) -> JobEvent:
    return JobEvent(
        job_id=job.id,
        song_id=song.id,
        type=kind,  # type: ignore[arg-type]
        state=job.state,
        progress=job.progress,
        is_playable=song.is_playable,
        current_step=job.current_step,
        error_code=job.error_code,
    )

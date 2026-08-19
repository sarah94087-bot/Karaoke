"""Job status (chapter 6's `GET /jobs/{id}`), and asking for a job to be re-run.

The SSE stream that pushes these same changes is T-1.8; this is the polled form,
and it stays because a stream that drops has to have something to fall back to.
"""

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from packages.core import jobs as job_service
from packages.core.enums import JobState
from packages.core.models import Job, Song

from ..deps import RunnerDep, SessionDep
from ..errors import ApiError

router = APIRouter(tags=["jobs"])


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
    )


async def _load(session: SessionDep, job_id: uuid.UUID) -> tuple[Job, Song]:
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise ApiError("job_not_found", "no such job", status_code=status.HTTP_404_NOT_FOUND)
    song = await session.get(Song, job.song_id)
    if song is None:  # pragma: no cover - the foreign key makes this unreachable
        raise ApiError("song_not_found", "the job's song is gone", status_code=404)
    return job, song


@router.get("/jobs/{job_id}", response_model=JobStatus, summary="Where a job has got to")
async def get_job(session: SessionDep, job_id: uuid.UUID) -> JobStatus:
    job, song = await _load(session, job_id)
    return as_status(job, song)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobStatus,
    summary="Run a failed job again",
)
async def retry_job(session: SessionDep, runner: RunnerDep, job_id: uuid.UUID) -> JobStatus:
    """Manual on purpose.

    Chapter 7: no automatic retry on a GPU step, because a retry costs double
    credit. This endpoint is the user deciding to spend it.
    """
    job, song = await _load(session, job_id)
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

"""Service-level endpoints. Nothing here touches user data.

`/system/health` is the keep-alive target (D-26). An external cron hits it every
10 minutes, so several hundred times a day, purely to stop the free PaaS tier
from sleeping and dropping a 30-60s wake penalty on the first real user. That is
the whole reason it must stay cheap: **no database, no storage, no outbound
call**. A health check that talks to Postgres turns the thing that protects the
free tier into the thing that burns it.

Anything that does need to reach a dependency belongs in a separate, unlisted
readiness endpoint later - not here.
"""

import logging
import secrets
import time

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from packages.core import retention

from ..config import settings
from ..deps import get_sessions, get_storage
from ..errors import ApiError

router = APIRouter(tags=["system"])
log = logging.getLogger("karuki.api")

# Captured at import, i.e. at process start. monotonic() so that a clock
# adjustment on the host cannot produce a negative uptime.
_STARTED_AT = time.monotonic()


class Health(BaseModel):
    """Answer to the keep-alive ping."""

    status: str = Field(examples=["ok"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["local", "production"])
    uptime_sec: float = Field(
        description="Seconds since this process started. Resets to ~0 after a cold start, "
        "which is how you tell that keep-alive stopped working.",
        examples=[1234.5],
    )


@router.get(
    "/system/health",
    response_model=Health,
    summary="Liveness / keep-alive target",
)
async def health() -> Health:
    return Health(
        status="ok",
        version=settings.version,
        environment=settings.environment,
        uptime_sec=round(time.monotonic() - _STARTED_AT, 3),
    )


class ProbeError(RuntimeError):
    """Raised by the error probe, and by nothing else.

    Its own class so that it is one line to tell a deliberate error from a real
    one in the dashboard - including when filtering it back out later.
    """


@router.get(
    "/system/error",
    include_in_schema=False,
    summary="Deliberate error, for checking that monitoring works",
)
async def error_probe(token: str = "") -> None:
    """Chapter 14's checklist has an item that cannot be checked by reading:
    "a deliberate error appears in the monitoring tool". This is that error.

    It needs `KARUKI_ERROR_PROBE_TOKEN` to be set *and* matched. Unset is a
    404 - which is deliberately the same answer a wrong token gets, and which
    is also what a deployment gets if the variable is skipped, so the safe
    state is the default rather than something to remember to turn off. A
    route that anyone could use to raise 500s would be a way to spend a
    5,000-error monthly quota in an afternoon.
    """
    expected = settings.error_probe_token
    if not expected or not secrets.compare_digest(token, expected):
        raise ApiError("not_found", "no such endpoint", status_code=status.HTTP_404_NOT_FOUND)
    raise ProbeError("deliberate error from /system/error, to check that reporting works")


class ReapReport(BaseModel):
    """What the retention pass found, and what it did about it."""

    applied: bool = Field(description="False means nothing was changed.")
    days: int = Field(description="How long a song has to have been idle.")
    songs: int
    bytes: int
    freed_bytes: int = 0


@router.post(
    "/system/reap",
    response_model=ReapReport,
    include_in_schema=False,
    summary="Remove the audio of songs nobody has played (chapter 9), on a schedule",
)
async def reap(
    request: Request,
    token: str = "",
    apply: bool = False,
    days: int = retention.UNPLAYED_DAYS,
) -> ReapReport:
    """`scripts/reap.py` as an endpoint, so that a free deployment can run it.

    Chapter 9 wants this on a schedule and T-3.9 built it as a script for a
    machine with the database credentials on it. A deployment has no such
    machine: Render's cron jobs are a paid service type, and nothing here goes
    behind a payment method. The API already has the database and the storage,
    so the schedule can be an ordinary HTTP call from outside - which is the
    same shape as the keep-alive, and the same reasoning as chapter 14's rule
    that the cron must not live inside the service.

    **A dry run is the default here too** (T-3.9's reason: a command whose
    default is destructive is one that will one day be run by accident), and
    the token is its own - deliberately not the error probe's. One of these
    routes raises an exception; the other deletes audio, and they should not be
    opened by the same key.
    """
    # The token is checked before the database is even asked for, which is why
    # this takes the request rather than the usual dependencies: a stranger
    # should get the same 404 whether or not this deployment has a database,
    # and a 503 would tell them the route is real.
    expected = settings.maintenance_token
    if not expected or not secrets.compare_digest(token, expected):
        raise ApiError("not_found", "no such endpoint", status_code=status.HTTP_404_NOT_FOUND)

    sessions = get_sessions(request)
    storage = get_storage(request)
    async with sessions() as session:
        candidates = await retention.reapable(session, days=days)
        report = ReapReport(
            applied=apply,
            days=days,
            songs=len(candidates),
            bytes=sum(candidate.bytes for candidate in candidates),
        )
        if not apply or not candidates:
            log.info("retention: %d song(s), %d bytes, nothing changed", report.songs, report.bytes)
            return report

        for candidate in candidates:
            report.freed_bytes += await retention.archive(session, storage, candidate.song_id)
        await session.commit()
    log.info("retention: archived %d song(s), freed %d bytes", report.songs, report.freed_bytes)
    return report

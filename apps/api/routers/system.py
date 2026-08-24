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

import secrets
import time

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from ..config import settings
from ..errors import ApiError

router = APIRouter(tags=["system"])

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

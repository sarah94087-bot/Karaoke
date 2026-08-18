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

import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import settings

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

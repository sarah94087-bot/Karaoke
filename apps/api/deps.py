"""What endpoints ask for, and where it comes from.

The engine, the session factory and the storage backend are built once in the
lifespan (see main.py) and hung on `app.state`. Endpoints reach them through
these dependencies rather than importing a module-level singleton, so a test can
build an app with a temporary storage root and no database and still exercise
the parts that do not need one.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.providers.storage import Storage

from .errors import ApiError
from .runner import JobRunner


def get_storage(request: Request) -> Storage:
    storage: Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:  # pragma: no cover - a misconfigured app, not a request
        raise ApiError("storage_unavailable", "storage is not configured", status_code=503)
    return storage


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] | None = getattr(request.app.state, "sessions", None)
    if factory is None:
        # The API starts without a database on purpose: /system/health must
        # answer during a cold start or a database outage, because it is what
        # keep-alive polls. Endpoints that need data say so by failing here.
        raise ApiError("database_unavailable", "the database is not reachable", status_code=503)
    async with factory() as session:
        yield session


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner | None = getattr(request.app.state, "runner", None)
    if runner is None:
        raise ApiError(
            "processing_unavailable",
            "the service cannot run jobs right now",
            status_code=503,
        )
    return runner


SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]

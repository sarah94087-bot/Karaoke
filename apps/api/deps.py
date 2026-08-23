"""What endpoints ask for, and where it comes from.

The engine, the session factory and the storage backend are built once in the
lifespan (see main.py) and hung on `app.state`. Endpoints reach them through
these dependencies rather than importing a module-level singleton, so a test can
build an app with a temporary storage root and no database and still exercise
the parts that do not need one.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.providers.lyrics_catalogue import LyricsCatalogue, NoCatalogue
from packages.providers.storage import Storage

from .auth import AuthError, Verifier, bearer_token
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


def get_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    """The factory, not a session.

    For the SSE stream, which needs one short read and then must let go of the
    connection rather than holding it open for as long as a browser tab is.
    """
    factory: async_sessionmaker[AsyncSession] | None = getattr(request.app.state, "sessions", None)
    if factory is None:
        raise ApiError("database_unavailable", "the database is not reachable", status_code=503)
    return factory


def get_catalogue(request: Request) -> LyricsCatalogue:
    """The open lyrics database, or a stand-in that finds nothing.

    Unlike storage and the database, a missing catalogue is not a 503: the app
    works without one, it just cannot offer somebody else's timings.
    """
    return getattr(request.app.state, "catalogue", None) or NoCatalogue()


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner | None = getattr(request.app.state, "runner", None)
    if runner is None:
        raise ApiError(
            "processing_unavailable",
            "the service cannot run jobs right now",
            status_code=503,
        )
    return runner


def current_user(request: Request) -> uuid.UUID:
    """Who is asking, or a 401.

    Every song-scoped endpoint takes this. There is no "optional user" variant
    on purpose: an endpoint that works with or without one is an endpoint whose
    author has not decided what it does, and T-3.7 is exactly the task where
    that has to be decided.
    """
    verifier: Verifier | None = getattr(request.app.state, "verifier", None)
    if verifier is None:  # pragma: no cover - a misconfigured app
        raise ApiError("auth_unavailable", "authentication is not configured", status_code=503)
    try:
        return verifier.user_id(bearer_token(request.headers.get("Authorization")))
    except AuthError as exc:
        # One code for every reason. Telling a caller whether a token was
        # expired, forged or simply absent is telling them how to get closer.
        raise ApiError("not_signed_in", str(exc), status_code=401) from exc


SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(current_user)]
StorageDep = Annotated[Storage, Depends(get_storage)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SessionsDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessions)]
CatalogueDep = Annotated[LyricsCatalogue, Depends(get_catalogue)]

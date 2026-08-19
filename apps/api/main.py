"""Application factory and ASGI entry point.

    uvicorn apps.api.main:app --reload

The factory exists so tests can build a fresh app rather than reaching into a
module-level singleton, which matters once T-1.3 puts a database behind it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from packages.core.db import create_engine, session_factory
from packages.core.jobs import recover_interrupted
from packages.providers.separation import get_separator
from packages.providers.storage import LocalStorage

from .config import API_PREFIX, settings
from .errors import install_error_handlers
from .middleware import RequestIDMiddleware
from .request_id import HEADER
from .routers import jobs, songs, system
from .runner import JobRunner

log = logging.getLogger("karuki.api")

DESCRIPTION = """\
Backend for the Hebrew karaoke player: upload a song, separate it into stems,
and play it back with real-time key and tempo control and timed lyrics.

Every response carries a `request_id`, echoed in the `X-Request-ID` header.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the engine and the storage backend once, and let them go on exit.

    A missing or unreachable DATABASE_URL is logged and left alone rather than
    raising. /system/health is the keep-alive target and has to answer during a
    database outage; endpoints that actually need data fail on their own, in
    deps.get_session, with a code the web app can render.
    """
    app.state.storage = LocalStorage(Path(settings.storage_root))
    app.state.engine = None
    app.state.sessions = None
    app.state.runner = None

    if settings.database_url:
        engine = create_engine(settings.database_url)
        sessions = session_factory(engine)
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.runner = JobRunner(
            sessions=sessions,
            storage=app.state.storage,
            separator=get_separator(settings.separation_backend),
        )

        # Nothing is running, whatever the table says: jobs run inside this
        # process, and this process has just started. Rows left in `running` by
        # a crash or a redeploy are marked failed here so a user sees "it
        # stopped" instead of a bar that never moves again.
        async with sessions() as session:
            interrupted = await recover_interrupted(session)
        if interrupted:
            log.warning("marked %d interrupted job(s) failed at startup", len(interrupted))
    else:
        log.warning("DATABASE_URL is not set; endpoints that need data will return 503")

    try:
        yield
    finally:
        if app.state.runner is not None:
            await app.state.runner.drain()
        if app.state.engine is not None:
            await app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="karuki API",
        version=settings.version,
        description=DESCRIPTION,
        # Chapter 6: the REST surface lives under /api/v1. The docs are kept on
        # at the root so they are reachable without knowing the prefix.
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Order matters: CORS is added last so it runs first, and therefore still
    # answers a preflight if something further in fails.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER],
    )

    install_error_handlers(app)

    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(songs.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    # Same handler, unprefixed and unlisted: the container HEALTHCHECK and the
    # external keep-alive cron are configured once and should not have to be
    # re-pointed when the API version prefix moves.
    app.include_router(system.router, include_in_schema=False)

    return app


app = create_app()

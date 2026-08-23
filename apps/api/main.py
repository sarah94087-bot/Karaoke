"""Application factory and ASGI entry point.

    uvicorn apps.api.main:app --reload

The factory exists so tests can build a fresh app rather than reaching into a
module-level singleton, which matters once T-1.3 puts a database behind it.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from packages.core.db import create_engine, session_factory
from packages.core.jobs import recover_interrupted
from packages.providers.lyrics_catalogue import get_catalogue
from packages.providers.separation import get_separator
from packages.providers.storage import Storage, get_storage
from packages.providers.transcription import get_transcriber

from .auth import get_verifier
from .config import API_PREFIX, settings
from .errors import install_error_handlers
from .middleware import RequestIDMiddleware
from .request_id import HEADER
from .routers import files, jobs, lyrics, songs, system
from .runner import JobRunner

log = logging.getLogger("karuki.api")

# uvicorn configures its own loggers and leaves the root at WARNING, so every
# `log.info` this project writes - the separation timings, the recovery notice
# at startup - was going nowhere. Set once, here, because this module is what
# both entry points import.
logging.basicConfig(
    level=os.getenv("KARUKI_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DESCRIPTION = """\
Backend for the Hebrew karaoke player: upload a song, separate it into stems,
and play it back with real-time key and tempo control and timed lyrics.

Every response carries a `request_id`, echoed in the `X-Request-ID` header.
"""


def build_storage() -> Storage:
    """The storage backend named by the environment (D-12).

    Assembled here rather than in the provider so that nothing under
    `packages/` has to know the API's settings object exists.
    """
    s3 = None
    if settings.storage_backend == "s3":
        from packages.providers.storage_s3 import S3Config

        s3 = S3Config(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key_id=settings.s3_key_id,
            secret_access_key=settings.s3_secret,
        )
    return get_storage(
        settings.storage_backend,
        root=Path(settings.storage_root),
        secret=settings.signing_secret,
        base_url=settings.public_base_url,
        s3=s3,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the engine and the storage backend once, and let them go on exit.

    A missing or unreachable DATABASE_URL is logged and left alone rather than
    raising. /system/health is the keep-alive target and has to answer during a
    database outage; endpoints that actually need data fail on their own, in
    deps.get_session, with a code the web app can render.
    """
    app.state.storage = build_storage()
    # Built once: it caches the project's public keys, and a per-request
    # verifier would fetch them on every request.
    app.state.verifier = get_verifier(settings.supabase_url, settings.dev_user_id)
    # One instance, shared by the job runner and by the manual search endpoint,
    # so there is a single place that decides which database is in use.
    app.state.catalogue = get_catalogue(settings.lyrics_catalogue)
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
            catalogue=app.state.catalogue,
            transcriber=get_transcriber(settings.transcription_backend),
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
    # A production deployment with no identity provider would serve every user
    # the same library. Better to refuse to start than to find that out from
    # the outside.
    if not settings.is_local and not settings.supabase_url:
        raise RuntimeError(
            "SUPABASE_URL is required outside local development: without it every request "
            "is the same user and songs are not protected from one another"
        )

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
    app.include_router(lyrics.router, prefix=API_PREFIX)
    app.include_router(files.router, prefix=API_PREFIX)
    # Same handler, unprefixed and unlisted: the container HEALTHCHECK and the
    # external keep-alive cron are configured once and should not have to be
    # re-pointed when the API version prefix moves.
    app.include_router(system.router, include_in_schema=False)

    return app


app = create_app()

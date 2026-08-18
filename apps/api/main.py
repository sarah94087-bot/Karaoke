"""Application factory and ASGI entry point.

    uvicorn apps.api.main:app --reload

The factory exists so tests can build a fresh app rather than reaching into a
module-level singleton, which matters once T-1.3 puts a database behind it.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import API_PREFIX, settings
from .errors import install_error_handlers
from .middleware import RequestIDMiddleware
from .request_id import HEADER
from .routers import system

DESCRIPTION = """\
Backend for the Hebrew karaoke player: upload a song, separate it into stems,
and play it back with real-time key and tempo control and timed lyrics.

Every response carries a `request_id`, echoed in the `X-Request-ID` header.
"""


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
    # Same handler, unprefixed and unlisted: the container HEALTHCHECK and the
    # external keep-alive cron are configured once and should not have to be
    # re-pointed when the API version prefix moves.
    app.include_router(system.router, include_in_schema=False)

    return app


app = create_app()

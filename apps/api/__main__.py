"""Run the API locally:  python -m apps.api

This exists because of a Windows-only trap that costs an afternoon to diagnose.
psycopg's async driver refuses to run on a ProactorEventLoop, which is what
Python uses by default on Windows, and the failure surfaces during startup with
a message about the event loop that reads like a broken database.

Two things do *not* fix it, both of which look like they should:

- setting the policy inside `main.py`, because `uvicorn apps.api.main:app`
  imports the app after the loop already exists;
- setting the policy before `uvicorn.run(...)`, because uvicorn builds its loop
  from a `loop_factory` and never consults the policy at all.

So the server is run on a loop this module creates. On Linux, and therefore in
the container, `use_a_loop_psycopg_can_run_on` does nothing and the plain
`uvicorn apps.api.main:app` command in the Dockerfile is unaffected.
"""

import argparse
import asyncio

import uvicorn

from packages.core.db import use_a_loop_psycopg_can_run_on


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the karuki API for local development.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on code changes. Uses uvicorn's supervisor, which spawns its own "
        "process and therefore its own loop - see the note below.",
    )
    args = parser.parse_args()

    use_a_loop_psycopg_can_run_on()

    if args.reload:
        # The reloader runs the server in a child process that builds its own
        # loop, so the policy set above does not reach it. `--reload` is
        # therefore only useful for work that does not touch the database.
        uvicorn.run(
            "apps.api.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level=args.log_level,
        )
        return

    config = uvicorn.Config(
        "apps.api.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()

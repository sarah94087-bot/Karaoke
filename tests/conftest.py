"""Test-wide setup, and the fixtures the database-backed tests share.

The event loop policy is set at import: psycopg's async driver refuses to run on
Windows' default ProactorEventLoop, with a message that reads like a broken
database rather than a broken loop. Linux and the container are unaffected.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

from packages.core.db import use_a_loop_psycopg_can_run_on

use_a_loop_psycopg_can_run_on()

ROOT = Path(__file__).resolve().parent.parent

# The database-backed tests drop every table, so they run against a local
# database or not at all.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The local database, or a skip.

    A skip rather than a failure because `scripts/check.py` has to stay green on
    a machine with nothing running, and CI on a free tier has no Postgres.
    """
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL is not set; start the compose stack to run these")
    host = urlparse(url).hostname or ""
    if host not in LOCAL_HOSTS:
        pytest.skip(f"refusing to drop tables on a non-local database ({host})")

    psycopg = pytest.importorskip("psycopg", reason="psycopg is in the api dependency group")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database at {urlparse(url).netloc}: {exc}")
    return url


@pytest.fixture(scope="session")
def connect(database_url: str) -> Callable[[], "object"]:
    import psycopg

    def _connect() -> psycopg.Connection:
        return psycopg.connect(database_url, autocommit=True)

    return _connect


@pytest.fixture(scope="session")
def alembic(database_url: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": database_url, "PYTHONIOENCODING": "utf-8"},
        )
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"
        return result

    return _alembic


@pytest.fixture(scope="session")
def schema(alembic: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    """The tables, present. Session-scoped: migrating once is enough."""
    alembic("upgrade", "head")


@pytest.fixture
def empty_songs(schema: None, connect: Callable[[], "object"]) -> Iterator[None]:
    """Leave the songs table as it was found. Stems and jobs cascade."""
    yield
    with connect() as conn:
        conn.execute("delete from songs")

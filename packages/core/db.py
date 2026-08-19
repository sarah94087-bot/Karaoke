"""The declarative base, and how the rest of the system gets a session.

Async throughout, because the API is async and a blocking driver call inside an
async endpoint stalls the whole event loop - which matters more than usual here,
where chapter 9 budgets for exactly one backend instance on a free tier.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Without this, constraints and indexes get names chosen by Postgres, and an
# Alembic downgrade cannot drop by name what it did not name. It is the
# difference between "runs down cleanly" and "runs down on this machine".
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def use_a_loop_psycopg_can_run_on() -> None:
    """Windows-only, and only for processes that open an async connection.

    Python defaults to ProactorEventLoop on Windows and psycopg's async driver
    refuses to run on it: "Psycopg cannot use the 'ProactorEventLoop'". It
    never comes up in the container, which is Linux, so it looks like a broken
    database until you read the message. Call this before asyncio.run().
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def database_url(raw: str | None = None) -> str:
    """The URL in the form SQLAlchemy's async engine wants.

    Compose, and every hosted Postgres, hands out `postgresql://...`. The async
    engine needs the driver named explicitly, so the two are reconciled in one
    place rather than in every caller's environment file.
    """
    url = raw if raw is not None else os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(database_url(url), pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def sessions(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session

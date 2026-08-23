"""What the GPU has cost this month.

Chapter 7 calls `gpu_seconds` the only way to know how much free credit is left,
and phase 0 found the credit was $1 rather than $30 - so "how much is left" is a
question with a real answer that matters. T-3.3 moved the transfers inside the
billed window and roughly doubled the per-song cost, which is exactly the kind
of change that has to be visible without anybody remembering to look.

The month is the calendar month in UTC, because that is how the credit resets.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import Job

# Modal's T4, from the workspace's own pricing page (docs/phase0/quotas.md).
T4_USD_PER_HOUR = 0.59
# The monthly credit on the tier with no payment method attached.
MONTHLY_CREDIT_USD = 1.00


def start_of_month(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def gpu_seconds_this_month(
    session: AsyncSession, user_id: uuid.UUID | None = None, *, now: datetime | None = None
) -> float:
    """Every second billed since the first of the month, failures included.

    A job that failed halfway through still spent what it spent; counting only
    the successes is how a credit runs out unexpectedly.
    """
    query = select(func.coalesce(func.sum(Job.gpu_seconds), 0)).where(
        Job.created_at >= start_of_month(now)
    )
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    return float(await session.scalar(query) or 0.0)


def usd(seconds: float) -> float:
    return round(seconds * T4_USD_PER_HOUR / 3600, 4)

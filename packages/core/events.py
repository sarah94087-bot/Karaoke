"""Progress events, published in-process and read by the SSE stream (D-18).

Why not poll the database, and why not LISTEN/NOTIFY.

Polling is the obvious answer and it is the wrong shape here: every connected
client would run a query several times a second for the whole length of a job,
against a managed Postgres on a free tier where connections are the scarce
resource. LISTEN/NOTIFY fixes the query cost but wants a connection dedicated to
listening, and doing that per client is worse, not better.

Neither is needed, because of D-25: there is no separate worker. The job runs
inside this process, so the process already knows the instant anything changes
and can simply say so. The cost is one asyncio queue per connected client and no
database traffic at all while a job runs.

What this assumes is that there is exactly one API instance - which chapter 9
does not merely allow but requires, since a month is ~730 hours and the free
tiers give ~750, enough for one service running continuously and not two. If
that ever stops being true, this is the file that has to become LISTEN/NOTIFY,
and `GET /jobs/{id}` is the fallback that keeps working meanwhile.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from typing import Literal

EventType = Literal["snapshot", "progress", "playable", "ready", "failed"]

# Enough room for a slow client to fall behind a burst without being cut off,
# small enough that a client which has stopped reading entirely cannot grow the
# process's memory. A job emits well under this many events in total.
QUEUE_SIZE = 64


@dataclass(frozen=True)
class JobEvent:
    """One change worth telling the progress screen about."""

    job_id: uuid.UUID
    type: EventType
    state: str
    progress: int
    is_playable: bool
    current_step: str | None = None
    error_code: str | None = None

    def payload(self) -> dict[str, object]:
        data = asdict(self)
        data["job_id"] = str(self.job_id)
        return data

    @property
    def is_final(self) -> bool:
        """After this, nothing more will happen and the stream can close."""
        return self.type in ("ready", "failed")


class EventBus:
    """Fan-out from the job runner to whoever is watching."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[JobEvent]]] = {}

    def publish(self, event: JobEvent) -> None:
        """Never blocks and never raises.

        A progress update is not worth failing a job over: if a client's queue
        is full it is not keeping up, and the snapshot it gets on reconnect - or
        the polled endpoint - will tell it the truth anyway.
        """
        for queue in self._subscribers.get(event.job_id, set()):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, job_id: uuid.UUID) -> AsyncIterator[asyncio.Queue[JobEvent]]:
        queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(job_id, set()).add(queue)
        try:
            yield queue
        finally:
            watchers = self._subscribers.get(job_id)
            if watchers is not None:
                watchers.discard(queue)
                if not watchers:
                    del self._subscribers[job_id]

    def watcher_count(self, job_id: uuid.UUID) -> int:
        return len(self._subscribers.get(job_id, set()))

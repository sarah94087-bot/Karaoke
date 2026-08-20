"""Running jobs inside the API process.

D-25 removed the separate worker: the GPU platform is the queue and Postgres
holds the status. Chapter 9 explains the arithmetic behind that - a month is
about 730 hours and the free tiers give roughly 750 of runtime, which is enough
for one service running continuously and not two. So the job runs here.

"Here" still must not mean "on the event loop". The blocking part is handed to a
worker thread by the pipeline; this module is only about starting the task,
keeping a reference to it, and making sure a job that is already running is not
started twice.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core import jobs as job_service
from packages.core.events import EventBus
from packages.core.models import Song
from packages.core.pipeline import run_job
from packages.providers.lyrics_catalogue import LyricsCatalogue, NoCatalogue
from packages.providers.separation import Separator
from packages.providers.storage import Storage

log = logging.getLogger("karuki.runner")


@dataclass
class JobRunner:
    """Starts jobs and remembers the ones in flight."""

    sessions: async_sessionmaker[AsyncSession]
    storage: Storage
    separator: Separator
    # The open lyrics database (T-2.2). Defaults to none so that a test which
    # builds a runner by hand never reaches the network.
    catalogue: LyricsCatalogue = field(default_factory=NoCatalogue)
    # Shared with the SSE endpoint: the runner publishes, the stream subscribes.
    events: EventBus = field(default_factory=EventBus)
    _running: dict[uuid.UUID, asyncio.Task[None]] = field(default_factory=dict)

    def schedule(self, job_id: uuid.UUID) -> None:
        """Start a job in the background. Returns at once, as chapter 6 requires."""
        if job_id in self._running:
            log.info("job %s is already running; not starting it twice", job_id)
            return

        task = asyncio.create_task(self._run(job_id))
        # asyncio keeps only a weak reference to a running task, so a task with
        # no strong reference anywhere can be garbage collected mid-flight. This
        # dict is that reference as much as it is bookkeeping.
        self._running[job_id] = task
        task.add_done_callback(lambda _: self._running.pop(job_id, None))

    def is_running(self, job_id: uuid.UUID) -> bool:
        return job_id in self._running

    async def _run(self, job_id: uuid.UUID) -> None:
        # Its own session: the request that scheduled this has long since
        # returned, and its session is closed.
        async with self.sessions() as session:
            job = await job_service.get_job(session, job_id)
            if job is None:
                log.warning("job %s vanished before it could run", job_id)
                return
            song = await session.get(Song, job.song_id)
            if song is None:  # pragma: no cover - the foreign key prevents this
                log.warning("job %s has no song", job_id)
                return
            try:
                await run_job(
                    session,
                    self.storage,
                    self.separator,
                    job,
                    song,
                    self.events,
                    catalogue=self.catalogue,
                )
            except Exception:
                # run_job has already recorded the failure on the row; this is
                # only so the traceback is not swallowed by the task.
                log.exception("job %s ended badly", job_id)

    async def drain(self) -> None:
        """On shutdown, stop waiting on jobs rather than blocking the exit.

        The rows they leave behind are picked up by `recover_interrupted` at the
        next startup, which is the honest answer: a half-finished separation is
        not resumable, and pretending otherwise would leave a job that never
        moves again.
        """
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()

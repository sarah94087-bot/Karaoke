"""The event bus and the SSE framing, without a server.

These are the parts that are easy to get subtly wrong and hard to see wrong from
the outside: a slow client blocking the job that is publishing to it, a
subscriber that is never cleaned up, a frame missing its blank line.
"""

import asyncio
import uuid

import pytest

from apps.api import sse
from packages.core.events import QUEUE_SIZE, EventBus, JobEvent


def an_event(job_id: uuid.UUID, kind: str = "progress", progress: int = 50) -> JobEvent:
    return JobEvent(
        job_id=job_id,
        type=kind,  # type: ignore[arg-type]
        state="running",
        progress=progress,
        is_playable=False,
        current_step="separating",
    )


# --- the bus ----------------------------------------------------------------


async def test_a_subscriber_receives_what_is_published():
    bus = EventBus()
    job_id = uuid.uuid4()

    async with bus.subscribe(job_id) as queue:
        bus.publish(an_event(job_id))

        received = await asyncio.wait_for(queue.get(), timeout=1)

    assert received.progress == 50


async def test_two_subscribers_both_receive():
    """Two tabs on the same progress screen is not an exotic case."""
    bus = EventBus()
    job_id = uuid.uuid4()

    async with bus.subscribe(job_id) as first, bus.subscribe(job_id) as second:
        bus.publish(an_event(job_id))

        assert (await asyncio.wait_for(first.get(), 1)).progress == 50
        assert (await asyncio.wait_for(second.get(), 1)).progress == 50


async def test_events_do_not_leak_between_jobs():
    bus = EventBus()
    mine, someone_elses = uuid.uuid4(), uuid.uuid4()

    async with bus.subscribe(mine) as queue:
        bus.publish(an_event(someone_elses))

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.05)


async def test_unsubscribing_leaves_nothing_behind():
    """A leak here would be one queue per progress screen ever opened."""
    bus = EventBus()
    job_id = uuid.uuid4()

    async with bus.subscribe(job_id):
        assert bus.watcher_count(job_id) == 1

    assert bus.watcher_count(job_id) == 0


async def test_publishing_with_nobody_listening_is_fine():
    """The common case: a job runs and no browser is watching."""
    bus = EventBus()

    bus.publish(an_event(uuid.uuid4()))


async def test_a_client_that_stopped_reading_cannot_block_the_job():
    """The job publishes from the pipeline. If a full queue blocked, one dead
    browser tab would stall the separation of a song."""
    bus = EventBus()
    job_id = uuid.uuid4()

    async with bus.subscribe(job_id) as queue:
        for i in range(QUEUE_SIZE + 20):
            bus.publish(an_event(job_id, progress=i))

        assert queue.qsize() == QUEUE_SIZE


def test_a_finished_event_is_marked_final():
    job_id = uuid.uuid4()

    assert an_event(job_id, "ready").is_final
    assert an_event(job_id, "failed").is_final
    assert not an_event(job_id, "progress").is_final
    assert not an_event(job_id, "playable").is_final, (
        "playable is not the end - ready still has to arrive"
    )


def test_the_payload_is_json_safe():
    """A UUID is not, and it is the first field."""
    import json

    payload = an_event(uuid.uuid4()).payload()

    assert json.loads(json.dumps(payload))["state"] == "running"


# --- the framing ------------------------------------------------------------


def test_a_frame_ends_with_a_blank_line():
    """Without it the client buffers the message forever, waiting for the end."""
    text = sse.frame("progress", {"a": 1})

    assert text.endswith("\n\n")
    assert text.startswith("event: progress\n")


def test_hebrew_survives_the_framing():
    """Error messages are Hebrew; escaping them would show mojibake to a user."""
    text = sse.frame("failed", {"message": "העיבוד נכשל"})

    assert "העיבוד נכשל" in text


def test_a_comment_carries_no_event():
    """Heartbeats must not look like data to the client."""
    text = sse.comment("keep-alive")

    assert text.startswith(":")
    assert "event:" not in text


def test_the_stream_tells_the_browser_how_long_to_wait_before_reconnecting():
    assert sse.opening().startswith("retry: ")


async def test_the_heartbeat_fills_a_silence():
    """During separation there is genuinely nothing to send for a minute or
    more, and an idle connection gets closed by proxies."""

    async def slow():
        await asyncio.sleep(0.3)
        yield sse.frame("ready", {})

    chunks = [chunk async for chunk in sse.with_heartbeat(slow(), interval=0.05)]

    assert any(chunk.startswith(":") for chunk in chunks), "no heartbeat during the silence"
    assert chunks[-1].startswith("event: ready")


async def test_the_heartbeat_does_not_delay_a_real_event():
    async def prompt():
        yield sse.frame("progress", {"progress": 10})

    chunks = [chunk async for chunk in sse.with_heartbeat(prompt(), interval=5)]

    assert chunks == [sse.frame("progress", {"progress": 10})]

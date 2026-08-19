"""Server-sent events, framed by hand.

SSE is nine lines of formatting, and a dependency for it would be a dependency
to keep working on a free PaaS tier. The rules that actually matter are the ones
below: every message ends with a blank line, comments start with a colon and are
ignored by the client, and `retry:` tells the browser's EventSource how long to
wait before reconnecting on its own.
"""

import json
from collections.abc import AsyncIterator

# EventSource reconnects by itself when a stream drops. Three seconds is long
# enough not to hammer a service that is restarting and short enough that a user
# watching a progress bar does not notice the gap.
RETRY_MS = 3000

# A proxy that sees nothing on a connection for long enough will close it. This
# is under the usual 30-60s idle timeout, and during separation - the long step -
# there is genuinely nothing else to send.
HEARTBEAT_SECONDS = 15.0

MEDIA_TYPE = "text/event-stream"

# text/event-stream through a buffering reverse proxy arrives all at once at the
# end, which is indistinguishable from the feature not working.
HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx
}


def frame(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def comment(text: str) -> str:
    """A no-op message. Keeps the connection warm without the client seeing it."""
    return f": {text}\n\n"


def opening() -> str:
    return f"retry: {RETRY_MS}\n\n"


async def with_heartbeat(
    events: AsyncIterator[str], interval: float = HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """Yield from `events`, emitting a comment during long silences."""
    import asyncio

    iterator = events.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield comment("keep-alive")
                continue
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
    finally:
        if pending is not None:
            pending.cancel()

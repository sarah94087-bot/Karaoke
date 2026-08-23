"""Whose song is this (T-3.7).

One function, used by every endpoint that names a song, because the failure mode
of "each router checks in its own way" is that one of them forgets and nobody
notices until somebody sees a stranger's library.

**Somebody else's song is a 404, not a 403.** A 403 says "this exists and it is
not yours", which answers a question the asker had no right to ask - with song
ids being uuids, the only way to find one is to have been given it. The two
cases are indistinguishable from outside, which is the point.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import Song

from .errors import ApiError


async def owned_song(session: AsyncSession, song_id: uuid.UUID, user_id: uuid.UUID) -> Song:
    """The song, if it is theirs. Otherwise the same 404 a missing song gets."""
    song = await session.get(Song, song_id)
    if song is None or song.user_id != user_id:
        raise ApiError("song_not_found", "no such song", status_code=404)
    return song

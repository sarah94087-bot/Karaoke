"""The account: where this user stands against chapter 9's limits (T-3.8).

Chapter 6 calls it `GET /me/quota`. The screen it feeds is the one D-30
describes - what is used, what is left, and if the storage is full, which songs
to remove - so the candidates come back with the numbers rather than from a
second request the client has to know to make.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from packages.core import quota

from ..deps import SessionDep, UserDep

router = APIRouter(tags=["account"])


class RemovalCandidate(BaseModel):
    """A song offered up when there is no room. D-30 asks for these by name."""

    song_id: uuid.UUID
    title: str
    bytes: int
    last_played_at: str | None = Field(
        description="Null means it has never been played, which is what makes it a candidate."
    )


class Quota(BaseModel):
    songs_this_month: int
    songs_per_month: int
    songs_left: int
    storage_bytes: int
    storage_limit_bytes: int
    storage_left_bytes: int
    running_jobs: int
    concurrent_jobs: int
    max_song_seconds: int
    candidates: list[RemovalCandidate] = Field(
        description="Least played first. Present whether or not the quota is reached, so a "
        "screen can offer to free space before it becomes urgent."
    )


@router.get("/me/quota", response_model=Quota, summary="Where this user stands")
async def my_quota(session: SessionDep, user_id: UserDep) -> Quota:
    standing = await quota.usage(session, user_id)
    candidates = await quota.crowding_out(session, user_id)
    return Quota(
        songs_this_month=standing.songs_this_month,
        songs_per_month=standing.songs_per_month,
        songs_left=standing.songs_left,
        storage_bytes=standing.storage_bytes,
        storage_limit_bytes=standing.storage_limit_bytes,
        storage_left_bytes=standing.storage_left_bytes,
        running_jobs=standing.running_jobs,
        concurrent_jobs=standing.concurrent_jobs,
        max_song_seconds=quota.MAX_SONG_SECONDS,
        candidates=[
            RemovalCandidate(
                song_id=candidate.song_id,
                title=candidate.title,
                bytes=candidate.bytes,
                last_played_at=(
                    candidate.last_played_at.isoformat() if candidate.last_played_at else None
                ),
            )
            for candidate in candidates
        ],
    )

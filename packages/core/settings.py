"""Reading and writing a person's settings for a song.

An upsert rather than a read-modify-write. The player saves on every change, so
two saves can be in flight at once - a fader moved while a key change is still
posting - and a read-modify-write would let the older one win. Postgres decides
instead, and the last statement to arrive is the one that stands.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import UserSongSettings

# Chapter 8's ranges, enforced here as well as in the database and in the player.
# This is the layer that turns a bad value into a corrected one rather than a
# constraint violation, because a settings save must never fail a user's session.
KEY_SHIFT_RANGE = (-6, 6)
TEMPO_RANGE = (0.5, 1.5)
# The lyrics offset (T-2.7). Wider than the ±3s the player offers, because this
# is the backstop and not the control: what it exists to catch is a unit mix-up
# - seconds sent where milliseconds were meant - not a user who nudged too far.
# A song is eight minutes at most, so half a minute is already nonsense.
LYRIC_OFFSET_RANGE = (-30_000, 30_000)
STEM_KINDS = ("vocals", "drums", "bass", "other")


def clamp_key_shift(value: int | None) -> int:
    if value is None:
        return 0
    return max(KEY_SHIFT_RANGE[0], min(KEY_SHIFT_RANGE[1], int(value)))


def clamp_tempo(value: float | None) -> float:
    if value is None:
        return 1.0
    return round(max(TEMPO_RANGE[0], min(TEMPO_RANGE[1], float(value))), 2)


def clamp_lyric_offset(value: int | None) -> int:
    if value is None:
        return 0
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return 0
    return max(LYRIC_OFFSET_RANGE[0], min(LYRIC_OFFSET_RANGE[1], offset))


def clean_volumes(value: dict | None) -> dict[str, float] | None:
    """Keep only the stems we know, clamped to 0..1.

    A stored volume for a stem that no longer exists is harmless; a stored 4.0
    is not, and some future client will apply it without checking.
    """
    if not value:
        return None
    cleaned = {
        kind: max(0.0, min(1.0, float(volume)))
        for kind, volume in value.items()
        if kind in STEM_KINDS and isinstance(volume, int | float)
    }
    return cleaned or None


async def get_settings(
    session: AsyncSession, user_id: uuid.UUID, song_id: uuid.UUID
) -> UserSongSettings | None:
    return await session.get(UserSongSettings, (user_id, song_id))


async def save_settings(
    session: AsyncSession,
    user_id: uuid.UUID,
    song_id: uuid.UUID,
    *,
    key_shift: int | None = None,
    tempo_ratio: float | None = None,
    stem_volumes: dict | None = None,
    lyric_offset_ms: int | None = None,
) -> UserSongSettings:
    values = {
        "user_id": user_id,
        "song_id": song_id,
        "key_shift": clamp_key_shift(key_shift),
        "tempo_ratio": clamp_tempo(tempo_ratio),
        "stem_volumes_json": clean_volumes(stem_volumes),
        "lyric_offset_ms": clamp_lyric_offset(lyric_offset_ms),
    }

    statement = insert(UserSongSettings).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[UserSongSettings.user_id, UserSongSettings.song_id],
        set_={
            key: statement.excluded[key]
            for key in ("key_shift", "tempo_ratio", "stem_volumes_json", "lyric_offset_ms")
        },
    )
    await session.execute(statement)
    await session.commit()

    # Expired so the returned row reflects what the upsert actually stored,
    # including the database's updated_at, rather than the values we sent.
    session.expire_all()
    saved = await get_settings(session, user_id, song_id)
    assert saved is not None  # noqa: S101 - the upsert just wrote it
    return saved

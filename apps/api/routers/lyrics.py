"""Chapter 6's two lyrics endpoints (T-2.1).

    GET  /songs/{id}/lyrics          timed lines; 202 while they are still being made
    PUT  /songs/{id}/lyrics          a manual edit - a new version, never an overwrite
    POST /songs/{id}/lyrics/search   ask the open lyrics database again (T-2.2)

The 202 is the part worth reading twice. D-28 opens the player before the lyrics
exist, so "we do not have them yet" is a normal answer to a normal request, not
an error: a 404 would make the client show a failure for a song that is working
exactly as designed. The client polls, or - once T-2.6 wires it up - listens on
the job's SSE stream and asks again when the lyrics land.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.core.enums import LyricsSource, LyricsStatus
from packages.core.lyrics import (
    MAX_LINES,
    LineDraft,
    LyricsError,
    get_lyrics,
    list_versions,
    save_lyrics,
)
from packages.core.lyrics_lookup import lookup_lyrics
from packages.core.models import LyricLine, Lyrics, Song

from ..deps import CatalogueDep, SessionDep, UserDep
from ..errors import ApiError
from ..ownership import owned_song

router = APIRouter(tags=["lyrics"])


class Word(BaseModel):
    """One word inside a line, when the alignment got that far."""

    text: str
    start_ms: int
    end_ms: int | None = None


class Line(BaseModel):
    """One line, as the player scrolls it."""

    index: int = Field(description="0-based position in the song, which is the order to sing.")
    text: str
    start_ms: int | None = Field(
        default=None, description="Milliseconds from the start of the song. Null when untimed."
    )
    end_ms: int | None = None
    words: list[Word] = Field(
        default_factory=list,
        description="Empty when the alignment is line-level only, which chapter 7 treats as a "
        "normal outcome rather than a failure.",
    )


class LineIn(BaseModel):
    """One line on its way in.

    No `index`: the order of the list is the order of the song. An index sent by
    the client is a second source of truth for the order, and the two disagree
    the first time the editor deletes a line.
    """

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    words: list[Word] = Field(default_factory=list)


class Version(BaseModel):
    """One entry in the history the editor can go back to."""

    version: int
    source: str = Field(description="db | mix_asr | vocals_asr | manual")
    language: str
    is_verified: bool
    created_at: datetime


class SongLyrics(BaseModel):
    """The words of one song, at one version."""

    song_id: uuid.UUID
    version: int
    language: str
    source: str
    is_verified: bool
    status: str = Field(
        description="The song's lyrics_status, derived from these lines: word | line | missing."
    )
    lines: list[Line]
    versions: list[Version] = Field(
        description="Every version of this song's lyrics, newest first. An edit adds to this "
        "list rather than replacing what was there."
    )
    created_at: datetime


class LyricsIn(BaseModel):
    """A manual save from the editor (T-2.8 through T-2.10)."""

    lines: list[LineIn] = Field(
        description=f"The whole song, in order. At most {MAX_LINES} lines; blank ones are "
        "dropped rather than refused, because a paste has them between verses."
    )
    language: str = "he"
    source: LyricsSource = Field(
        default=LyricsSource.MANUAL,
        description="Normally left alone. The pipeline sets mix_asr / vocals_asr / db when it "
        "writes a transcript; a save from the editor is manual.",
    )
    is_verified: bool = Field(
        default=False,
        description="A person saying the words are right. Not a confidence score.",
    )


class LyricsPending(BaseModel):
    """The 202 body: nothing to show yet, and why."""

    song_id: uuid.UUID
    status: str
    detail: str


def _as_line(row: LyricLine) -> Line:
    return Line(
        index=row.index,
        text=row.text,
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        words=[Word(**word) for word in (row.words_json or [])],
    )


def _as_lyrics(song: Song, lyrics: Lyrics, versions: list[Lyrics]) -> SongLyrics:
    return SongLyrics(
        song_id=song.id,
        version=lyrics.version,
        language=lyrics.language,
        source=lyrics.source,
        is_verified=lyrics.is_verified,
        status=song.lyrics_status,
        lines=[_as_line(line) for line in sorted(lyrics.lines, key=lambda line: line.index)],
        versions=[
            Version(
                version=row.version,
                source=row.source,
                language=row.language,
                is_verified=row.is_verified,
                created_at=row.created_at,
            )
            for row in versions
        ],
        created_at=lyrics.created_at,
    )


async def _song_or_404(session: SessionDep, song_id: uuid.UUID, user_id: uuid.UUID) -> Song:
    """Somebody else's song is a 404 here too - see `ownership.py`."""
    return await owned_song(session, song_id, user_id)


@router.get(
    "/songs/{song_id}/lyrics",
    response_model=SongLyrics,
    responses={202: {"model": LyricsPending, "description": "Still being transcribed or aligned"}},
    summary="The timed lyrics of a song",
)
async def read_lyrics(
    session: SessionDep,
    user_id: UserDep,
    song_id: uuid.UUID,
    version: int | None = Query(
        default=None,
        ge=1,
        description="A specific version. Omit for the newest, which is what the player wants.",
    ),
) -> SongLyrics | JSONResponse:
    """Newest version by default; `?version=` for one the editor wants back.

    A song with no lyrics answers 202 while the pipeline is still working and
    200 with an empty list once it has given up. Both are states the user can be
    shown - "coming" and "none, here is the editor" - and neither is an error.
    """
    song = await _song_or_404(session, song_id, user_id)
    found = await get_lyrics(session, song_id, version)

    if found is None:
        if version is not None:
            raise ApiError(
                "lyrics_version_not_found",
                f"song {song_id} has no lyrics version {version}",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if song.lyrics_status == LyricsStatus.PENDING:
            # A Response rather than a model, because the 202 body is a
            # different shape from the 200 one and `response_model` would
            # otherwise validate this against SongLyrics and fail.
            pending = LyricsPending(
                song_id=song.id,
                status=song.lyrics_status,
                detail="the lyrics for this song are still being prepared",
            )
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content=jsonable_encoder(pending),
            )
        # Transcription failed, or nobody has written any: an empty set of
        # lyrics rather than a 404, so the editor opens on it (T-2.10).
        return SongLyrics(
            song_id=song.id,
            version=0,
            language="he",
            source=str(LyricsSource.MANUAL),
            is_verified=False,
            status=song.lyrics_status,
            lines=[],
            versions=[],
            created_at=song.created_at,
        )

    return _as_lyrics(song, found, await list_versions(session, song_id))


@router.put(
    "/songs/{song_id}/lyrics",
    response_model=SongLyrics,
    status_code=status.HTTP_201_CREATED,
    summary="Save an edited set of lyrics as a new version",
)
async def write_lyrics(
    session: SessionDep, user_id: UserDep, song_id: uuid.UUID, body: LyricsIn
) -> SongLyrics:
    """201, not 200: this creates a version, it does not update one.

    Chapter 6 is explicit that an edit never overwrites. That matters most for
    the case phase 2 exists to serve - somebody fixing timings by hand for
    twenty minutes and then wanting the machine's version back.
    """
    song = await _song_or_404(session, song_id, user_id)

    try:
        saved = await save_lyrics(
            session,
            song_id,
            lines=[
                LineDraft(
                    text=line.text,
                    start_ms=line.start_ms,
                    end_ms=line.end_ms,
                    words=[word.model_dump() for word in line.words],
                )
                for line in body.lines
            ],
            source=body.source,
            language=body.language,
            is_verified=body.is_verified,
        )
    except LyricsError as exc:
        raise ApiError(exc.code, exc.message, status_code=status.HTTP_400_BAD_REQUEST) from exc

    await session.refresh(song)
    return _as_lyrics(song, saved, await list_versions(session, song_id))


# Referenced in the OpenAPI description above; kept here so the limit is written
# once and the docs cannot drift from the code that enforces it.
write_lyrics.__doc__ = (write_lyrics.__doc__ or "") + f"\n\nAt most {MAX_LINES} lines."


@router.post(
    "/songs/{song_id}/lyrics/search",
    response_model=SongLyrics,
    status_code=status.HTTP_201_CREATED,
    summary="Look this song up in the open lyrics database",
)
async def search_lyrics(
    session: SessionDep, catalogue: CatalogueDep, user_id: UserDep, song_id: uuid.UUID
) -> SongLyrics:
    """Chapter 6's `reprocess`, for the one stage that costs nothing to re-run.

    The pipeline already does this once (D-08), so this is for the song that was
    processed before the title was fixed, or before the database had it. A match
    lands as a new `db` version, which is why running it after somebody has
    edited by hand is safe: their version is still there, one behind.
    """
    song = await _song_or_404(session, song_id, user_id)

    found = await lookup_lyrics(session, song, catalogue, replace_existing=True)
    if found is None or found.lyrics is None:
        raise ApiError(
            "lyrics_match_not_found",
            f"no timed lyrics found for {song.title!r}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    await session.refresh(song)
    return _as_lyrics(song, found.lyrics, await list_versions(session, song_id))

"""Reading and writing a song's words, one version at a time (T-2.1).

Two rules from chapter 6 live here, and everything else is bookkeeping:

* **An edit creates a version; it never overwrites one.** Phase 2's whole story
  is that automatic Hebrew alignment will not be good enough to leave alone, so
  people will edit, and some of those edits will make things worse. Keeping the
  old rows means "put it back the way the machine had it" is a read.
* **The song's `lyrics_status` is derived from the lines, never passed in.** It
  is what the library and the player branch on, and a caller free to set it
  eventually says `word` about a set of lines with no timings in them at all.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.core.enums import LyricsSource, LyricsStatus
from packages.core.models import LyricLine, Lyrics, Song

# A song is eight minutes at most (chapter 9). A thousand lines in eight minutes
# is two a second, which is not a song but a paste of something else, and the
# player would have to render all of them.
MAX_LINES = 1000
MAX_LINE_CHARS = 500
MAX_WORDS_PER_LINE = 200

# Anything longer than the longest song we accept is a unit mix-up - seconds
# where milliseconds were meant - and letting it through produces lyrics that
# silently never appear.
MAX_MS = 480_000


class LyricsError(Exception):
    """A save the editor should not have been able to send.

    Carries a `code` for the same reason ApiError does: the editor has to say
    *what* is wrong with the lines in Hebrew, and "422" does not.

    Unlike a settings save, which is automatic and is therefore clamped rather
    than refused (see packages/core/settings.py), saving lyrics is a deliberate
    press of a button. Quietly rewriting what a person typed is worse here than
    telling them it did not go in.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LineDraft:
    """A line on its way in, before it is a row.

    The `index` is not here: it is the position in the list, assigned on save.
    An index supplied by the client is a second source of truth for the order,
    and the two disagree the first time the editor deletes a line.
    """

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    words: list[dict] = field(default_factory=list)


def _check_ms(value: int) -> int:
    if value < 0 or value > MAX_MS:
        raise LyricsError(
            "invalid_lyrics", f"a time of {value}ms is outside the song (0..{MAX_MS})"
        )
    return value


def _clean_words(words: list[dict] | None, line_start: int | None) -> list[dict] | None:
    """Word timings, or nothing.

    Words are stored as a blob and read as a blob, so this is the only place
    that ever looks inside one. A word without a start is not a timing, and a
    list where some words have them and some do not cannot drive a highlight, so
    such a line falls back to line-level rather than half-working.
    """
    if not words or line_start is None:
        return None
    if len(words) > MAX_WORDS_PER_LINE:
        raise LyricsError("invalid_lyrics", f"a line may hold at most {MAX_WORDS_PER_LINE} words")

    cleaned: list[dict] = []
    for word in words:
        text = str(word.get("text", "")).strip()
        start = word.get("start_ms")
        if not text or start is None:
            return None
        end = word.get("end_ms")
        cleaned.append(
            {
                "text": text,
                "start_ms": _check_ms(int(start)),
                "end_ms": None if end is None else _check_ms(int(end)),
            }
        )
    return cleaned or None


def clean_lines(drafts: list[LineDraft]) -> list[LineDraft]:
    """Validate a save, and drop what is not a line.

    Blank lines are dropped rather than refused: a paste from a lyrics site
    arrives with empty lines between verses (T-2.10), and they carry no timing
    and nothing to sing. Everything else that is wrong is refused.
    """
    if len(drafts) > MAX_LINES:
        raise LyricsError("too_many_lyric_lines", f"a song may hold at most {MAX_LINES} lines")

    kept: list[LineDraft] = []
    for draft in drafts:
        text = draft.text.strip()
        if not text:
            continue
        if len(text) > MAX_LINE_CHARS:
            raise LyricsError(
                "invalid_lyrics", f"a line may hold at most {MAX_LINE_CHARS} characters"
            )

        start = None if draft.start_ms is None else _check_ms(int(draft.start_ms))
        end = None if draft.end_ms is None else _check_ms(int(draft.end_ms))
        if start is not None and end is not None and end < start:
            raise LyricsError("invalid_lyrics", f"line ends at {end}ms but starts at {start}ms")
        if end is not None and start is None:
            # An end with no start cannot be shown or scrolled to; keeping it
            # would only make the row look better timed than it is.
            end = None

        kept.append(
            LineDraft(
                text=text,
                start_ms=start,
                end_ms=end,
                words=_clean_words(draft.words, start) or [],
            )
        )
    return kept


def status_for(lines: list[LineDraft]) -> LyricsStatus:
    """What the player can do with these lines.

    `MISSING` covers both "there are no words" and "there are words but no
    times", which chapter 7 treats as the same thing from the player's side: the
    lyrics area cannot scroll, and the honest move is to invite the user to the
    editor. `WORD` needs *every* timed line to carry words - a highlight that
    works for the first verse and then stops looks broken, where a line-level
    highlight throughout looks deliberate.
    """
    timed = [line for line in lines if line.start_ms is not None]
    if not timed:
        return LyricsStatus.MISSING
    if all(line.words for line in timed):
        return LyricsStatus.WORD
    return LyricsStatus.LINE


async def get_lyrics(
    session: AsyncSession, song_id: uuid.UUID, version: int | None = None
) -> Lyrics | None:
    """One version with its lines, or the newest when no version is asked for."""
    statement = (
        select(Lyrics)
        .where(Lyrics.song_id == song_id)
        .options(selectinload(Lyrics.lines))
        .order_by(Lyrics.version.desc())
        .limit(1)
    )
    if version is not None:
        statement = statement.where(Lyrics.version == version)
    return (await session.execute(statement)).scalar_one_or_none()


async def list_versions(session: AsyncSession, song_id: uuid.UUID) -> list[Lyrics]:
    """Every version, newest first, without their lines.

    The editor offers "go back to what the machine wrote", and that list has to
    be cheap enough to send alongside the lyrics themselves.
    """
    statement = select(Lyrics).where(Lyrics.song_id == song_id).order_by(Lyrics.version.desc())
    return list((await session.execute(statement)).scalars())


async def _next_version(session: AsyncSession, song_id: uuid.UUID) -> int:
    highest = await session.scalar(
        select(func.max(Lyrics.version)).where(Lyrics.song_id == song_id)
    )
    return int(highest or 0) + 1


async def save_lyrics(
    session: AsyncSession,
    song_id: uuid.UUID,
    *,
    lines: list[LineDraft],
    source: LyricsSource | str,
    language: str = "he",
    is_verified: bool = False,
) -> Lyrics:
    """Write a new version and point the song's `lyrics_status` at it.

    The version number is read and then written, which is a race; the unique
    constraint on (song_id, version) is what makes losing that race harmless,
    and this retries rather than reporting a conflict nobody can act on. Two
    people editing one song is a phase 3 problem, but the pipeline writing a
    transcript while the user saves a paste is a phase 2 one.
    """
    cleaned = clean_lines(lines)
    status = status_for(cleaned)

    for _ in range(3):
        lyrics = Lyrics(
            song_id=song_id,
            language=language,
            source=str(source),
            is_verified=is_verified,
            version=await _next_version(session, song_id),
            lines=[
                LyricLine(
                    index=index,
                    text=line.text,
                    start_ms=line.start_ms,
                    end_ms=line.end_ms,
                    words_json=line.words or None,
                )
                for index, line in enumerate(cleaned)
            ],
        )
        session.add(lyrics)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            continue

        song = await session.get(Song, song_id)
        if song is not None:
            song.lyrics_status = str(status)
        await session.commit()
        saved = await get_lyrics(session, song_id, lyrics.version)
        assert saved is not None  # noqa: S101 - the flush above just wrote it
        return saved

    raise LyricsError("lyrics_save_conflict", "another save landed first; try again")

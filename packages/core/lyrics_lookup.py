"""Asking the open lyrics database whether this song is already timed (T-2.2).

D-08's order is: open database, then transcription, then the editor. This is the
first of the three, and it is the only one that can produce word-perfect timings
for free - somebody has already sat with the song and done it by hand.

The whole module is built to fail quietly. A lookup that finds nothing, a
database that is down, an LRC file that turns out to be empty: all three mean
the same thing to everything above here - no lyrics yet, carry on - because
chapter 7 is explicit that a lyrics failure is not a job failure.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.enums import LyricsSource
from packages.core.lyrics import LineDraft, LyricsError, get_lyrics, save_lyrics
from packages.core.models import Lyrics, Song
from packages.lyrics.lrc import parse_lrc
from packages.lyrics.matching import Match, best_match, readings
from packages.providers.lyrics_catalogue import CatalogueError, LyricsCatalogue

log = logging.getLogger("karuki.lyrics.lookup")

HEBREW = re.compile(r"[֐-׿]")


@dataclass(frozen=True)
class Found:
    """What was matched, for the log and for the screen that offers it."""

    match: Match
    lines: list[LineDraft]
    lyrics: Lyrics | None = None


def language_of(lines: list[LineDraft]) -> str:
    """`he` or `en`, from the text itself.

    Not from the user's locale: a Hebrew speaker's library has English songs in
    it, and T-2.5's aligner needs to be told which language it is looking at.
    """
    text = " ".join(line.text for line in lines[:20])
    return "he" if HEBREW.search(text) else "en"


def search(
    catalogue: LyricsCatalogue,
    title: str,
    artist: str | None = None,
    duration_sec: int | None = None,
) -> Found | None:
    """The blocking half: one HTTP call, then the matching rules.

    Separate from the database half so it can be called from a worker thread,
    and so the matching can be tested without a session.
    """
    candidates = []
    for asked_title, asked_artist in _queries(title, artist):
        candidates.extend(catalogue.search(asked_title, asked_artist))
        if candidates:
            break

    match = best_match(candidates, title, artist, duration_sec)
    if match is None:
        log.info("no lyrics match for %r (%s candidates)", title, len(candidates))
        return None

    lines = parse_lrc(match.candidate.synced_lyrics or "")
    if not lines:
        # The database said it had synchronised lyrics and the file has no timed
        # line in it. Rare, and not worth storing a version for.
        log.info("match for %r had no timed lines", title)
        return None

    return Found(match=match, lines=lines)


def _queries(title: str, artist: str | None) -> list[tuple[str, str | None]]:
    """What to actually ask the database, narrowest first.

    **Every reading is asked twice: with the artist, and without.** That second
    ask is not belt and braces, it is what makes Hebrew work at all. LRCLIB
    stores Hebrew songs under a Hebrew title with a *transliterated* artist -
    `ממעמקים` by `Idan Raichel` - so a search carrying `עידן רייכל` matches
    nothing, while the bare title returns fifteen rows. Measured: it took the
    hit rate on ten well-known Hebrew songs from 1 to 5.

    Stopping at the first reading that returns anything keeps this to one HTTP
    call for the songs that are easy to find.
    """
    asked: list[tuple[str, str | None]] = []
    for want in readings(title, artist):
        for pair in ((want.title, want.artist), (want.title, None)):
            if pair not in asked:
                asked.append(pair)
    if (title, None) not in asked:
        asked.append((title, None))
    return asked


async def lookup_lyrics(
    session: AsyncSession,
    song: Song,
    catalogue: LyricsCatalogue,
    *,
    replace_existing: bool = False,
) -> Found | None:
    """Search, and store a match as a new `db` version. Never raises.

    Returns `None` for every kind of "no": no match, no catalogue, no network,
    no timed lines in the file. The caller's next move is the same in all four
    cases, so distinguishing them here would only be a distinction the caller
    has to ignore.
    """
    if not replace_existing and await get_lyrics(session, song.id) is not None:
        # Something already wrote lyrics for this song - a previous run, or a
        # person. Overwriting a person's work with a database guess is exactly
        # the failure T-2.1's versioning exists to prevent.
        return None

    try:
        # urllib blocks, and this runs inside the job's event loop.
        found = await asyncio.to_thread(
            search, catalogue, song.title, song.artist, song.duration_sec
        )
    except CatalogueError as exc:
        log.info("lyrics catalogue unavailable for song %s: %s", song.id, exc)
        return None
    except Exception:  # noqa: BLE001 - a lyrics lookup must never fail a job
        log.exception("lyrics lookup crashed for song %s", song.id)
        return None

    if found is None:
        return None

    try:
        stored = await save_lyrics(
            session,
            song.id,
            lines=found.lines,
            source=LyricsSource.DB,
            language=language_of(found.lines),
        )
    except LyricsError as exc:
        # A catalogue file that our own rules reject - a line past the end of
        # the song, a thousand lines. Not the user's problem, and not a failure.
        log.info(
            "lyrics from %s rejected for song %s: %s", found.match.candidate.provider, song.id, exc
        )
        return None

    log.info(
        "song %s matched %r by %r (%s, score %.2f, %d lines)",
        song.id,
        found.match.candidate.title,
        found.match.candidate.artist,
        found.match.candidate.provider,
        found.match.score,
        len(found.lines),
    )
    return Found(match=found.match, lines=found.lines, lyrics=stored)

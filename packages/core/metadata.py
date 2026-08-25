"""What a song is called, and who sang it (T-4.2).

Four things can claim to know, and they are not equally good. In order:

1. **What the source said.** An importer that read a page knows the track and
   the artist as fields, not as a string somebody has to take apart.
2. **The file's own tags.** Somebody wrote those down; they are evidence.
3. **The open lyrics database**, once it has *identified* the song. This one
   arrives late - minutes after the song exists - and is the only way a file
   called `ריטה - שביר.mp3` ever learns that the artist is ריטה and the song is
   שביר. It is evidence rather than a guess precisely because the match was
   made on the title, the artist *and* the measured duration (T-2.2).
4. **The file name**, which is the weakest and is only ever a title.

The one thing deliberately *not* done here is splitting a file name in half.
`עוף גוזל - אריק איינשטיין` and `אריק איינשטיין - עוף גוזל` are both common and
nothing in the name says which is which; `packages/lyrics/matching.readings`
exists because T-2.2 decided to ask the database both ways round rather than
guess. Writing a guess onto the row would make that decision twice, in two
places, and the second one would be wrong half the time. So a file name fills
the title and leaves the artist empty - which is honest, and which the user can
fix in one field.

And above all four: **a person who has typed something wins forever.**
`songs.details_edited_at` is what says so, and every automatic write checks it.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from packages.audio.tags import Tags, clean
from packages.core.models import Song
from packages.lyrics.matching import normalise as normalise_name

log = logging.getLogger("karuki.metadata")

# The fallback title, in the language of the app rather than "Untitled".
NO_NAME = "ללא שם"


@dataclass(frozen=True)
class Details:
    """What a new song will be called."""

    title: str
    artist: str | None = None


def title_from_filename(filename: str | None) -> str:
    """The stem of the name, tidied. Hebrew file names are the normal case here,
    so no transliteration and no slugging."""
    if not filename:
        return NO_NAME
    return clean(Path(filename).stem) or NO_NAME


def details_for(
    *,
    filename: str | None,
    tags: Tags | None = None,
    title_hint: str | None = None,
    artist_hint: str | None = None,
) -> Details:
    """The best name available at the moment a song is created.

    The hints come from an importer that read a page and had these as fields.
    They win over the tags because a file downloaded from a page is often
    tagless, or tagged by whoever encoded it years ago; when the hint is only a
    file name in a URL - which is what the `direct` resolver has - it is no
    better than a file name, and the tags still win.
    """
    tags = tags or Tags()
    hinted_title = clean(title_hint)
    hinted_artist = clean(artist_hint)

    # A hint that is really just the address's file name is not evidence over a
    # tag. The importer says so by sending no artist with it.
    hint_is_a_name = hinted_artist is None

    if hinted_title and not hint_is_a_name:
        return Details(title=hinted_title, artist=hinted_artist)

    if tags.title:
        return Details(title=tags.title, artist=tags.artist or hinted_artist)

    return Details(
        title=hinted_title or title_from_filename(filename),
        artist=tags.artist or hinted_artist,
    )


def adopt_from_catalogue(
    song_title: str,
    song_artist: str | None,
    candidate_title: str,
    candidate_artist: str | None,
) -> Details | None:
    """What the open database taught us, or `None` when it taught us nothing.

    Two narrow rules, and both of them are about *only* improving what is
    demonstrably a file name:

    * The artist is filled **when there is none**. Never replaced: a tag was
      written by somebody about this file, and a database row is about a
      recording that merely matched.
    * The title is replaced **only when ours contains the database's** after
      normalising - `ריטה - שביר` contains `שביר`, so the extra words are the
      file name's and the short one is the song. A title that is merely similar
      is left alone, because "similar" is where a wrong match would land.
    """
    title = clean(candidate_title)
    artist = clean(candidate_artist)

    new_title = song_title
    if title and normalise_name(title) and normalise_name(title) != normalise_name(song_title):
        ours = normalise_name(song_title).split()
        theirs = normalise_name(title).split()
        if _contains_in_order(ours, theirs):
            new_title = title

    new_artist = song_artist or artist

    if new_title == song_title and new_artist == song_artist:
        return None
    return Details(title=new_title, artist=new_artist)


class Named(Protocol):
    """Anything that claims to be a title and an artist.

    A protocol rather than the catalogue's `Candidate`, so this module keeps
    knowing nothing about providers - and so the rule above can be tested with
    two strings. Read-only members, because `Candidate` is a frozen dataclass
    and a plain attribute satisfies these while the reverse is not true.
    """

    @property
    def title(self) -> str: ...

    @property
    def artist(self) -> str | None: ...


def name_from_catalogue(song: Song, candidate: Named) -> bool:
    """Take the database's name for this song, where the rules allow it.

    Returns whether anything changed. The caller commits; this deliberately does
    not, because it runs inside a job that is already batching its writes.
    """
    if song.details_edited_at is not None:
        # Somebody has typed here. Nothing automatic writes to this row again -
        # and this one would land minutes after they did it, while they are
        # still looking at the screen.
        return False

    chosen = adopt_from_catalogue(song.title, song.artist, candidate.title, candidate.artist)
    if chosen is None:
        return False

    log.info(
        "song %s renamed from the lyrics database: %r by %r", song.id, chosen.title, chosen.artist
    )
    song.title = chosen.title
    song.artist = chosen.artist
    return True


def _contains_in_order(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) >= len(haystack):
        return False
    return any(haystack[at : at + len(needle)] == needle for at in range(len(haystack)))


def edited_now() -> datetime:
    """The stamp that stops every automatic write from here on."""
    return datetime.now(UTC)

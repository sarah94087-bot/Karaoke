"""What the file itself says it is (T-4.2).

Almost every audio file carries a name and an artist inside it, and until this
task the project threw them away: `normalise` strips metadata on the way to the
wav (deliberately - an upload's tags are the user's, and nothing downstream
wants them), and the title came from the file name instead. For a library of
Hebrew songs that is a poor trade, because a file name is a guess and a tag is
what somebody wrote down.

Read from the *original*, because the normalised wav has had its tags removed on
purpose. One extra `ffprobe`, about 50ms, on a path that is already running
ffmpeg over the whole file.

Nothing here decides what a song is called - that is `packages/core/metadata.py`,
which weighs these against the file name and against what an importer said.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# The same runner `normalize.py` probes with, on purpose rather than a second
# subprocess call written here: it resolves the tool the same way, raises the
# same `ToolMissing`, and decodes output as UTF-8 - which is the whole ball game
# when the tags are Hebrew and the console is cp1255.
from packages.audio.normalize import _run

# Enough for the longest real title; past this it is a description, a URL, or a
# file that has had something else written into its tags.
MAX_LENGTH = 200

# What players write when they have nothing. A tag saying "Unknown Artist" is
# not more information than an empty field, and storing it means a person has to
# delete it before they can type the real one.
EMPTY_VALUES = {
    "",
    "unknown",
    "unknown artist",
    "unknown album",
    "untitled",
    "various artists",
    "n/a",
    "none",
    "-",
    "לא ידוע",
}


# A tag that is a web address is the download site's watermark, not a name.
# Measured on the phase 0 sample rather than imagined: of five real Hebrew mp3s
# on this machine, two carried `albumaty.com` and `newsmusic.blogspot.com` in
# the artist field. Deliberately narrow - the whole value, no spaces, and a
# last segment of two to six letters - so `U.S.A.` and `blink.182` are names.
WEB_ADDRESS = re.compile(r"^(https?://)?(www\.)?[\w-]+(\.[\w-]+)*\.[a-z]{2,6}/?$", re.IGNORECASE)


@dataclass(frozen=True)
class Tags:
    """What the container says. Every field is what somebody typed, so no field
    is trusted for anything but a default the user can change."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.artist or self.album)


def clean(value: str | None) -> str | None:
    """Trim, collapse, refuse the placeholders, and cut the absurd.

    Control characters are removed rather than trimmed: a tag written by a
    careless encoder can carry a stray newline, and a title with a newline in it
    breaks a library row rather than reading as a long title.
    """
    if value is None:
        return None
    text = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in value)
    text = " ".join(text.split())
    if text.casefold() in EMPTY_VALUES:
        return None
    if WEB_ADDRESS.match(text):
        return None
    return text[:MAX_LENGTH] or None


def first_performer(artist: str | None) -> str | None:
    """`אבי לרנר/חדשות המוזיקה להורדה` is two fields in one, and ID3 says so.

    ID3v2.3 separates multiple performers in `TPE1` with a slash, so this is
    reading the format rather than guessing at it - and on the real files here
    the second half is not a performer at all but the site the mp3 came from.
    Two of the five Hebrew mp3s on this machine carry exactly that shape.

    The exemption is the obvious counterexample: `AC/DC` is one band. A segment
    of one or two characters is a name with a slash in it, not a list - which is
    a heuristic, and the reason the field is editable at all.
    """
    if artist is None or "/" not in artist:
        return artist
    parts = [part.strip() for part in artist.split("/")]
    if any(len(part) < 3 for part in parts):
        return artist
    return parts[0] or artist


def read_tags(path: Path) -> Tags:
    """The title, artist and album written into the file, or empty fields.

    Never raises for a file with no tags, an unreadable file, or a container
    ffprobe does not know: this is a default for a field the user can edit, and
    failing an upload over it would be absurd. `probe` in `normalize.py` is
    where a file that is not audio is refused, and it runs first.
    """
    result = _run(
        "ffprobe",
        ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
    )
    if result.returncode != 0:
        return Tags()

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return Tags()

    # Both places, because it depends on the container: mp3 and mp4 write them
    # on the format, while ogg and flac write them on the stream. Format first -
    # a file with both is describing the recording there.
    found: dict[str, str] = {}
    for source in (payload.get("streams") or [])[::-1] + [payload.get("format") or {}]:
        for key, value in (source.get("tags") or {}).items():
            if isinstance(value, str):
                found[key.casefold()] = value

    # Each candidate is cleaned before the next is considered, rather than
    # picking the first non-empty string and cleaning that: a file whose artist
    # is `albumaty.com` and whose album artist is a person should end up with
    # the person, and `or` on the raw values would stop at the website.
    artist = next(
        (
            cleaned
            for cleaned in (
                # `album_artist` as a fallback and not the other way round: on a
                # compilation the album artist is "Various Artists" while the
                # track artist is the person who sang it.
                clean(found.get(key))
                for key in ("artist", "album_artist", "performer")
            )
            if cleaned
        ),
        None,
    )

    return Tags(
        title=clean(found.get("title")),
        artist=first_performer(artist),
        album=clean(found.get("album")),
    )

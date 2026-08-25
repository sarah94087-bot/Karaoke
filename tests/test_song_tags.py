"""T-4.2: reading the title and the artist out of the file itself.

Against real files written by ffmpeg, in three containers, because the thing
that goes wrong here is container-specific and no fake would show it: mp3 and
mp4 write their tags on the *format*, while ogg and flac write them on the
*stream*, and code that reads only one of those works perfectly on half a
library.

Hebrew tags throughout, since that is the library this is for and the encoding
path from ffprobe's JSON through a cp1255 console is exactly where it would
break.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from packages.audio.tags import Tags, read_tags

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg writes the files")


def write(path: Path, **tags: str) -> Path:
    arguments = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=1"]
    for key, value in tags.items():
        arguments += ["-metadata", f"{key}={value}"]
    subprocess.run([*arguments, str(path)], check=True, capture_output=True, encoding="utf-8")
    return path


@pytest.mark.parametrize("suffix", [".mp3", ".m4a", ".ogg", ".flac", ".wav"])
def test_the_tags_come_back_whatever_the_container(tmp_path: Path, suffix: str):
    """mp3 and mp4 keep them on the format, ogg and flac on the stream. Reading
    only one of those is code that works on half a library."""
    path = write(tmp_path / f"song{suffix}", title="שביר", artist="ריטה", album="שביר")

    assert read_tags(path) == Tags(title="שביר", artist="ריטה", album="שביר")


def test_a_file_with_no_tags_is_empty_rather_than_an_error(tmp_path: Path):
    got = read_tags(write(tmp_path / "bare.wav"))

    assert got.is_empty


def test_a_file_that_is_not_audio_does_not_raise(tmp_path: Path):
    """This is a default for a field the user can edit. Failing an upload over
    it would be absurd - and `normalise` has already refused anything that is
    not audio by the time this runs."""
    path = tmp_path / "note.txt"
    path.write_text("not audio at all")

    assert read_tags(path).is_empty


def test_a_missing_file_does_not_raise(tmp_path: Path):
    assert read_tags(tmp_path / "nothing-here.mp3").is_empty


def test_the_track_artist_is_preferred_over_the_album_artist(tmp_path: Path):
    """On a compilation the album artist is "Various Artists" and the track
    artist is the person who actually sang it."""
    path = write(
        tmp_path / "compilation.mp3", title="עוף גוזל", album_artist="אוסף", artist="אריק איינשטיין"
    )

    assert read_tags(path).artist == "אריק איינשטיין"


def test_a_download_site_in_the_artist_field_is_not_an_artist(tmp_path: Path):
    """Measured rather than imagined: of five real Hebrew mp3s on this machine,
    two carried `albumaty.com` and `newsmusic.blogspot.com` as the artist. The
    album artist, if there is a real one, is then what is left."""
    path = write(
        tmp_path / "downloaded.mp3",
        title="תן לי",
        artist="albumaty.com",
        album_artist="יגאל בשן",
    )

    assert read_tags(path).artist == "יגאל בשן"


@pytest.mark.parametrize(
    ("tagged", "expected"),
    [
        # Two of the five real files here carry exactly this: a performer, a
        # slash, and the site the mp3 was downloaded from.
        ("אבי לרנר/חדשות המוזיקה להורדה", "אבי לרנר"),
        ("שי וינר/חדשות המוזיקה להורדה", "שי וינר"),
        # ID3v2.3 says a slash separates performers, so this is reading the
        # format rather than guessing - but AC/DC is one band, and a one or two
        # character segment is what tells them apart.
        ("AC/DC", "AC/DC"),
        ("בני פרידמן, ברוך לוין", "בני פרידמן, ברוך לוין"),
    ],
)
def test_a_slash_in_the_artist_is_a_list_unless_it_is_a_name(
    tmp_path: Path, tagged: str, expected: str
):
    path = write(tmp_path / "slashes.mp3", artist=tagged)

    assert read_tags(path).artist == expected


def test_a_name_that_merely_has_a_dot_in_it_survives(tmp_path: Path):
    """The rule has to be narrow, or it eats names."""
    path = write(tmp_path / "dots.mp3", title="U.S.A.", artist="blink.182")

    assert read_tags(path) == Tags(title="U.S.A.", artist="blink.182")


def test_a_placeholder_tag_reads_as_no_tag(tmp_path: Path):
    path = write(tmp_path / "player.mp3", title="שביר", artist="Unknown Artist")

    got = read_tags(path)

    assert got.title == "שביר"
    assert got.artist is None

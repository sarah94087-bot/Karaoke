"""T-4.2: what a song is called, and who is allowed to say so.

The rules being pinned here are the ones that would be invisible until they went
wrong on somebody's library:

* the order of the four sources, so a tag beats a file name and a person beats
  everything;
* that a file name is **never split** into artist and title, because T-2.2
  already decided that question cannot be answered without asking the database
  (`matching.readings` tries both ways round for exactly this reason);
* that the database's name is only taken where ours is demonstrably a file name
  with extra words in it.
"""

import uuid
from datetime import UTC, datetime

import pytest

from packages.audio.tags import Tags, clean
from packages.core.metadata import (
    NO_NAME,
    Details,
    adopt_from_catalogue,
    details_for,
    name_from_catalogue,
    title_from_filename,
)
from packages.core.models import Song


class Row:
    """A catalogue candidate, reduced to the two fields the rule reads."""

    def __init__(self, title: str, artist: str | None = None):
        self.title = title
        self.artist = artist


def a_song(title: str, artist: str | None = None, edited: datetime | None = None) -> Song:
    return Song(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title=title,
        artist=artist,
        details_edited_at=edited,
    )


# -- cleaning ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  שיר   שלי  ", "שיר שלי"),
        ("line\nbreak", "line break"),
        ("", None),
        ("   ", None),
        ("Unknown Artist", None),
        ("unknown", None),
        ("לא ידוע", None),
        ("-", None),
        (None, None),
    ],
)
def test_placeholders_are_the_same_as_nothing(raw: str | None, expected: str | None):
    """A tag saying "Unknown Artist" is not more information than an empty
    field, and storing it means the user has to delete it before they can type
    the real one."""
    assert clean(raw) == expected


def test_an_absurd_name_is_cut_rather_than_stored_whole():
    assert len(clean("x" * 5000) or "") == 200


# -- where a name comes from -------------------------------------------------


def test_the_file_name_is_the_last_resort():
    assert details_for(filename="עוף גוזל.mp3") == Details(title="עוף גוזל", artist=None)


def test_a_tag_beats_a_file_name():
    """Somebody wrote the tag down. The file name is whatever the download was
    called."""
    got = details_for(filename="01 - track.mp3", tags=Tags(title="שביר", artist="ריטה"))

    assert got == Details(title="שביר", artist="ריטה")


def test_what_the_source_said_beats_the_tags():
    """An importer that read a page had these as *fields*, not as a string
    somebody has to take apart - and a file downloaded from a page is often
    tagged by whoever encoded it years ago."""
    got = details_for(
        filename="video.m4a",
        tags=Tags(title="Track 1", artist="YouTube Audio"),
        title_hint="ממעמקים",
        artist_hint="עידן רייכל",
    )

    assert got == Details(title="ממעמקים", artist="עידן רייכל")


def test_a_hint_that_is_only_a_file_name_does_not_beat_a_tag():
    """The `direct` resolver's "title" is the file name in the address and
    nothing more, and it says so by sending no artist with it. A real tag is
    better evidence than that."""
    got = details_for(
        filename="song.mp3",
        tags=Tags(title="שביר", artist="ריטה"),
        title_hint="song",
    )

    assert got == Details(title="שביר", artist="ריטה")


def test_a_file_name_is_never_split_into_an_artist_and_a_title():
    """The decision T-2.2 made, kept in one place: `עוף גוזל - אריק איינשטיין`
    and `אריק איינשטיין - עוף גוזל` are both common and nothing in the name says
    which is which. `matching.readings` asks the database both ways round; a
    guess written onto the row would be wrong about half the time."""
    got = details_for(filename="ריטה - שביר.mp3")

    assert got.artist is None
    assert got.title == "ריטה - שביר"


def test_a_song_always_has_a_name():
    assert title_from_filename(None) == NO_NAME
    assert title_from_filename("   .mp3") == NO_NAME
    assert details_for(filename=None).title == NO_NAME


def test_an_artist_survives_a_tagless_file():
    got = details_for(filename="a.mp3", tags=Tags(artist="ריטה"))

    assert got == Details(title="a", artist="ריטה")


# -- what the lyrics database is allowed to teach us -------------------------


def test_the_database_name_replaces_a_file_name_that_contains_it():
    """This is the case the whole rule exists for: the match was made on the
    title, the artist *and* the measured duration, so `ריטה - שביר` being a file
    name is not a guess at that point."""
    got = adopt_from_catalogue("ריטה - שביר", None, "שביר", "ריטה")

    assert got == Details(title="שביר", artist="ריטה")


def test_a_merely_similar_title_is_left_alone():
    """`מעמקים` scores 0.923 against `ממעמקים` and is a different word (T-2.2).
    "Similar" is exactly where a wrong match would land, so only containment
    counts."""
    assert adopt_from_catalogue("ממעמקים", "עידן רייכל", "מעמקים", "עידן רייכל") is None


def test_an_artist_we_already_have_is_never_replaced():
    """A tag was written by somebody about this file; a database row is about a
    recording that merely matched it."""
    got = adopt_from_catalogue("שביר", "ריטה כלינסקי", "שביר", "ריטה")

    assert got is None


def test_an_artist_we_do_not_have_is_filled_in():
    got = adopt_from_catalogue("שביר", None, "שביר", "ריטה")

    assert got == Details(title="שביר", artist="ריטה")


def test_nothing_to_learn_is_none_rather_than_an_identical_row():
    assert adopt_from_catalogue("שביר", "ריטה", "שביר", "ריטה") is None


def test_a_song_somebody_has_named_is_never_renamed_by_the_machine():
    """The write-back happens minutes after the song is on the screen, so a
    person can perfectly well have corrected the name in between - and having it
    overwritten a minute later is how somebody stops trusting the field."""
    song = a_song("my favourite version", edited=datetime.now(UTC))

    assert name_from_catalogue(song, Row("שביר", "ריטה")) is False
    assert song.title == "my favourite version"
    assert song.artist is None


def test_a_song_nobody_has_touched_takes_the_database_name():
    song = a_song("ריטה - שביר")

    assert name_from_catalogue(song, Row("שביר", "ריטה")) is True
    assert (song.title, song.artist) == ("שביר", "ריטה")

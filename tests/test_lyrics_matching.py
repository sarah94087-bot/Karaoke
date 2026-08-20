"""Deciding whether a row from the lyrics database is this song (T-2.2).

Wrong lyrics are worse than no lyrics: no lyrics is a missing feature, the wrong
words are the app being confidently wrong at the exact moment the user is
looking at it. So most of these are about refusing.

The titles are the ones this project actually gets - filenames, because T-1.5
has nothing else to name a song by.
"""

import pytest

from packages.lyrics.matching import (
    MAX_DURATION_DRIFT_SEC,
    best_match,
    normalise,
    readings,
    similarity,
)
from packages.providers.lyrics_catalogue import Candidate

LRC = "[00:12.00]שורה\n"


def candidate(
    title="עוף גוזל",
    artist="אריק איינשטיין",
    duration=222.0,
    synced=LRC,
    instrumental=False,
):
    return Candidate(
        title=title,
        artist=artist,
        album=None,
        duration_sec=duration,
        synced_lyrics=synced,
        instrumental=instrumental,
        remote_id="1",
        provider="fake",
    )


def test_a_filename_is_read_as_artist_and_title_both_ways_round():
    """Nothing in the file says which order it is in, so both are tried and the
    database decides."""
    guesses = readings("אריק איינשטיין - עוף גוזל")

    assert ("עוף גוזל", "אריק איינשטיין") in [(want.title, want.artist) for want in guesses]
    assert ("אריק איינשטיין", "עוף גוזל") in [(want.title, want.artist) for want in guesses]


def test_a_known_artist_is_not_second_guessed():
    """Splitting is for filenames. Once an artist is known - from tags, or from
    the user - there is one reading and it is theirs."""
    guesses = readings("עוף גוזל", "אריק איינשטיין")

    assert [(want.title, want.artist) for want in guesses] == [("עוף גוזל", "אריק איינשטיין")]


def test_normalising_strips_what_nobody_sang():
    assert normalise("01 - עוף גוזל (Official Video) [HD]") == "01 עוף גוזל"


def test_niqqud_does_not_make_it_a_different_song():
    assert similarity("שָׁלוֹם", "שלום") == 1.0


def test_a_decorated_title_still_matches():
    assert similarity("עוף גוזל", "עוף גוזל (מתוך המופע)") >= 0.9


def test_the_right_song_is_matched():
    """The acceptance criterion: a well-known song comes back with its words."""
    match = best_match([candidate()], "אריק איינשטיין - עוף גוזל.mp3", duration_sec=222)

    assert match is not None
    assert match.candidate.title == "עוף גוזל"


def test_a_different_song_by_the_same_artist_is_refused():
    match = best_match([candidate(title="אני ואתה")], "אריק איינשטיין - עוף גוזל", duration_sec=222)

    assert match is None


def test_a_recording_of_a_different_length_is_refused():
    """Timed lyrics are only as good as the recording they were timed against;
    a different cut drifts, and drifting lyrics are the failure users notice."""
    drifted = candidate(duration=222 + MAX_DURATION_DRIFT_SEC + 1)

    assert best_match([drifted], "עוף גוזל", "אריק איינשטיין", duration_sec=222) is None


def test_a_length_inside_the_tolerance_is_accepted():
    close = candidate(duration=222 + MAX_DURATION_DRIFT_SEC - 0.5)

    assert best_match([close], "עוף גוזל", "אריק איינשטיין", duration_sec=222) is not None


def test_an_unknown_length_is_not_a_veto():
    """Missing evidence is not evidence against."""
    assert best_match([candidate(duration=None)], "עוף גוזל", "אריק איינשטיין") is not None


def test_lyrics_with_no_timings_are_not_a_match():
    """The task is synchronised lyrics. Plain words are T-2.10's job."""
    assert best_match([candidate(synced=None)], "עוף גוזל", "אריק איינשטיין") is None


def test_an_instrumental_is_not_a_match():
    assert best_match([candidate(instrumental=True)], "עוף גוזל", "אריק איינשטיין") is None


def test_the_best_of_several_candidates_wins():
    match = best_match(
        [candidate(title="עוף גוזל - גרסת כיסוי", artist="מישהו אחר"), candidate()],
        "אריק איינשטיין - עוף גוזל",
        duration_sec=222,
    )

    assert match is not None
    assert match.candidate.artist == "אריק איינשטיין"


def test_a_transliterated_artist_does_not_count_against_the_match():
    """The common case in this database, not an edge one: LRCLIB holds Hebrew
    songs under a Hebrew title with a Latin artist - `ממעמקים` by
    `Idan Raichel`. Comparing those two strings gives about zero, and treating
    that as evidence would refuse the right song."""
    match = best_match(
        [candidate(title="ממעמקים", artist="Idan Raichel", duration=307.0)],
        "עידן רייכל - ממעמקים",
        duration_sec=307,
    )

    assert match is not None


def test_a_title_alone_is_not_an_identity():
    """`שביר` is a Rita song and an Eviatar Banai song. A live search by the
    bare title - which is the search Hebrew needs - offered the wrong one first,
    scoring 1.00 on the name. Without a comparable artist, the measured length
    is what says which recording this is."""
    other_persons_song = candidate(title="שביר", artist="Eviatar Banai", duration=240.0)

    assert best_match([other_persons_song], "ריטה - שביר") is None
    assert best_match([other_persons_song], "ריטה - שביר", duration_sec=240) is not None


def test_a_title_only_match_has_to_be_the_same_title():
    """With no comparable artist the title is the only name-evidence there is,
    so a title that merely resembles this one is not enough - `מעמקים` scores
    0.923 against `ממעמקים` and is a different word."""
    resembling = candidate(title="מעמקים", artist="Idan Raichel", duration=307.0)

    assert best_match([resembling], "עידן רייכל - ממעמקים", duration_sec=307) is None


def test_a_subtitle_does_not_break_a_title_only_match():
    """Whole words, though: the same song with something appended is still the
    song, and databases are full of `(Remastered)`."""
    decorated = candidate(title="ממעמקים (Remastered)", artist="Idan Raichel", duration=307.0)

    assert best_match([decorated], "עידן רייכל - ממעמקים", duration_sec=307) is not None


def test_the_same_title_by_a_different_artist_is_refused_even_at_the_same_length():
    """A live search for `ריטה - שביר` returned `שביר` by `אריק איינשטיין`, and
    the weighted score let it through: a perfect title outvoted an artist at
    0.33. A different artist is not weaker evidence for this song, it is
    evidence for another one."""
    someone_else = candidate(title="שביר", artist="אריק איינשטיין", duration=216.0)

    assert best_match([someone_else], "ריטה - שביר", duration_sec=216) is None


def test_two_artists_with_the_same_song_name_and_length_is_no_match():
    """Guessing between them is right half the time, and the other half is the
    failure this whole file exists to prevent."""
    both = [
        candidate(title="שביר", artist="Rita", duration=216.0),
        candidate(title="שביר", artist="Eviatar Banai", duration=216.0),
    ]

    assert best_match(both, "ריטה - שביר", duration_sec=216) is None


def test_nothing_at_all_is_no_match():
    assert best_match([], "עוף גוזל") is None


@pytest.mark.parametrize(
    "filename",
    [
        "עוף גוזל - אריק איינשטיין.mp3",
        "אריק איינשטיין - עוף גוזל (Official Video)",
        "03 - אריק איינשטיין - עוף גוזל",
        "עוף גוזל",
    ],
)
def test_the_shapes_filenames_actually_take(filename: str):
    """Four ways the same song arrives from a phone, a download and a rip."""
    assert best_match([candidate()], filename, duration_sec=222) is not None

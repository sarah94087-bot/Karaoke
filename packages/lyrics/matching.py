"""Deciding whether a row from the lyrics database is actually this song.

This is the part of T-2.2 that decides whether the feature helps or hurts.
Wrong lyrics on the screen are worse than none: no lyrics is a missing feature,
the wrong words are the app being confidently wrong at the one moment the user
is looking at it. So the gate is deliberately strict, and every rule here exists
to refuse rather than to accept.

Two facts about this project shape it:

* **The title is usually a filename**, because that is all `T-1.5` has to go on -
  `אריק איינשטיין - עוף גוזל (Official Video).mp3`. So the artist is often
  hiding inside the title, and the title is wrapped in things nobody sang.
* **The duration is measured**, not claimed: `T-1.5` normalises the audio and
  reads the length off it. It is the strongest signal available, and a
  synchronised lyric file for a different cut of the same song is wrong in the
  way that matters - it drifts.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from packages.providers.lyrics_catalogue import Candidate

# Timed lyrics are only as good as the recording they were timed against. Three
# seconds is about a fade-out; beyond that it is a different cut, and every line
# in the second half will sit wrong.
MAX_DURATION_DRIFT_SEC = 3.0
# Below this, "it looked close enough" is doing the work. Measured against real
# filenames rather than chosen: see tests/test_lyrics_matching.py.
MIN_SCORE = 0.72
# When the artist cannot be compared - see `comparable_artists` - the title is
# the only name-evidence there is, so it has to be the same title rather than a
# similar one: 0.95 is exactly the point where "equal after normalising" and
# "contains the whole title" get through and character-level resemblance does
# not. `מעמקים` scores 0.923 against `ממעמקים`, and it is a different word.
MIN_TITLE_ONLY_SCORE = 0.95
# A name is worth more than an artist, because the artist is often a guess made
# by splitting a filename in half.
TITLE_WEIGHT = 0.65
# Below this, two comparable artist names are two different people - see
# `artist_disagrees`, which treats that as a veto rather than as a low score.
MIN_ARTIST_SCORE = 0.6

# The things people put in filenames that nobody ever sang.
NOISE = re.compile(
    r"\b(official|video|audio|lyrics?|hd|hq|full|remaster(ed)?|live|version|clip|mp3|"
    r"קליפ|רשמי|מילים|אודיו|הרשמי)\b",
    re.IGNORECASE,
)
BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
LEADING_TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")
SEPARATORS = re.compile(r"\s+[-–—_|]\s+|\s+[-–—]\s*|\s*[-–—]\s+")
PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
# Hebrew points and cantillation: a vocalised title and a bare one are the same
# title, and only one of them will be in the database.
NIQQUD = re.compile(r"[֑-ׇ]")
# Any Hebrew character at all, used to tell two scripts apart.
NIQQUD_OR_HEBREW = re.compile(r"[֐-׿]")


def normalise(text: str) -> str:
    """What two names have to share to count as the same name."""
    folded = unicodedata.normalize("NFKD", text)
    folded = NIQQUD.sub("", folded)
    folded = BRACKETED.sub(" ", folded)
    folded = NOISE.sub(" ", folded)
    folded = PUNCTUATION.sub(" ", folded)
    return " ".join(folded.split()).casefold()


@dataclass(frozen=True)
class Want:
    """One reading of what the song is called."""

    title: str
    artist: str | None = None


def readings(raw_title: str, artist: str | None = None) -> list[Want]:
    """Every sensible way to read a filename, best guess first.

    `עוף גוזל - אריק איינשטיין` and `אריק איינשטיין - עוף גוזל` are both common,
    and nothing in the file says which this is - so both are tried, and the one
    the database agrees with wins. That is a search, not a guess.
    """
    cleaned = LEADING_TRACK_NUMBER.sub("", raw_title.strip())

    if artist:
        return [Want(title=cleaned, artist=artist)]

    parts = [part.strip() for part in SEPARATORS.split(cleaned, maxsplit=1) if part.strip()]
    if len(parts) == 2:
        left, right = parts
        return [Want(title=right, artist=left), Want(title=left, artist=right), Want(title=cleaned)]
    return [Want(title=cleaned)]


def similarity(left: str, right: str) -> float:
    left, right = normalise(left), normalise(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # A title that contains the other whole - "עוף גוזל" inside
    # "עוף גוזל מתוך המופע" - is the same song with decoration on it. Whole
    # words, not characters: `מעמקים` sits inside `ממעמקים` as a substring and
    # is a different word, which is the sort of accident this rule would
    # otherwise turn into a confident match.
    short, long = sorted((left.split(), right.split()), key=len)
    if _contains_in_order(long, short):
        return 0.95
    return SequenceMatcher(None, left, right).ratio()


def _contains_in_order(haystack: list[str], needle: list[str]) -> bool:
    remaining = iter(haystack)
    return all(word in remaining for word in needle)


def comparable_artists(left: str | None, right: str | None) -> bool:
    """Whether two artist names can be compared at all.

    They cannot when they are written in different scripts, and in this database
    that is the common case rather than an edge one: LRCLIB holds Hebrew songs
    under Hebrew titles with **transliterated** artists - `ממעמקים` by
    `Idan Raichel`. Comparing those two strings gives about zero, which would
    read as evidence that it is the wrong song when it is in fact the right one
    written the other way round. Transliterating Hebrew properly is a project of
    its own; saying "this tells us nothing" is both cheaper and honest.
    """
    if not left or not right:
        return False
    return bool(NIQQUD_OR_HEBREW.search(left)) == bool(NIQQUD_OR_HEBREW.search(right))


def title_similarity(candidate_title: str, wanted_title: str) -> float:
    """Like `similarity`, but the extra words may only be on the database's side.

    The direction matters, and getting it wrong was a real false positive. A row
    called `שביר` scored 0.95 against the *whole filename* `ריטה - שביר`,
    because the title is one of its words - which is true of every song whose
    name appears anywhere in the filename, the artist's own name included. The
    database adding `(Remastered)` is decoration; our query having a word left
    over is a query that was never split properly.
    """
    left, right = normalise(candidate_title), normalise(wanted_title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if _contains_in_order(left.split(), right.split()):
        return 0.95
    return SequenceMatcher(None, left, right).ratio()


def score(candidate: Candidate, want: Want) -> float:
    """How much this row looks like what was asked for, 0..1."""
    title = title_similarity(candidate.title, want.title)
    if not comparable_artists(candidate.artist, want.artist):
        return title
    artist = similarity(candidate.artist or "", want.artist or "")
    return TITLE_WEIGHT * title + (1 - TITLE_WEIGHT) * artist


def floor_for(candidate: Candidate, want: Want) -> float:
    """How good that score has to be before it counts as the song."""
    if comparable_artists(candidate.artist, want.artist):
        return MIN_SCORE
    return MIN_TITLE_ONLY_SCORE


def durations_agree(candidate: Candidate, duration_sec: int | None) -> bool:
    """Both lengths known, and the same recording."""
    if duration_sec is None or candidate.duration_sec is None:
        return False
    return abs(float(candidate.duration_sec) - duration_sec) <= MAX_DURATION_DRIFT_SEC


def duration_fits(candidate: Candidate, duration_sec: int | None) -> bool:
    """An unknown length on either side is not evidence, so it is not a veto."""
    if duration_sec is None or candidate.duration_sec is None:
        return True
    return durations_agree(candidate, duration_sec)


def artist_disagrees(candidate: Candidate, want: Want) -> bool:
    """Two comparable artist names that are simply not the same person.

    A weighted score is the wrong instrument for this. `שביר` by
    `אריק איינשטיין` scored 0.77 against a search for `ריטה - שביר` - a perfect
    title carried it over the bar while the artist, at 0.33, was outvoted. But a
    different artist is not weaker evidence for the same song, it is evidence
    for a different song, and a live search returned exactly that row.

    The 0.6 is above what unrelated Hebrew names score on characters alone
    (`אריק איינשטיין` against `ריטה` is 0.33) and below what a real name scores
    against itself with a band or a spelling attached.
    """
    if not comparable_artists(candidate.artist, want.artist):
        return False
    return similarity(candidate.artist or "", want.artist or "") < MIN_ARTIST_SCORE


def identifiable(candidate: Candidate, want: Want, duration_sec: int | None) -> bool:
    """Is there enough here to say *which* song this is, not just its name?

    A title on its own is not an identity. `שביר` is a Rita song and an Eviatar
    Banai song, and a live search found exactly that: searching by the bare
    title - which is the search Hebrew needs, see `comparable_artists` - offered
    the wrong one first, scoring 1.00 on the name.

    So a match made without a comparable artist has to be backed by the
    measured length. That costs nothing where it matters: the pipeline always
    knows the duration, because T-1.5 reads it off the normalised audio.
    """
    return comparable_artists(candidate.artist, want.artist) or durations_agree(
        candidate, duration_sec
    )


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    score: float
    want: Want
    # The bar this particular comparison had to clear. It is not the same for
    # every row: a match made on the title alone has to be nearly exact.
    floor: float = MIN_SCORE

    @property
    def is_good_enough(self) -> bool:
        return self.score >= self.floor


def best_match(
    candidates: list[Candidate],
    raw_title: str,
    artist: str | None = None,
    duration_sec: int | None = None,
) -> Match | None:
    """The one row worth showing a user, or nothing at all.

    Returning nothing is a perfectly good outcome: the song goes on to be
    transcribed (T-2.3), which is what the pipeline was going to do anyway.
    """
    wants = readings(raw_title, artist)

    scored = [
        Match(
            candidate=candidate,
            score=score(candidate, want),
            want=want,
            floor=floor_for(candidate, want),
        )
        for candidate in candidates
        if candidate.is_usable and duration_fits(candidate, duration_sec)
        for want in wants
        if identifiable(candidate, want, duration_sec) and not artist_disagrees(candidate, want)
    ]
    good = [match for match in scored if match.is_good_enough]
    if not good:
        return None

    # Margin over the bar, not raw score: a title-only match at 0.95 cleared a
    # much higher bar than a title-and-artist match at 0.95, and preferring the
    # raw number would systematically pick the weaker evidence.
    winner = max(good, key=lambda match: (match.score - match.floor, match.score))

    if winner.floor == MIN_TITLE_ONLY_SCORE and _ambiguous(good, winner):
        # Two different artists, the same title, the same length, and nothing
        # here can tell them apart. Guessing gets it right half the time, and
        # the half it gets wrong is the failure this whole file is about.
        return None
    return winner


def _ambiguous(good: list["Match"], winner: "Match") -> bool:
    artists = {
        normalise(match.candidate.artist or "")
        for match in good
        if match.floor == MIN_TITLE_ONLY_SCORE
    }
    return len(artists - {normalise(winner.candidate.artist or "")}) > 0

"""The rules about lines, tested without a database.

`clean_lines` and `status_for` are pure on purpose: what counts as a line, and
what the player can do with a set of them, are decisions worth being able to
check in a millisecond rather than behind an upload and a migration.
"""

import pytest

from packages.core.enums import LyricsStatus
from packages.core.lyrics import (
    MAX_LINE_CHARS,
    MAX_LINES,
    MAX_MS,
    LineDraft,
    LyricsError,
    clean_lines,
    status_for,
)


def line(text: str = "שורה", start: int | None = None, end: int | None = None, words=None):
    return LineDraft(text=text, start_ms=start, end_ms=end, words=words or [])


def test_blank_lines_are_dropped_not_refused():
    """A paste from a lyrics site has empty lines between verses (T-2.10).
    They carry no timing and nothing to sing."""
    kept = clean_lines([line("בית ראשון"), line("   "), line(""), line("בית שני")])

    assert [draft.text for draft in kept] == ["בית ראשון", "בית שני"]


def test_text_is_stripped():
    assert clean_lines([line("  שורה עם רווחים  ")])[0].text == "שורה עם רווחים"


def test_a_line_that_ends_before_it_starts_is_refused():
    """The editor can never produce this legitimately, so accepting it would
    only hide a bug in whatever did."""
    with pytest.raises(LyricsError) as raised:
        clean_lines([line(start=5_000, end=4_000)])

    assert raised.value.code == "invalid_lyrics"


def test_a_time_past_the_longest_song_is_refused():
    """Seconds where milliseconds were meant. Stored, it would produce lyrics
    that silently never appear."""
    with pytest.raises(LyricsError):
        clean_lines([line(start=MAX_MS + 1)])


def test_a_negative_time_is_refused():
    with pytest.raises(LyricsError):
        clean_lines([line(start=-1)])


def test_an_end_without_a_start_is_dropped():
    """It cannot be shown or scrolled to; keeping it would make the row look
    better timed than it is."""
    assert clean_lines([line(end=4_000)])[0].end_ms is None


def test_too_many_lines_is_its_own_code():
    """A different screen from "that timing is wrong": nothing about the lines
    needs fixing, there are just too many of them."""
    with pytest.raises(LyricsError) as raised:
        clean_lines([line(f"שורה {n}") for n in range(MAX_LINES + 1)])

    assert raised.value.code == "too_many_lyric_lines"


def test_a_line_longer_than_a_line_is_refused():
    with pytest.raises(LyricsError):
        clean_lines([line("א" * (MAX_LINE_CHARS + 1))])


def test_words_are_kept_when_every_one_of_them_is_timed():
    kept = clean_lines(
        [
            line(
                "שתי מילים",
                start=1_000,
                words=[
                    {"text": "שתי", "start_ms": 1_000, "end_ms": 1_400},
                    {"text": "מילים", "start_ms": 1_400},
                ],
            )
        ]
    )

    assert [word["text"] for word in kept[0].words] == ["שתי", "מילים"]
    assert kept[0].words[1]["end_ms"] is None


def test_a_line_with_one_untimed_word_falls_back_to_line_level():
    """A highlight that works for half a line looks broken; a line-level
    highlight throughout looks deliberate."""
    kept = clean_lines(
        [
            line(
                "שתי מילים",
                start=1_000,
                words=[{"text": "שתי", "start_ms": 1_000}, {"text": "מילים"}],
            )
        ]
    )

    assert kept[0].words == []


def test_words_on_an_untimed_line_are_dropped():
    kept = clean_lines([line("שורה", words=[{"text": "שורה", "start_ms": 1_000}])])

    assert kept[0].words == []


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], LyricsStatus.MISSING),
        ([line("שורה")], LyricsStatus.MISSING),
        ([line("שורה", start=0)], LyricsStatus.LINE),
        ([line("שורה", start=0), line("שנייה")], LyricsStatus.LINE),
        (
            [line("שורה", start=0, words=[{"text": "שורה", "start_ms": 0}])],
            LyricsStatus.WORD,
        ),
        (
            [
                line("שורה", start=0, words=[{"text": "שורה", "start_ms": 0}]),
                line("שנייה", start=1_000),
            ],
            LyricsStatus.LINE,
        ),
    ],
)
def test_the_status_describes_what_the_player_can_do(lines, expected):
    """Words with no times are `missing`, not `line`: the lyrics area cannot
    scroll, which from the player's side is the same as having none."""
    assert status_for(clean_lines(lines)) == expected

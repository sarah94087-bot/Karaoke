"""T-2.5: a time for every line, and words only where they can be trusted.

The rules under test all come from phase 0 measurements, and the ones worth
reading twice are the negatives: no timestamp is ever moved (`T-0.5.3` measured
the alternative and it was worse than doing nothing), and a word-level highlight
is refused unless the numbers support it (`T-0.5.2`: "a word-level highlight as
a default will look broken").
"""

from packages.audio.silence import Silence
from packages.lyrics.align import (
    MAX_LINE_MS,
    MAX_LINE_WORDS,
    MIN_WORD_CONFIDENCE,
    align,
    align_segment,
    words_are_trustworthy,
)
from packages.providers.transcription import Segment, Word


def words(*pairs: tuple[str, int, int]) -> list[Word]:
    return [Word(text=text, start_ms=start, end_ms=end) for text, start, end in pairs]


def segment(text="שורה", start=1_000, end=5_000, logprob=-0.25, word_list=()):
    return Segment(
        text=text,
        start_ms=start,
        end_ms=end,
        words=list(word_list),
        avg_logprob=logprob,
        no_speech_prob=0.1,
    )


def test_the_times_are_the_models_own():
    """`T-0.5.3` tapped a song by hand: raw Whisper was 242ms off at the median,
    the energetic aligner 372ms and never inside 100ms. So nothing here moves a
    timestamp - the alignment splits, it does not re-anchor."""
    spoken = words(("שורה", 1_000, 2_000), ("שנייה", 2_000, 3_000))

    lines = align([segment(start=1_000, end=3_000, word_list=spoken)])

    assert lines[0].start_ms == 1_000
    assert lines[0].end_ms == 3_000


def test_a_long_segment_is_split_at_a_silence():
    """`T-0.5.1`: a Whisper segment is not a karaoke line - one ran 14.86s to
    26.46s across four sung phrases. The break comes from the audio."""
    spoken = words(
        ("אחת", 1_000, 2_000),
        ("שתיים", 2_000, 3_000),
        ("שלוש", 6_000, 7_000),
        ("ארבע", 7_000, 8_000),
    )
    gap = Silence(start_ms=3_200, end_ms=5_800)

    lines = align_segment(segment(start=1_000, end=8_000, word_list=spoken), [gap])

    assert [line.text for line in lines] == ["אחת שתיים", "שלוש ארבע"]
    assert [line.start_ms for line in lines] == [1_000, 6_000]


def test_the_silence_decides_where_and_the_words_decide_when():
    """The break lands in the silence; the new line still starts at the word's
    own timestamp, not at the end of the gap."""
    spoken = words(("אחת", 1_000, 2_000), ("שתיים", 6_000, 7_000))

    lines = align_segment(
        segment(start=1_000, end=7_000, word_list=spoken), [Silence(3_000, 5_000)]
    )

    assert lines[1].start_ms == 6_000, "the line starts when the word does"


def test_a_silence_outside_the_segment_is_not_a_break():
    spoken = words(("אחת", 1_000, 2_000), ("שתיים", 2_000, 3_000))

    lines = align_segment(
        segment(start=1_000, end=3_000, word_list=spoken), [Silence(20_000, 25_000)]
    )

    assert len(lines) == 1


def test_a_segment_too_long_to_show_is_split_even_without_a_silence():
    """The measured transcripts have segments of 14.0s, 18.1s and 18.9s, and the
    silence detector finds nothing inside most of them: a continuously sung
    vocal has very little real silence in it. A line nobody can follow is worse
    than a break in an odd place, so length is the backstop."""
    spoken = words(*[(f"מילה{n}", n * 1_500, (n + 1) * 1_500) for n in range(12)])

    lines = align_segment(segment(start=0, end=18_000, word_list=spoken), [])

    assert len(lines) > 1
    for line in lines:
        assert (line.end_ms or 0) - (line.start_ms or 0) <= MAX_LINE_MS
        assert len(line.text.split()) <= MAX_LINE_WORDS


def test_a_line_is_not_stretched_to_the_end_of_its_segment():
    """A real case from the measurements: a segment whose single word is sung at
    0.0s and whose end is 12.1s, because Whisper ran it into the instrumental.
    A twelve-second highlight sitting over silence is not a measurement."""
    lines = align_segment(segment(start=0, end=12_100, word_list=words(("תחת", 0, 1_400))), [])

    assert lines[0].end_ms == 1_400


def test_a_line_nobody_could_have_sung_that_long_has_no_end_at_all():
    """One measured case is a single word the model gave a 15.1s duration. It
    cannot be split - it is one word - and pretending the end is a measurement
    would hold the highlight over fifteen seconds of nothing. T-2.1 made
    `end_ms` nullable so "we do not know" can be said out loud."""
    line = align_segment(segment(start=0, end=15_100, word_list=words(("מילה", 0, 15_100))), [])[0]

    assert line.start_ms == 0
    assert line.end_ms is None


def test_a_segment_with_no_word_timings_stays_one_line():
    """Splitting text with no times to place it by would be inventing timings,
    which is the one thing this refuses to do."""
    lines = align_segment(segment(text="שורה ארוכה מאוד", start=0, end=18_000), [])

    assert [line.text for line in lines] == ["שורה ארוכה מאוד"]
    assert lines[0].words == []


def test_word_timings_are_kept_when_the_model_was_confident():
    trusted = segment(
        start=1_000,
        end=3_000,
        logprob=-0.25,  # about 0.78 confidence
        word_list=words(("אחת", 1_000, 2_000), ("שתיים", 2_000, 3_000)),
    )

    assert words_are_trustworthy(trusted)
    assert align_segment(trusted, [])[0].words != []


def test_word_timings_are_dropped_below_phase_zeros_usable_threshold():
    """`T-0.4.2` called a word usable at confidence >= 0.5 and built its whole
    comparison on it. This is the same threshold, not a new one."""
    unsure = segment(
        start=1_000,
        end=3_000,
        logprob=-1.5,  # about 0.22
        word_list=words(("אחת", 1_000, 2_000), ("שתיים", 2_000, 3_000)),
    )

    assert MIN_WORD_CONFIDENCE == 0.5
    assert not words_are_trustworthy(unsure)
    line = align_segment(unsure, [])[0]
    assert line.words == [], "the line keeps its text and its line-level timing"
    assert line.text == "אחת שתיים"


def test_word_timings_that_cover_almost_none_of_the_line_are_dropped():
    """A highlight that reaches the end of the line and waits there for six
    seconds is worse than no highlight."""
    sparse = segment(
        start=0,
        end=10_000,
        word_list=words(("אחת", 0, 300), ("שתיים", 300, 600)),
    )

    assert not words_are_trustworthy(sparse)


def test_words_out_of_order_are_dropped():
    """A highlight that jumps backwards."""
    scrambled = segment(
        start=0,
        end=4_000,
        word_list=words(("שתיים", 2_000, 4_000), ("אחת", 0, 2_000)),
    )

    assert not words_are_trustworthy(scrambled)


def test_a_segment_with_no_confidence_number_is_judged_on_its_timings_alone():
    """A service that does not report `avg_logprob` is not evidence against the
    words it did report."""
    plain = Segment(
        text="אחת שתיים",
        start_ms=0,
        end_ms=2_000,
        words=words(("אחת", 0, 1_000), ("שתיים", 1_000, 2_000)),
    )

    assert words_are_trustworthy(plain)


def test_lines_come_out_in_time_order():
    late = segment(start=30_000, end=32_000, word_list=words(("מאוחר", 30_000, 32_000)))
    early = segment(start=1_000, end=2_000, word_list=words(("מוקדם", 1_000, 2_000)))

    lines = align([late, early], [])

    assert [line.text for line in lines] == ["מוקדם", "מאוחר"]

"""What phase 0 found the model doing on music, and what we do about it.

Two measured failures (`docs/phase0/quotas.md`), both from a real Hebrew song:
`תודה רבה` written over a fifteen-second instrumental intro, and four exactly
duplicated segments. Every rule here deletes; none corrects. A line that is
missing is obvious to a singer and easy to type back, where a line that quietly
moved to the wrong place is not.
"""

from packages.lyrics.transcript import (
    MAX_CONSECUTIVE_REPEATS,
    clean_segments,
    is_caption_filler,
    lines_from,
    to_lines,
)
from packages.providers.transcription import Segment, Transcript, Word


def segment(text="שורה", start=1_000, end=4_000, no_speech=0.05, logprob=-0.2, words=()):
    return Segment(
        text=text,
        start_ms=start,
        end_ms=end,
        words=list(words),
        no_speech_prob=no_speech,
        avg_logprob=logprob,
    )


def test_the_hallucinated_intro_is_dropped():
    """Phase 0's exact case: `תודה רבה` at 0.0s, over an instrumental intro,
    on a song whose singing starts at 15.3s."""
    kept = clean_segments([segment("תודה רבה", start=0, end=15_300), segment("שורה אמיתית")])

    assert [line.text for line in kept] == ["שורה אמיתית"]


def test_filler_inside_a_real_line_is_not_filler():
    """Somebody has written a song with `תודה רבה` in it, and deleting their
    line would be the app being wrong about the words it was asked to show."""
    assert not is_caption_filler("תודה רבה על הכל")
    assert is_caption_filler("תודה רבה!")


def test_a_segment_the_model_says_was_not_speech_is_dropped():
    silence = segment("משהו", no_speech=0.9, logprob=-1.5)

    assert clean_segments([silence]) == []


def test_one_bad_number_is_not_enough_to_drop_a_line():
    """Both of Whisper's own thresholds, not either: a quiet or heavily produced
    vocal legitimately scores badly on one of them, and Hebrew scores badly on
    `avg_logprob` a lot."""
    quiet = segment("שורה חלשה", no_speech=0.9, logprob=-0.4)
    hard = segment("שורה קשה", no_speech=0.1, logprob=-1.8)

    assert len(clean_segments([quiet, hard])) == 2


def test_the_real_numbers_from_a_real_song():
    """The measurement that settles the threshold, kept as a test.

    Running the phase 0 song through the real service produced these four
    segments. Sung Hebrew scores *higher* on `no_speech_prob` than the
    hallucination does - 0.82 against 0.69 - so a rule that dropped on that
    number alone would delete the two real lines and keep the two false ones,
    which is exactly backwards.
    """
    measured = [
        segment("תודה רבה.", start=0, no_speech=0.69, logprob=-0.21),
        segment("דרושנה דורשיך", start=30_000, no_speech=0.55, logprob=-0.27),
        segment("לשמוע אל הרינה ואל התפילה", start=86_400, no_speech=0.82, logprob=-0.27),
        segment("תודה.", start=130_400, no_speech=0.76, logprob=-0.11),
    ]

    kept = clean_segments(measured)

    assert [line.text for line in kept] == ["דרושנה דורשיך", "לשמוע אל הרינה ואל התפילה"]


def test_a_segment_with_no_confidence_numbers_is_kept():
    """A service that does not report them is not evidence that the line is
    bad."""
    plain = Segment(text="שורה", start_ms=0, end_ms=1_000)

    assert clean_segments([plain]) == [plain]


def test_a_run_of_identical_segments_is_cut_short():
    """Phase 0 saw four. Two in a row can be a real chorus; four is the model
    stuck in a loop."""
    stutter = [segment("אותה שורה", start=n * 1_000) for n in range(4)]

    kept = clean_segments(stutter)

    assert len(kept) == MAX_CONSECUTIVE_REPEATS


def test_a_line_that_comes_back_later_is_not_a_repeat():
    """A chorus is a song working as intended."""
    kept = clean_segments(
        [
            segment("פזמון", start=10_000),
            segment("בית", start=20_000),
            segment("פזמון", start=30_000),
        ]
    )

    assert len(kept) == 3


def test_segments_become_lines_with_their_words():
    lines = to_lines(
        [
            segment(
                "שתי מילים",
                start=1_000,
                end=2_000,
                words=[Word("שתי", 1_000, 1_500), Word("מילים", 1_500, 2_000)],
            )
        ]
    )

    assert lines[0].text == "שתי מילים"
    assert lines[0].start_ms == 1_000
    assert [word["text"] for word in lines[0].words] == ["שתי", "מילים"]


def test_the_whole_journey_from_a_transcript_to_lines():
    transcript = Transcript(
        segments=[
            segment("תודה רבה", start=0, end=15_000),
            segment("שורה ראשונה", start=15_300, end=19_100),
            segment("שורה ראשונה", start=19_100, end=23_000),
            segment("שורה ראשונה", start=23_000, end=27_000),
        ],
        text="",
        language="hebrew",
        duration_sec=133.2,
        model="whisper-large-v3",
        backend="test",
        elapsed_sec=9.4,
    )

    lines = lines_from(transcript)

    assert [line.text for line in lines] == ["שורה ראשונה", "שורה ראשונה"]
    assert lines[0].start_ms == 15_300

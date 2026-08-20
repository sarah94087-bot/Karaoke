"""Turning a transcript into karaoke lines: times for every line, words where
the timing can be trusted (T-2.5, D-09).

Everything here is shaped by three phase 0 measurements, and two of them say
"do less than you were about to".

**1. Whisper's own times are the best start times available.** `T-0.5.3` tapped
a song by hand and compared: raw Whisper had a 242ms median error against the
human, while the energetic aligner built for the job had 372ms and never once
landed inside 100ms. So no timestamp here is invented or moved. The alignment
does not re-anchor anything.

**2. A Whisper segment is not a line.** `T-0.5.1` found single segments running
14.86s to 26.46s across four sung phrases, and the transcripts measured for this
task agree: segments of 14.0s, 18.1s and 18.9s in three songs. Nobody can follow
an eighteen-second line. So segments get split - at silences in the vocals stem
where there are any, which is the one job `T-0.5.3` left the detector, and by
length where there are not.

**3. Word-level timing is relatively right and absolutely wrong.** `T-0.5.2`
measured the word durations as genuinely derived from the audio - a coefficient
of variation of 0.34-0.50, `יש` at 160ms against `חברים` at 1400ms in one
segment - but the first word of a phrase off by a median 215-395ms, varying line
to line. Its recommendation is D-09's: line-level by default, word-level only
where it is good enough. That is what `words_are_trustworthy` decides, and it
uses phase 0's own usable-word threshold to decide it.
"""

import logging
import math

from packages.audio.silence import Silence
from packages.core.lyrics import LineDraft
from packages.providers.transcription import Segment, Word

log = logging.getLogger("karuki.lyrics.align")

# Longer than this, nobody can follow the line - it is off the screen and the
# highlight sits on it for ten seconds. The measured segments run to 18.9s.
MAX_LINE_MS = 8_000
# And a line that is short in time can still be a paragraph. Karaoke lines in
# the measured transcripts average 4.3-5.7 words.
MAX_LINE_WORDS = 10
# A break has to leave something on both sides worth calling a line.
MIN_LINE_MS = 700

# `T-0.4.2` called a word usable at confidence >= 0.5 and built its whole
# comparison on it. `avg_logprob` is the log of that, so this is the same
# threshold rather than a new one invented here.
MIN_WORD_CONFIDENCE = 0.5
# Words have to account for most of the line they are in. A line whose words
# cover a third of it is a line the model mostly did not hear, and a highlight
# that jumps to the end and waits is worse than no highlight.
MIN_WORD_COVERAGE = 0.6


def confidence_of(segment: Segment) -> float | None:
    """`avg_logprob` as the probability phase 0 measured usability in."""
    if segment.avg_logprob is None:
        return None
    return math.exp(segment.avg_logprob)


def words_are_trustworthy(segment: Segment) -> bool:
    """Whether this segment's word timings are worth showing a highlight from.

    Three conditions, and a line that fails any of them keeps its text and its
    line-level timing. D-09 is "line, and word where possible"; phase 0 is
    blunter - a word-level highlight as a default "will look broken".
    """
    words = segment.words
    if not words:
        return False

    confidence = confidence_of(segment)
    if confidence is not None and confidence < MIN_WORD_CONFIDENCE:
        return False

    # In order, and inside the line. Out-of-order words are a highlight that
    # jumps backwards.
    starts = [word.start_ms for word in words]
    if starts != sorted(starts):
        return False

    span = max(1, segment.end_ms - segment.start_ms)
    covered = sum(max(0, (word.end_ms or word.start_ms) - word.start_ms) for word in words)
    return covered / span >= MIN_WORD_COVERAGE


def _as_line(
    words: list[Word], text: str, start_ms: int, end_ms: int, keep_words: bool
) -> LineDraft:
    # An end that implies a line longer than anyone sings is not a measurement -
    # one real case is a single word the model gave a 15.1s duration - and
    # T-2.1 made `end_ms` nullable exactly so "we do not know" can be said out
    # loud. The player then shows the line until the next one starts, which is
    # true, instead of holding a highlight over fifteen seconds of nothing.
    unknown_end = end_ms - start_ms > MAX_LINE_MS
    return LineDraft(
        text=text,
        start_ms=start_ms,
        end_ms=None if unknown_end else max(end_ms, start_ms),
        words=(
            [
                {"text": word.text, "start_ms": word.start_ms, "end_ms": word.end_ms}
                for word in words
            ]
            if keep_words and words
            else []
        ),
    )


def _break_points(segment: Segment, gaps: list[Silence]) -> list[int]:
    """Times inside this segment where the singing stopped for long enough.

    The silence is used to decide *where* a line ends, never *when* the next one
    starts - that stays the next word's own timestamp. This is the split that
    `T-0.5.1` asked the audio for and the one use `T-0.5.3` left the detector.
    """
    return [
        gap.middle_ms
        for gap in gaps
        if segment.start_ms + MIN_LINE_MS < gap.middle_ms < segment.end_ms - MIN_LINE_MS
    ]


def _split_at(words: list[Word], points: list[int]) -> list[list[Word]]:
    groups: list[list[Word]] = [[]]
    remaining = sorted(points)
    for word in words:
        while remaining and word.start_ms >= remaining[0]:
            remaining.pop(0)
            if groups[-1]:
                groups.append([])
        groups[-1].append(word)
    return [group for group in groups if group]


def _split_long(group: list[Word]) -> list[list[Word]]:
    """Last resort for a line that is still too long, or too many words.

    The break lands on a word boundary and both new lines keep measured
    timestamps - what is being guessed is *where the line breaks*, not when it
    is sung. A break in the wrong place is a line reading oddly; a wrong time is
    a line arriving at the wrong moment, which is the failure that matters.
    """
    if len(group) < 2:
        return [group]

    span = (group[-1].end_ms or group[-1].start_ms) - group[0].start_ms
    if span <= MAX_LINE_MS and len(group) <= MAX_LINE_WORDS:
        return [group]

    # The longest pause between two words, if there is one; the middle if the
    # model timed the words back to back, which is what it usually does.
    gaps = [
        (word.start_ms - (previous.end_ms or previous.start_ms), index)
        for index, (previous, word) in enumerate(zip(group, group[1:], strict=False), start=1)
    ]
    longest, at = max(gaps)
    if longest <= 0:
        at = len(group) // 2

    return _split_long(group[:at]) + _split_long(group[at:])


def align_segment(segment: Segment, gaps: list[Silence]) -> list[LineDraft]:
    """One transcript segment as one or more karaoke lines."""
    keep_words = words_are_trustworthy(segment)

    if not segment.words:
        # Nothing to place the text by, so it stays one line however long it is.
        # Splitting the text without times would be inventing timings, which is
        # the one thing this module refuses to do.
        return [_as_line([], segment.text, segment.start_ms, segment.end_ms, False)]

    groups = _split_at(segment.words, _break_points(segment, gaps))
    lines: list[LineDraft] = []
    for group in groups:
        for part in _split_long(group):
            text = " ".join(word.text for word in part).strip()
            if not text:
                continue
            lines.append(
                _as_line(
                    part,
                    text,
                    part[0].start_ms,
                    part[-1].end_ms or part[-1].start_ms,
                    keep_words,
                )
            )

    # Deliberately *not* stretched to the segment's end. A segment often runs on
    # into an instrumental tail - one measured song has a segment whose single
    # word is sung at 0.0s and whose end is 12.1s - and a line that claims to
    # last twelve seconds is a highlight that sits there while nothing is sung.
    # The last word's end is the last thing the model actually measured.
    return lines or [_as_line([], segment.text, segment.start_ms, segment.end_ms, False)]


def align(segments: list[Segment], gaps: list[Silence] | None = None) -> list[LineDraft]:
    """Every segment, split into lines, in time order."""
    gaps = gaps or []
    lines = [line for segment in segments for line in align_segment(segment, gaps)]
    lines.sort(key=lambda line: line.start_ms or 0)
    log.info("aligned %d segments into %d lines", len(segments), len(lines))
    return lines

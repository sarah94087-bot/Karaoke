"""Turning a raw transcript into lines worth showing (T-2.3).

Phase 0 transcribed a real Hebrew song on both local Whisper and Groq, and the
finding that matters here is not about speed:

* Groq began at `0.0s` and wrote `תודה רבה` over an **instrumental intro** whose
  singing does not start until 15.3s.
* It returned **four exactly duplicated segments**.

Re-running it here on the same song reproduced the first exactly - `תודה רבה.`
at 0.0s and `תודה.` at 130.4s, on a recording that ends at 132.7s - and both are
now dropped.

Both are the well-known failure of this family of models on music: given silence
or an instrument, it produces the most common thing it has heard over silence,
and once it starts repeating it keeps repeating. `docs/phase0/quotas.md` is
explicit that the same filtering is needed whichever service is used.

So every rule below deletes rather than corrects, and each one is narrow enough
to name what it deletes and why. Deleting a real line is worse than keeping a
false one: a missing line is obvious to a singer and easy to type back, where a
line that quietly moved is not.
"""

import logging
import re

from packages.audio.silence import Silence
from packages.core.lyrics import LineDraft
from packages.lyrics.align import align
from packages.providers.transcription import Segment, Transcript

log = logging.getLogger("karuki.lyrics.transcript")

# Whisper's own thresholds for "this was not speech", and it takes *both*.
# Measured on the phase 0 song rather than assumed, and the measurement is the
# reason the second condition is there at all:
#
#     0.0s   no_speech 0.69  logprob -0.21   תודה רבה.        <- hallucinated
#     30.0s  no_speech 0.55  logprob -0.27   דרושנה דורשיך...  <- real
#     86.4s  no_speech 0.82  logprob -0.27   לשמוע אל הרינה... <- real
#     130.4s no_speech 0.76  logprob -0.11   תודה.            <- hallucinated
#
# Sung Hebrew scores *higher* on `no_speech_prob` than the hallucination does -
# 0.82 against 0.69 - so dropping on that number alone would have deleted four
# real lines and kept neither of the two false ones. Requiring both numbers
# leaves this transcript untouched, which is the point: the rule is a backstop
# for genuine noise, and the caption list below is what catches phase 0's case.
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0

# What the model writes when it hears no words at all. These are captions from
# its training data, not lyrics, and phase 0 saw the first of them over an
# instrumental intro. Matched against the whole line only - `תודה רבה` inside a
# real line is a real line.
CAPTION_FILLER = {
    "תודה רבה",
    "תודה",
    "תודה על הצפייה",
    "תודה שצפיתם",
    "כתוביות",
    "הכתוביות נעשו על ידי",
    "עריכה וכתוביות",
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara.org community",
    "please subscribe",
}

# A model that has started repeating itself will do it for as long as the silence
# lasts. Two in a row can be a real chorus; four cannot.
MAX_CONSECUTIVE_REPEATS = 2

PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def _bare(text: str) -> str:
    return " ".join(PUNCTUATION.sub(" ", text).split()).casefold()


def is_caption_filler(text: str) -> bool:
    return _bare(text) in {_bare(phrase) for phrase in CAPTION_FILLER}


def heard_no_speech(segment: Segment) -> bool:
    """The model's own two numbers, both of them.

    `no_speech_prob` alone throws away quiet real singing; `avg_logprob` alone
    throws away anything the model found hard, which in Hebrew is a lot.
    """
    if segment.no_speech_prob is None or segment.avg_logprob is None:
        return False
    return segment.no_speech_prob > MAX_NO_SPEECH_PROB and segment.avg_logprob < MIN_AVG_LOGPROB


def clean_segments(segments: list[Segment]) -> list[Segment]:
    """Drop what the model heard in the silence, and its stutters."""
    kept: list[Segment] = []
    repeats = 0

    for segment in segments:
        if not segment.text.strip():
            continue
        if is_caption_filler(segment.text):
            log.info("dropping caption filler %r at %dms", segment.text, segment.start_ms)
            continue
        if heard_no_speech(segment):
            log.info(
                "dropping %r at %dms (no_speech %.2f, logprob %.2f)",
                segment.text,
                segment.start_ms,
                segment.no_speech_prob or 0,
                segment.avg_logprob or 0,
            )
            continue

        if kept and _bare(segment.text) == _bare(kept[-1].text):
            repeats += 1
            if repeats >= MAX_CONSECUTIVE_REPEATS:
                log.info("dropping repeat %d of %r", repeats + 1, segment.text)
                continue
        else:
            repeats = 0

        kept.append(segment)

    return kept


def lines_from(transcript: Transcript, gaps: list[Silence] | None = None) -> list[LineDraft]:
    """The whole journey: a raw transcript in, karaoke lines out.

    Cleaning first, because a hallucinated segment should not be given a line to
    sit on, and then `packages/lyrics/align.py`, which is where a segment
    becomes one or more lines. `gaps` is the silence in the vocals stem, when
    there is a vocals stem to look at; without it the aligner splits on length
    alone.
    """
    return align(clean_segments(transcript.segments), gaps)

"""Where the singing stops, measured off the separated vocals.

This exists because of one measurement in phase 0 and the correction to it.

`T-0.5.1` found that a Whisper segment is not a karaoke line: 53 segments
against 182 sung phrases in the same song, with single segments running from
14.86s to 26.46s and covering four phrases. Its conclusion was that **the line
division has to come from the audio, not from the transcript.**

`T-0.5.3` then measured the energetic detector against human taps and found it
*worse* than Whisper's own timings for deciding when a line **starts** - 372ms
median error against 242ms, and 0% within 100ms against 16%. Its conclusion was
precise about what survives: the detector is useful **for splitting lines, not
for setting their times.**

So this module answers one question - where is nobody singing - and nothing
here ever becomes a timestamp. The timestamps stay Whisper's.

It works on the isolated vocals stem, which is what makes "quiet" mean "not
singing" rather than "not playing"; `T-0.1.3` scored that separation 5/5 for
exactly this kind of use.
"""

from dataclasses import dataclass

import numpy as np

from .decode import Decoded

# 20ms frames: short enough to place a boundary well inside the 100ms budget
# chapter 8 gives the lyrics, long enough that one percussive frame of bleed
# does not read as singing.
FRAME_MS = 20
# A gap has to last this long to be a place a line could break. Shorter than
# this is a consonant or a breath inside a phrase, and phase 0 measured 82-94%
# of words as contiguous, so real breaks are the minority by design.
MIN_SILENCE_MS = 350
# The floor is taken from the recording rather than fixed: a quiet ballad and a
# loud rock vocal have nothing in common in absolute terms. It is anchored to
# the *loud* end - the 90th percentile of frame energy is "what this singer
# sounds like" - and silence is a small fraction of that, about 22dB down.
#
# Anchoring it to the quiet end instead was tried first and is subtly broken: a
# percentile of the quiet frames moves with how much silence the song happens to
# contain, so a song with a long instrumental raises its own floor until the
# singing falls under it.
LOUD_PERCENTILE = 90
SILENCE_RATIO = 0.08
# If a recording is so compressed that the floor lands on top of the singing,
# stop rather than declaring the whole song silent.
MAX_SILENT_FRACTION = 0.9


@dataclass(frozen=True)
class Silence:
    """A stretch where nobody is singing, in milliseconds from the start."""

    start_ms: int
    end_ms: int

    @property
    def middle_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


def frame_energy(decoded: Decoded, frame_ms: int = FRAME_MS) -> np.ndarray:
    """RMS per frame. Nothing clever: loudness is the whole question here."""
    size = max(1, int(decoded.rate * frame_ms / 1000))
    usable = len(decoded.samples) - len(decoded.samples) % size
    if usable < size:
        return np.zeros(0, dtype=np.float64)
    frames = decoded.samples[:usable].reshape(-1, size).astype(np.float64)
    return np.sqrt((frames**2).mean(axis=1))


def silences(
    decoded: Decoded,
    frame_ms: int = FRAME_MS,
    min_silence_ms: int = MIN_SILENCE_MS,
) -> list[Silence]:
    """Every gap in the singing that is long enough to break a line at."""
    energy = frame_energy(decoded, frame_ms)
    if energy.size == 0:
        return []

    floor = float(np.percentile(energy, LOUD_PERCENTILE)) * SILENCE_RATIO
    if floor <= 0:
        # Nothing loud anywhere: there is no singing to find gaps between.
        return []
    quiet = energy <= floor
    if quiet.mean() > MAX_SILENT_FRACTION:
        # The floor swallowed the song. Better to find no breaks than to find
        # them everywhere.
        return []

    found: list[Silence] = []
    start: int | None = None
    for index, is_quiet in enumerate(quiet):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            found.append(Silence(start * frame_ms, index * frame_ms))
            start = None
    if start is not None:
        found.append(Silence(start * frame_ms, len(quiet) * frame_ms))

    return [gap for gap in found if gap.length_ms >= min_silence_ms]

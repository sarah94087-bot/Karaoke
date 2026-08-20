"""Finding where the singing stops, on signals built to have known gaps.

Synthetic rather than real audio on purpose: the question here is arithmetic -
does a gap of a known length get found, and does a signal with no gap produce
none - and a real recording answers it with an asterisk every time.

What this is *for* is the narrow job `T-0.5.3` left the energy detector after
measuring it against human taps: splitting lines, never setting their times.
"""

import numpy as np

from packages.audio.decode import Decoded
from packages.audio.silence import FRAME_MS, MIN_SILENCE_MS, frame_energy, silences

RATE = 22050


def signal(*parts: tuple[float, float]) -> Decoded:
    """A signal from (seconds, amplitude) pairs."""
    samples = np.concatenate(
        [
            np.sin(np.linspace(0, seconds * 440 * 2 * np.pi, int(seconds * RATE))) * amplitude
            for seconds, amplitude in parts
        ]
    ).astype(np.float32)
    return Decoded(samples=samples, rate=RATE)


def test_a_gap_between_two_sung_stretches_is_found():
    decoded = signal((3.0, 0.5), (1.5, 0.0), (3.0, 0.5))

    found = silences(decoded)

    assert len(found) == 1
    assert abs(found[0].start_ms - 3_000) <= FRAME_MS * 2
    assert abs(found[0].end_ms - 4_500) <= FRAME_MS * 2


def test_a_short_pause_is_not_a_line_break():
    """Phase 0 measured 82-94% of words as contiguous: breaths and consonants
    are the common case, and breaking a line on one would produce a line per
    word."""
    decoded = signal((3.0, 0.5), (MIN_SILENCE_MS / 2_000, 0.0), (3.0, 0.5))

    assert silences(decoded) == []


def test_continuous_singing_has_no_breaks_in_it():
    assert silences(signal((6.0, 0.5))) == []


def test_a_quiet_voice_is_still_a_voice():
    """The floor is taken from the recording, not fixed: a ballad and a rock
    vocal have nothing in common in absolute terms."""
    decoded = signal((3.0, 0.02), (1.0, 0.0), (3.0, 0.02))

    assert len(silences(decoded)) == 1


def test_silence_all_the_way_through_finds_nothing():
    """Better to find no breaks than to find them everywhere: a detector that
    calls the whole song silent would cut every line in half."""
    assert silences(signal((6.0, 0.0))) == []


def test_a_signal_shorter_than_a_frame_is_not_an_error():
    assert silences(Decoded(samples=np.zeros(4, dtype=np.float32), rate=RATE)) == []


def test_energy_follows_the_amplitude():
    energy = frame_energy(signal((1.0, 0.0), (1.0, 0.8)))

    assert energy[:40].max() < energy[-40:].min()


def test_the_middle_of_a_gap_is_where_a_line_would_break():
    decoded = signal((2.0, 0.5), (2.0, 0.0), (2.0, 0.5))

    gap = silences(decoded)[0]

    assert abs(gap.middle_ms - 3_000) <= FRAME_MS * 2
    assert gap.length_ms >= MIN_SILENCE_MS

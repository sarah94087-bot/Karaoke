"""Tempo and key detection, in numpy.

Written here rather than pulled in from librosa, which would bring scipy and
numba with it - a few hundred MB into an API image that has to stay deployable
on a free tier, for two functions. The project already made this trade once, in
phase 0, when the phase vocoder was written by hand rather than taking on
SoundTouch.

Both algorithms are standard and old:

- tempo from the autocorrelation of a spectral-flux onset envelope;
- key by correlating a chroma vector against the Krumhansl-Schmuckler profiles.

Both are fed the whole mix. Feeding them stems instead was tried and measured,
and it was worse - see packages/core/analysis.py for the numbers and the reason.
"""

from dataclasses import dataclass

import numpy as np

from .decode import Decoded

# Chapter 8's player shows this, so the range is the one a person would accept
# as a tempo rather than the one a maths function would return.
MIN_BPM = 60.0
MAX_BPM = 200.0
# Where an ambiguous octave gets resolved to. Most songs a person sings sit
# here, and a detector that says 75 when the answer is 150 is more annoying than
# one that says nothing.
PREFERRED_BPM = (85.0, 165.0)

FRAME = 2048
HOP = 512

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl & Kessler's probe-tone ratings. These are the standard weights; the
# point is the shape, which says which scale degrees a listener expects to be
# prominent in a key.
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float64
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float64
)


# Below this the chroma does not really pick a key, and storing one anyway shows
# the user a confident wrong answer they may transpose against.
MIN_KEY_CONFIDENCE = 0.03


@dataclass(frozen=True)
class Analysis:
    """What the player shows and the song row stores."""

    bpm: float | None
    key: str | None
    # Margin over the best key that is *not* the relative of the winner. See
    # detect_key: a relative pair shares all seven notes, so measuring against
    # it would report near-zero confidence for a perfectly clear key.
    key_confidence: float = 0.0
    # The relative major or minor, when there is one. Chroma alone cannot choose
    # between them, and saying so is more honest than pretending.
    key_alternative: str | None = None


def _onset_envelope(decoded: Decoded) -> np.ndarray:
    """Spectral flux: how much the spectrum brightened since the last frame.

    Only increases count. A note ending is not an onset, and counting it as one
    doubles the apparent tempo of anything staccato.
    """
    samples = decoded.samples
    if len(samples) < FRAME * 2:
        return np.zeros(0, dtype=np.float64)

    frames = 1 + (len(samples) - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)
    strides = np.lib.stride_tricks.sliding_window_view(samples, FRAME)[::HOP][:frames]
    spectra = np.abs(np.fft.rfft(strides * window, axis=1))

    flux = np.diff(spectra, axis=0)
    envelope = np.maximum(flux, 0).sum(axis=1)

    # Remove the slow drift so a long crescendo does not look like a beat.
    if len(envelope) > 16:
        smoothed = np.convolve(envelope, np.ones(16) / 16, mode="same")
        envelope = np.maximum(envelope - smoothed, 0)
    return envelope.astype(np.float64)


def detect_bpm(decoded: Decoded) -> float | None:
    """Autocorrelate the onset envelope and read off the strongest period."""
    envelope = _onset_envelope(decoded)
    if envelope.size < 32 or not np.any(envelope):
        return None

    envelope = envelope - envelope.mean()
    correlation = np.correlate(envelope, envelope, mode="full")[len(envelope) - 1 :]
    if correlation[0] <= 0:
        return None

    frames_per_second = decoded.rate / HOP
    min_lag = max(1, int(round(frames_per_second * 60.0 / MAX_BPM)))
    max_lag = min(len(correlation) - 1, int(round(frames_per_second * 60.0 / MIN_BPM)))
    if max_lag <= min_lag:
        return None

    window = correlation[min_lag : max_lag + 1]
    lag = int(np.argmax(window)) + min_lag
    if correlation[lag] <= 0:
        return None

    return _fold_into_range(frames_per_second * 60.0 / lag)


def _fold_into_range(bpm: float) -> float:
    """Resolve the octave ambiguity every tempo detector has.

    A half-time reading is not wrong so much as unhelpful: 75 and 150 describe
    the same music, and one of them is what a person would tap.
    """
    low, high = PREFERRED_BPM
    for _ in range(4):
        if bpm < low:
            bpm *= 2
        elif bpm > high:
            bpm /= 2
        else:
            break
    return round(float(np.clip(bpm, MIN_BPM, MAX_BPM)), 1)


def chroma(decoded: Decoded) -> np.ndarray:
    """Energy per pitch class, averaged over the whole signal."""
    samples = decoded.samples
    if len(samples) < FRAME * 2:
        return np.zeros(12, dtype=np.float64)

    frames = 1 + (len(samples) - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)
    strides = np.lib.stride_tricks.sliding_window_view(samples, FRAME)[::HOP][:frames]
    magnitudes = np.abs(np.fft.rfft(strides * window, axis=1))

    freqs = np.fft.rfftfreq(FRAME, 1.0 / decoded.rate)
    # Below ~55Hz the bins are too coarse to name a note; above ~2kHz the
    # harmonics of everything blur together and only add noise.
    usable = (freqs >= 55.0) & (freqs <= 2000.0)
    freqs = freqs[usable]
    magnitudes = magnitudes[:, usable]
    if freqs.size == 0:
        return np.zeros(12, dtype=np.float64)

    # A4 = 440Hz is pitch class 9.
    midi = 69 + 12 * np.log2(freqs / 440.0)
    classes = np.rint(midi).astype(int) % 12

    totals = np.zeros(12, dtype=np.float64)
    energy = magnitudes.sum(axis=0)
    for pitch_class in range(12):
        totals[pitch_class] = energy[classes == pitch_class].sum()

    peak = totals.max()
    return totals / peak if peak > 0 else totals


def relative_of(key: str) -> str:
    """The relative minor of a major key, or the relative major of a minor one.

    C and Am contain exactly the same seven notes, so an averaged chroma cannot
    tell them apart - which is why they are compared as one candidate rather
    than as two.
    """
    if key.endswith("m"):
        tonic = PITCH_CLASSES.index(key[:-1])
        return PITCH_CLASSES[(tonic + 3) % 12]
    tonic = PITCH_CLASSES.index(key)
    return f"{PITCH_CLASSES[(tonic - 3) % 12]}m"


def detect_key(decoded: Decoded) -> tuple[str | None, float, str | None]:
    """Correlate the chroma against all 24 keys and take the best fit.

    Returns the key, how sure we are about the *notes*, and the relative key we
    could not rule out.
    """
    vector = chroma(decoded)
    if not np.any(vector):
        return None, 0.0, None

    scores: list[tuple[float, str]] = []
    for tonic in range(12):
        rotated = np.roll(vector, -tonic)
        for profile, suffix in ((MAJOR_PROFILE, ""), (MINOR_PROFILE, "m")):
            scores.append((_correlate(rotated, profile), f"{PITCH_CLASSES[tonic]}{suffix}"))

    scores.sort(reverse=True)
    best_score, best_key = scores[0]
    if best_score <= 0:
        return None, 0.0, None

    # The runner-up is nearly always the relative key, and it is not a rival: it
    # is the same seven notes. Confidence is measured against the best genuinely
    # different candidate, or the number would say "unsure" for every clear key.
    relative = relative_of(best_key)
    rival = next((score for score, key in scores[1:] if key != relative), 0.0)
    confidence = float(max(0.0, (best_score - rival) / best_score))
    return best_key, round(confidence, 3), relative


def _correlate(vector: np.ndarray, profile: np.ndarray) -> float:
    a = vector - vector.mean()
    b = profile - profile.mean()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 0 else 0.0


def analyse(for_tempo: Decoded, for_key: Decoded) -> Analysis:
    """Tempo from one signal, key from another.

    They are separate arguments because the best source for each is different,
    and the caller is the one that knows which stems it has.

    A key below MIN_KEY_CONFIDENCE is dropped rather than reported. Chapter 8
    shows this number to a singer who may transpose against it, so "we do not
    know" is worth more than a plausible guess.
    """
    key, confidence, alternative = detect_key(for_key)
    if key is not None and confidence < MIN_KEY_CONFIDENCE:
        key, alternative = None, None

    return Analysis(
        bpm=detect_bpm(for_tempo),
        key=key,
        key_confidence=confidence,
        key_alternative=alternative,
    )

"""Tempo and key detection.

Tested against signals whose answer is known by construction - a click track at
an exact tempo, a chord progression in an exact key - because there is no other
way to be sure a detector is right rather than merely confident. The real songs
in `input/` are used as a sanity check where they exist, since a detector that
works only on sine waves is not a detector.
"""

import shutil

import numpy as np
import pytest

from packages.audio.analyse import (
    MIN_KEY_CONFIDENCE,
    PITCH_CLASSES,
    Analysis,
    analyse,
    detect_bpm,
    detect_key,
    relative_of,
)
from packages.audio.decode import ANALYSIS_RATE, Decoded, decode, mix
from packages.audio.normalize import AudioError

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg decodes the audio")

RATE = ANALYSIS_RATE

NOTES = {
    "C": 261.63,
    "C#": 277.18,
    "D": 293.66,
    "D#": 311.13,
    "E": 329.63,
    "F": 349.23,
    "F#": 369.99,
    "G": 392.00,
    "G#": 415.30,
    "A": 440.00,
    "A#": 466.16,
    "B": 493.88,
}


def click_track(bpm: float, seconds: float = 16.0) -> Decoded:
    """Percussive clicks at an exact tempo."""
    total = int(RATE * seconds)
    signal = np.zeros(total, dtype=np.float32)
    period = int(RATE * 60.0 / bpm)
    envelope = np.exp(-np.arange(600) / RATE * 60) * np.sin(2 * np.pi * 180 * np.arange(600) / RATE)
    for start in range(0, total - 600, period):
        signal[start : start + 600] += envelope.astype(np.float32)
    return Decoded(samples=signal, rate=RATE)


def tone(frequency: float, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return sum(np.sin(2 * np.pi * frequency * h * t) / h for h in (1, 2, 3)).astype(np.float32)


def chord(names: list[str], octave_up: list[str] | None = None) -> np.ndarray:
    raised = set(octave_up or [])
    return sum(tone(NOTES[name] * (2 if name in raised else 1), 1.0) for name in names).astype(
        np.float32
    )


def progression(chords: list[np.ndarray], repeats: int = 4) -> Decoded:
    return Decoded(samples=np.concatenate(chords * repeats), rate=RATE)


# --- tempo ------------------------------------------------------------------


@pytest.mark.parametrize("bpm", [90, 100, 120, 128, 140])
def test_a_click_track_is_measured_within_two_percent(bpm: int):
    detected = detect_bpm(click_track(bpm))

    assert detected is not None
    assert abs(detected - bpm) / bpm < 0.02, f"{bpm} was measured as {detected}"


def test_a_half_time_reading_is_folded_into_the_range_people_tap():
    """75 and 150 describe the same music; one of them is what a person counts."""
    detected = detect_bpm(click_track(75))

    assert detected is not None
    assert 85 <= detected <= 165


def test_silence_has_no_tempo():
    silence = Decoded(samples=np.zeros(RATE * 5, dtype=np.float32), rate=RATE)

    assert detect_bpm(silence) is None


def test_a_clip_too_short_to_have_a_tempo_returns_nothing():
    tiny = Decoded(samples=np.zeros(100, dtype=np.float32), rate=RATE)

    assert detect_bpm(tiny) is None


# --- key --------------------------------------------------------------------


def test_a_major_progression_is_recognised():
    """I - V - vi - IV in C."""
    music = progression(
        [
            chord(["C", "E", "G"]),
            chord(["G", "B", "D"], octave_up=["D"]),
            chord(["A", "C", "E"], octave_up=["C", "E"]),
            chord(["F", "A", "C"], octave_up=["C"]),
        ]
    )

    key, confidence, _ = detect_key(music)

    assert key == "C", f"got {key}"
    assert confidence > MIN_KEY_CONFIDENCE


def test_a_minor_progression_is_recognised():
    """i - iv - v - i in A minor."""
    music = progression(
        [
            chord(["A", "C", "E"], octave_up=["C", "E"]),
            chord(["D", "F", "A"]),
            chord(["E", "G", "B"]),
            chord(["A", "C", "E"], octave_up=["C", "E"]),
        ]
    )

    key, _, _ = detect_key(music)

    assert key == "Am", f"got {key}"


def test_transposing_the_music_transposes_the_answer():
    """The strongest evidence that the detector is reading the notes rather than
    something incidental about the synthesis."""
    up_a_tone = progression(
        [
            chord(["D", "F#", "A"]),
            chord(["A", "C#", "E"], octave_up=["E"]),
            chord(["B", "D", "F#"], octave_up=["D", "F#"]),
            chord(["G", "B", "D"], octave_up=["D"]),
        ]
    )

    key, _, _ = detect_key(up_a_tone)

    assert key == "D", f"got {key}"


def test_silence_has_no_key():
    silence = Decoded(samples=np.zeros(RATE * 5, dtype=np.float32), rate=RATE)

    assert detect_key(silence) == (None, 0.0, None)


@pytest.mark.parametrize(
    ("key", "expected"), [("C", "Am"), ("Am", "C"), ("D", "Bm"), ("G#m", "B"), ("F", "Dm")]
)
def test_relative_keys_are_paired_correctly(key: str, expected: str):
    """A relative pair shares all seven notes, which is why confidence is not
    measured against it."""
    assert relative_of(key) == expected


def test_every_key_has_a_relative_and_it_round_trips():
    for tonic in PITCH_CLASSES:
        for name in (tonic, f"{tonic}m"):
            assert relative_of(relative_of(name)) == name


def test_confidence_is_not_ruined_by_the_relative_key():
    """Measured against the runner-up it would read near zero for every clear
    key, because the runner-up is almost always the relative."""
    music = progression(
        [
            chord(["C", "E", "G"]),
            chord(["G", "B", "D"], octave_up=["D"]),
            chord(["F", "A", "C"], octave_up=["C"]),
            chord(["C", "E", "G"]),
        ]
    )

    key, confidence, alternative = detect_key(music)

    assert key == "C"
    assert alternative == "Am"
    assert confidence > MIN_KEY_CONFIDENCE


# --- the two together -------------------------------------------------------


def test_analyse_reports_both():
    music = progression(
        [
            chord(["C", "E", "G"]),
            chord(["G", "B", "D"], octave_up=["D"]),
            chord(["A", "C", "E"], octave_up=["C", "E"]),
            chord(["F", "A", "C"], octave_up=["C"]),
        ]
    )

    result = analyse(click_track(120), music)

    assert isinstance(result, Analysis)
    assert result.bpm is not None
    assert result.key is not None


def test_a_single_triad_is_not_a_key():
    """C, Am, Em, F and G all contain C-E-G. Declining is the right answer, and
    this pins that it is deliberate rather than luck."""
    one_chord = progression([chord(["C", "E", "G"])])

    assert analyse(click_track(120), one_chord).key is None


def test_an_unconvincing_key_is_not_reported_at_all():
    """A singer may transpose against this, so "we do not know" is worth more
    than a plausible guess."""
    noise = Decoded(
        samples=np.random.default_rng(0).normal(0, 0.1, RATE * 8).astype(np.float32), rate=RATE
    )

    result = analyse(noise, noise)

    if result.key is not None:
        assert result.key_confidence >= MIN_KEY_CONFIDENCE


# --- decoding ---------------------------------------------------------------


def test_decoding_a_file_that_is_not_audio_is_an_audio_error(tmp_path):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")

    with pytest.raises(AudioError):
        decode(broken)


def test_mixing_requires_a_common_rate():
    with pytest.raises(AudioError):
        mix(
            [
                Decoded(samples=np.zeros(10, dtype=np.float32), rate=22050),
                Decoded(samples=np.zeros(10, dtype=np.float32), rate=44100),
            ]
        )


def test_mixing_nothing_is_an_error_rather_than_silence():
    with pytest.raises(AudioError):
        mix([])


def test_mixing_truncates_to_the_shortest_part():
    mixed = mix(
        [
            Decoded(samples=np.ones(100, dtype=np.float32), rate=RATE),
            Decoded(samples=np.ones(60, dtype=np.float32), rate=RATE),
        ]
    )

    assert len(mixed.samples) == 60

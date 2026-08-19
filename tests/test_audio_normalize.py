"""T-1.5's acceptance criterion: every format in comes out as 44.1kHz stereo WAV.

The inputs are generated with ffmpeg rather than committed as fixtures - binary
test assets rot, and generating them means the awkward cases (mono, 8kHz, a
video container, cover art) are cheap to add.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from packages.audio.normalize import (
    MAX_DURATION_SEC,
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    AudioError,
    normalise,
    probe,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is required to generate the inputs and to convert them",
)

REPO_INPUT = Path(__file__).resolve().parent.parent / "input"


def synth(path: Path, *, seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> Path:
    """A sine tone in whatever shape the test needs."""
    layout = "mono" if channels == 1 else "stereo"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac",
            str(channels),
            "-af",
            f"aformat=channel_layouts={layout}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.parametrize("suffix", [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus"])
def test_every_format_comes_out_as_44100_stereo_wav(tmp_path: Path, suffix: str):
    source = synth(tmp_path / f"in{suffix}")

    info = normalise(source, tmp_path / "out.wav")

    assert (info.sample_rate, info.channels) == (TARGET_SAMPLE_RATE, TARGET_CHANNELS)
    assert info.codec == "pcm_s16le"


@pytest.mark.parametrize(("rate", "channels"), [(8000, 1), (22050, 1), (48000, 2), (96000, 2)])
def test_odd_rates_and_mono_are_resampled_and_upmixed(tmp_path: Path, rate: int, channels: int):
    """Chapter 8 mixes four stems against one clock; a stem at another rate is a
    drift bug that only shows up minutes into a song."""
    source = synth(tmp_path / "in.wav", rate=rate, channels=channels)

    info = normalise(source, tmp_path / "out.wav")

    assert (info.sample_rate, info.channels) == (TARGET_SAMPLE_RATE, TARGET_CHANNELS)


def test_duration_survives_the_conversion(tmp_path: Path):
    source = synth(tmp_path / "in.mp3", seconds=3.0)

    info = normalise(source, tmp_path / "out.wav")

    assert info.duration_sec == pytest.approx(3.0, abs=0.1)


def test_a_video_container_keeps_only_the_audio(tmp_path: Path):
    """People upload the mp4 they got from a phone. Cover art arrives the same
    way and would otherwise be encoded into the wav as noise."""
    source = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    info = normalise(source, tmp_path / "out.wav")

    assert (info.sample_rate, info.channels) == (TARGET_SAMPLE_RATE, TARGET_CHANNELS)


def test_a_file_that_is_not_audio_is_rejected_by_code(tmp_path: Path):
    """The code is the contract: the web app maps it to Hebrew."""
    source = tmp_path / "not-audio.mp3"
    source.write_bytes(b"this is not an mp3, whatever the extension says")

    with pytest.raises(AudioError) as caught:
        normalise(source, tmp_path / "out.wav")

    assert caught.value.code in {"unreadable_audio", "no_audio_stream", "unknown_duration"}


def test_a_song_over_the_cap_is_rejected_before_any_conversion(tmp_path: Path):
    """Chapter 9 caps songs at 8 minutes. Rejecting here means it never reaches
    a GPU, which is the expensive place to find out."""
    source = synth(tmp_path / "long.wav", seconds=MAX_DURATION_SEC + 2, rate=8000, channels=1)
    destination = tmp_path / "out.wav"

    with pytest.raises(AudioError) as caught:
        normalise(source, destination)

    assert caught.value.code == "song_too_long"
    assert not destination.exists()


def test_a_song_exactly_at_the_cap_is_accepted(tmp_path: Path):
    source = synth(tmp_path / "edge.wav", seconds=MAX_DURATION_SEC, rate=8000, channels=1)

    info = normalise(source, tmp_path / "out.wav")

    assert info.duration_sec == pytest.approx(MAX_DURATION_SEC, abs=0.5)


def test_nothing_is_left_behind_when_conversion_fails(tmp_path: Path):
    source = tmp_path / "broken.wav"
    source.write_bytes(b"RIFF____WAVEfmt ")
    destination = tmp_path / "out.wav"

    with pytest.raises(AudioError):
        normalise(source, destination)

    assert not destination.exists()


def test_metadata_is_not_carried_into_the_normalised_file(tmp_path: Path):
    """An upload's tags are the user's, and nothing downstream reads them."""
    source = tmp_path / "tagged.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-metadata",
            "title=מקורי",
            "-metadata",
            "artist=מישהו",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    normalise(source, tmp_path / "out.wav")
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(tmp_path / "out.wav")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout

    assert "מקורי" not in probed


@pytest.mark.skipif(not REPO_INPUT.is_dir(), reason="phase 0 inputs are gitignored")
def test_a_real_hebrew_named_song_normalises(tmp_path: Path):
    """The filenames in this project are Hebrew and the console is cp1255;
    passing one to a subprocess is exactly where that breaks."""
    songs = sorted(REPO_INPUT.glob("*.mp3"))
    if not songs:
        pytest.skip("no sample songs on this machine")
    source = next((s for s in songs if any("֐" <= c <= "ת" for c in s.name)), songs[0])

    info = normalise(source, tmp_path / "out.wav")

    assert (info.sample_rate, info.channels) == (TARGET_SAMPLE_RATE, TARGET_CHANNELS)
    assert info.duration_sec == pytest.approx(probe(source).duration_sec, abs=0.5)

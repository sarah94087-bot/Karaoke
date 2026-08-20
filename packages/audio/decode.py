"""Decoding audio into an array, for analysis.

Separate from `normalize.py` because the two want different things. Normalising
produces a file for everything downstream to play; this produces numbers for one
function to look at, and it is free to be lossy about it - mono, and at a lower
rate, because nothing here cares about stereo image or anything above 10kHz.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .normalize import AudioError, _tool

# 22.05kHz is half the source rate and still covers everything an onset or a
# chroma bin needs; it makes the analysis about four times faster than working
# at 44.1kHz stereo, on a free tier where CPU is the thing being rationed.
ANALYSIS_RATE = 22050


@dataclass(frozen=True)
class Decoded:
    samples: np.ndarray  # float32, mono, in [-1, 1]
    rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate


def decode(path: Path, rate: int = ANALYSIS_RATE, max_seconds: float | None = None) -> Decoded:
    """Decode `path` to mono float32 at `rate`.

    Raises AudioError rather than returning something empty: a caller that gets
    silence back cannot tell a quiet song from a failed decode.
    """
    command = [
        _tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
    ]
    if max_seconds is not None:
        command += ["-t", str(max_seconds)]
    command += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-acodec", "pcm_s16le", "-"]

    result = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0 or not result.stdout:
        raise AudioError("unreadable_audio", "the file could not be decoded for analysis")

    raw = np.frombuffer(result.stdout, dtype="<i2")
    return Decoded(samples=(raw.astype(np.float32) / 32768.0), rate=rate)


def mix(parts: list[Decoded]) -> Decoded:
    """Sum several decoded signals of the same rate, scaled to avoid clipping."""
    if not parts:
        raise AudioError("empty_audio", "nothing to mix")
    rate = parts[0].rate
    if any(part.rate != rate for part in parts):
        raise AudioError("normalisation_failed", "cannot mix audio at different rates")

    length = min(len(part.samples) for part in parts)
    if length == 0:
        raise AudioError("empty_audio", "the audio is empty")

    stacked = np.stack([part.samples[:length] for part in parts])
    return Decoded(samples=stacked.sum(axis=0) / len(parts), rate=rate)

"""Bring any uploaded file to the one audio format the rest of the system uses.

Everything downstream - separation, analysis, the player - assumes 44.1kHz
stereo 16-bit PCM. Normalising once, at the boundary, is what lets every later
stage skip the "but what if it is mono / 48kHz / an m4a" branch. Demucs in
particular resamples internally anyway, so doing it here costs nothing and
removes a class of bug.

ffmpeg is invoked as a subprocess rather than through a binding: it is already a
dependency of the GPU image (apps/gpu/karuki_modal.py), it handles every input
format anyone will upload, and a binding would be a second thing to keep working
on Windows, in the container, and on the remote GPU.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The one format. Chapter 8's player mixes four stems against a single clock;
# a stem at a different rate is a drift bug that only shows up minutes in.
TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2
TARGET_SAMPLE_FORMAT = "s16"

# Chapter 9. Enforced in the database too, but a file that fails here never
# reaches a GPU, which is the expensive place to find out.
MAX_DURATION_SEC = 8 * 60


class AudioError(Exception):
    """Something about the file itself. The `code` is what the web app maps to
    Hebrew text, so it is part of the API contract, not a log string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolMissing(RuntimeError):
    """ffmpeg or ffprobe is not installed. An operator problem, not a user one."""


@dataclass(frozen=True)
class AudioInfo:
    """What a probe can tell us before deciding whether to accept a file."""

    duration_sec: float
    sample_rate: int
    channels: int
    codec: str
    container: str


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ToolMissing(
            f"{name} is not on PATH. It ships in the API image; locally, install ffmpeg."
        )
    return path


def _run(tool: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_tool(tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",  # filenames and tags are Hebrew; the console is cp1255
        errors="replace",
        stdin=subprocess.DEVNULL,
    )


def probe(path: Path) -> AudioInfo:
    """Read the file's shape without decoding it.

    Raises AudioError when the file is not audio we can use, which is the normal
    case for a bad upload rather than an exceptional one.
    """
    result = _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "a:0",
            "-of",
            "json",
            str(path),
        ],
    )
    if result.returncode != 0:
        raise AudioError("unreadable_audio", "the file could not be read as audio")

    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise AudioError("no_audio_stream", "the file has no audio track")

    stream = streams[0]
    container = payload.get("format", {})
    # Duration can be absent on a stream and present on the container, or the
    # other way round, depending on the format. A VBR mp3 with no Xing header
    # reports neither, and ffprobe has to decode to find out.
    raw_duration = stream.get("duration") or container.get("duration")
    if raw_duration is None:
        raise AudioError("unknown_duration", "the length of the file could not be determined")

    return AudioInfo(
        duration_sec=float(raw_duration),
        sample_rate=int(stream.get("sample_rate", 0)),
        channels=int(stream.get("channels", 0)),
        codec=str(stream.get("codec_name", "")),
        container=str(container.get("format_name", "")),
    )


def check_acceptable(info: AudioInfo) -> None:
    """The rules a file has to pass before it is worth spending GPU on."""
    if info.duration_sec <= 0:
        raise AudioError("empty_audio", "the file contains no audio")
    if info.duration_sec > MAX_DURATION_SEC:
        raise AudioError(
            "song_too_long",
            f"the song is longer than {MAX_DURATION_SEC // 60} minutes",
        )


def normalise(source: Path, destination: Path) -> AudioInfo:
    """Write `source` to `destination` as 44.1kHz stereo 16-bit PCM WAV.

    Returns the shape of the *result*, not of the input, because that is what
    the caller stores on the song row.
    """
    info = probe(source)
    check_acceptable(info)

    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        "ffmpeg",
        [
            "-nostdin",
            "-y",
            "-i",
            str(source),
            # Cover art arrives as a video stream and would otherwise be encoded
            # into the wav as garbage.
            "-vn",
            # Tags are not needed downstream, and an upload's metadata is the
            # user's, not ours to carry around.
            "-map_metadata",
            "-1",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-sample_fmt",
            TARGET_SAMPLE_FORMAT,
            "-f",
            "wav",
            str(destination),
        ],
    )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise AudioError("normalisation_failed", "the file could not be converted")

    return probe(destination)

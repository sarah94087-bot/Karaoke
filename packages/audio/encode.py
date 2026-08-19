"""Encoding stems for delivery.

D-13 picks a compressed format for the stems: four uncompressed 44.1kHz stereo
tracks is roughly 120MB for a four-minute song, against ~15MB compressed, and
chapter 9 budgets ~300MB of storage per user. The player downloads all four, so
this is the difference between a usable library and a full one.

The four are encoded concurrently, not one after another. Phase 0 measured
encoding at 15.5s against 6.2s for the separation itself - serially it was the
slowest part of the whole job, which is a silly thing for the cheap step to be.
"""

import subprocess
from pathlib import Path

# 128kbps is what phase 0 measured the storage budget against (15.4MB per song,
# see docs/phase0/quotas.md). It is a backing track to sing over, not a master.
STEM_FORMAT = "mp3"
STEM_BITRATE = "128k"


class EncodeError(RuntimeError):
    pass


def _command(source: Path, destination: Path, bitrate: str) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(destination),
    ]


def to_mp3(source: Path, destination: Path, bitrate: str = STEM_BITRATE) -> Path:
    """Encode one file. Convenience wrapper around `encode_all`."""
    return encode_all({destination.stem: (source, destination)}, bitrate)[destination.stem]


def encode_all(jobs: dict[str, tuple[Path, Path]], bitrate: str = STEM_BITRATE) -> dict[str, Path]:
    """Encode several files at once.

    `jobs` maps a name to (source, destination). Every process is started before
    any is waited on, which is the whole point; ffmpeg is single-threaded enough
    per stream that four at once costs little more than one.
    """
    running: dict[str, subprocess.Popen[bytes]] = {}
    for name, (source, destination) in jobs.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        running[name] = subprocess.Popen(
            _command(source, destination, bitrate),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )

    failures: list[str] = []
    for name, process in running.items():
        _, stderr = process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip().splitlines()
            failures.append(f"{name}: {detail[-1] if detail else 'unknown error'}")

    if failures:
        # Clean up whatever did succeed: a partial set of stems is not a usable
        # song, and leaving three of four behind invites a confusing retry.
        for _, destination in jobs.values():
            destination.unlink(missing_ok=True)
        raise EncodeError("; ".join(failures))

    return {name: destination for name, (_, destination) in jobs.items()}

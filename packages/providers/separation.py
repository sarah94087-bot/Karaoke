"""Separation, behind one call: audio in, four stems out.

Two backends implement the same protocol.

`local` runs Demucs on the CPU. Chapter 10 asks for exactly this in the local
environment - "slow, but free and with no dependency" - and chapter 11 makes it
the escape route if the GPU credit ever disappears. Phase 0 measured it at about
1.13x the length of the song.

`modal` calls the serverless GPU function deployed in T-0.3, which phase 0
measured at 6.2s for a song that takes 144s locally. It costs real money from a
$1/month credit, so it is never the default: it is chosen explicitly, by
configuration, and D-07's budget is the reason this file counts gpu_seconds.

Which one is in use is a setting, not an import. Nothing above this module
should know that a GPU exists.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from packages.audio.encode import STEM_BITRATE, STEM_FORMAT, encode_all
from packages.core.enums import StemKind

# The order Demucs returns, and the order the mixer shows them in.
STEM_NAMES: tuple[str, ...] = tuple(str(kind) for kind in StemKind)

MODAL_APP = "karuki-separation"
MODAL_FUNCTION = "separate"


class SeparationError(RuntimeError):
    """The separation itself failed. Chapter 7 is explicit that there is no
    automatic retry on a GPU step - a retry costs double credit, so the user
    decides."""


class SeparationUnavailable(SeparationError):
    """This process cannot separate at all - the backend is not installed here.

    Distinct from a failure, because it is an operator's problem and not the
    song's: it is what the API container reports, since torch and demucs are
    deliberately not in its image (they are ~2GB). Telling a user their file
    could not be separated would be a lie.
    """


@dataclass(frozen=True)
class Separated:
    """Four stems on disk, plus what the run cost."""

    stems: dict[str, Path]
    backend: str
    format: str = STEM_FORMAT
    # None on the local backend, which uses no GPU at all. On the remote one
    # this is what chapter 7 calls the only way to know how much credit is left.
    gpu_seconds: float | None = None
    timings: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = set(STEM_NAMES) - set(self.stems)
        if missing:
            raise SeparationError(f"separation produced no {', '.join(sorted(missing))}")


class Separator(Protocol):
    """What the rest of the system may assume about separation."""

    name: str

    def separate(self, source: Path, destination: Path) -> Separated:
        """Separate `source` into four stems written under `destination`."""
        ...


@dataclass
class LocalDemucsSeparator:
    """Demucs on this machine's CPU. Free, slow, and always available."""

    name: str = "local"
    model: str = "htdemucs"
    device: str = "cpu"

    def separate(self, source: Path, destination: Path) -> Separated:
        # Imported here, not at module scope: the whole point of keeping them
        # out of the `api` dependency group is that the API image does not carry
        # ~2GB of torch, so this module has to be importable without them.
        try:
            import soundfile
            from demucs.api import Separator as DemucsSeparator
        except ImportError as exc:
            raise SeparationUnavailable(
                "local separation needs the `separation` dependency group "
                f"(pip install -e .[separation]); {exc}"
            ) from exc

        destination.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            separator = DemucsSeparator(model=self.model, device=self.device)
            loaded = time.monotonic()
            _, stems = separator.separate_audio_file(source)
        except Exception as exc:  # demucs raises a variety of bare exceptions
            raise SeparationError(f"demucs failed: {exc}") from exc
        separated = time.monotonic()

        raw: dict[str, tuple[Path, Path]] = {}
        for name, tensor in stems.items():
            wav = destination / f"{name}.wav"
            soundfile.write(str(wav), tensor.cpu().numpy().T, separator.samplerate)
            raw[name] = (wav, destination / f"{name}.{STEM_FORMAT}")

        encoded = encode_all(raw, STEM_BITRATE)
        for wav, _ in raw.values():
            wav.unlink(missing_ok=True)
        finished = time.monotonic()

        return Separated(
            stems=encoded,
            backend=self.name,
            gpu_seconds=None,
            timings={
                "model_load_s": round(loaded - started, 2),
                "separation_s": round(separated - loaded, 2),
                "encode_s": round(finished - separated, 2),
                "total_s": round(finished - started, 2),
            },
        )


@dataclass
class ModalSeparator:
    """The deployed serverless GPU function from T-0.3.

    The function is looked up by name rather than imported, which is how it was
    called in phase 0 and keeps this side free of the modal app definition.
    """

    name: str = "modal"
    app: str = MODAL_APP
    function: str = MODAL_FUNCTION

    def separate(self, source: Path, destination: Path) -> Separated:
        import modal

        destination.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            remote = modal.Function.from_name(self.app, self.function)
            result = remote.remote(source.name, source.read_bytes())
        except Exception as exc:
            raise SeparationError(f"the remote GPU call failed: {exc}") from exc
        elapsed = time.monotonic() - started

        stems: dict[str, Path] = {}
        for name, blob in result["stems"].items():
            path = destination / f"{name}.{STEM_FORMAT}"
            path.write_bytes(blob)
            stems[name] = path

        timings = dict(result.get("timings", {}))
        timings["total_s"] = round(elapsed, 2)
        return Separated(
            stems=stems,
            backend=self.name,
            # Billed for the time the container spent on the work, which is what
            # the remote side reports; the round trip is ours, not the GPU's.
            gpu_seconds=float(timings.get("in_container_s", 0.0)),
            timings=timings,
        )


BACKENDS: dict[str, type[Separator]] = {
    "local": LocalDemucsSeparator,
    "modal": ModalSeparator,
}


def get_separator(backend: str = "local") -> Separator:
    """Local by default, deliberately.

    Defaulting to the GPU would mean a stray test run spends real credit out of
    a $1 monthly budget. Choosing it has to be a decision someone made.
    """
    try:
        return BACKENDS[backend]()
    except KeyError:
        raise SeparationError(
            f"unknown separation backend {backend!r}; expected one of {sorted(BACKENDS)}"
        ) from None

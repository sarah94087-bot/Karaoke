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

**Both backends are given storage and keys, not files** (T-3.3). The local one
reads and writes through the disk it already has; the remote one is handed
signed URLs and does its own reading and writing, so a 40MB source and 15MB of
stems never pass through the API at all. That is the same authorisation the
browser upload uses - a link, for one key, for an hour - and it means no
credential ever leaves this process.

Which one is in use is a setting, not an import. Nothing above this module
should know that a GPU exists.
"""

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from packages.audio.encode import STEM_BITRATE, STEM_FORMAT, encode_all
from packages.core.enums import StemKind
from packages.providers.storage import Storage, StoredObject

# The order Demucs returns, and the order the mixer shows them in.
STEM_NAMES: tuple[str, ...] = tuple(str(kind) for kind in StemKind)

MODAL_APP = "karuki-separation"
MODAL_FUNCTION = "separate_to_storage"

# Long enough to outlast a cold start plus the longest song the quota allows,
# and no longer. The GPU holds these links for the length of one call.
REMOTE_LINK_TTL = 3600


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
    """Four stems **in storage**, plus what the run cost."""

    stems: dict[str, StoredObject]
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

    def separate(self, storage: Storage, source_key: str, targets: dict[str, str]) -> Separated:
        """Separate the object at `source_key` into the four `targets` keys.

        Storage is passed in rather than paths, because where the work happens
        decides how the bytes travel: on this machine they go through the disk,
        on the GPU they go through signed links that this process never opens.
        """
        ...


@dataclass
class LocalDemucsSeparator:
    """Demucs on this machine's CPU. Free, slow, and always available."""

    name: str = "local"
    model: str = "htdemucs"
    device: str = "cpu"

    def separate(self, storage: Storage, source_key: str, targets: dict[str, str]) -> Separated:
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

        source = storage.local_path(source_key)
        started = time.monotonic()
        try:
            separator = DemucsSeparator(model=self.model, device=self.device)
            loaded = time.monotonic()
            _, stems = separator.separate_audio_file(source)
        except Exception as exc:  # demucs raises a variety of bare exceptions
            raise SeparationError(f"demucs failed: {exc}") from exc
        separated = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="karuki-stems-") as tmp:
            work = Path(tmp)
            raw: dict[str, tuple[Path, Path]] = {}
            for name, tensor in stems.items():
                wav = work / f"{name}.wav"
                soundfile.write(str(wav), tensor.cpu().numpy().T, separator.samplerate)
                raw[name] = (wav, work / f"{name}.{STEM_FORMAT}")

            encoded = encode_all(raw, STEM_BITRATE)
            for wav, _ in raw.values():
                wav.unlink(missing_ok=True)
            # The stems go into storage here rather than being handed back as
            # files. It is what makes the two backends the same shape, and the
            # caller no longer needs a temporary directory of its own.
            stored = {name: storage.put(targets[name], path) for name, path in encoded.items()}
        finished = time.monotonic()

        return Separated(
            stems=stored,
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

    def separate(self, storage: Storage, source_key: str, targets: dict[str, str]) -> Separated:
        """Hand the GPU a source link and four upload links, and let it do its
        own reading and writing.

        Phase 0 sent the audio inside the call and got 15MB of stems back the
        same way, which made this process the middle of every transfer. Signed
        links take it out of the path: the same authorisation the browser
        upload uses, for one key, for an hour, and no credential leaves here.
        """
        import modal

        source_url = storage.signed_url(source_key, REMOTE_LINK_TTL)
        self._must_be_reachable(source_url)
        stem_urls = {
            name: storage.signed_upload_url(key, REMOTE_LINK_TTL) for name, key in targets.items()
        }

        started = time.monotonic()
        try:
            remote = modal.Function.from_name(self.app, self.function)
            result = remote.remote(source_url, stem_urls)
        except Exception as exc:
            raise SeparationError(f"the remote GPU call failed: {exc}") from exc
        elapsed = time.monotonic() - started

        if result.get("error"):
            raise SeparationError(f"the remote GPU run failed: {result['error']}")

        written = result.get("written", {})
        stems = {
            name: StoredObject(key=key, bytes=int(written[name]))
            for name, key in targets.items()
            if name in written
        }

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

    @staticmethod
    def _must_be_reachable(url: str) -> None:
        """A link only this machine can open is no use to a rented container.

        The local storage backend hands out root-relative links unless
        KARUKI_PUBLIC_BASE_URL is set, and root-relative means "this API",
        which from somebody else network is nothing at all. Better said here
        than as a timeout twenty seconds into a call that costs money.
        """
        if not url.startswith("http"):
            raise SeparationUnavailable(
                "the modal backend needs storage that hands out absolute links: "
                "use KARUKI_STORAGE_BACKEND=s3, or set KARUKI_PUBLIC_BASE_URL"
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

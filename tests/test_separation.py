"""T-1.6: one call takes a file and returns four stems.

The local CPU backend is exercised for real - it is free, which is the point of
it existing. The Modal backend is exercised against a fake: a real call spends
money out of a $1/month credit (docs/phase0/quotas.md), and a test suite that
quietly bills the project is a bad test suite. What the fake checks is the part
that is ours: that the result is unpacked, the stems land on disk, and
gpu_seconds is carried out to the caller.
"""

import shutil
from pathlib import Path

import pytest

from packages.audio.encode import STEM_FORMAT
from packages.core.enums import StemKind
from packages.providers.separation import (
    STEM_NAMES,
    LocalDemucsSeparator,
    ModalSeparator,
    Separated,
    SeparationError,
    get_separator,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg encodes the stems")


def synth(path: Path, seconds: float = 2.0) -> Path:
    import subprocess

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
            f"sine=frequency=440:duration={seconds}:sample_rate=44100",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


# --- the contract, independent of backend -----------------------------------


def test_the_four_stem_names_are_the_ones_the_mixer_shows():
    assert STEM_NAMES == ("vocals", "drums", "bass", "other")
    assert set(STEM_NAMES) == {str(kind) for kind in StemKind}


def test_a_result_missing_a_stem_is_refused(tmp_path: Path):
    """Three of four is not a usable song, and the mixer has four faders."""
    with pytest.raises(SeparationError) as caught:
        Separated(stems={"vocals": tmp_path / "v.mp3"}, backend="fake")

    assert "drums" in str(caught.value)


def test_local_is_the_default_backend():
    """Defaulting to the GPU would let a stray run spend real credit."""
    assert get_separator().name == "local"
    assert isinstance(get_separator(), LocalDemucsSeparator)


def test_the_remote_backend_has_to_be_asked_for_by_name():
    assert isinstance(get_separator("modal"), ModalSeparator)


def test_an_unknown_backend_fails_loudly():
    with pytest.raises(SeparationError):
        get_separator("gpu-that-does-not-exist")


# --- the local CPU backend, for real ----------------------------------------


@pytest.fixture(scope="module")
def local_result(tmp_path_factory: pytest.TempPathFactory):
    """One real separation, shared by the assertions below.

    Demucs loads an 84MB model before it does anything, so running it once per
    assertion would put half a minute into every test run for no extra coverage.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg encodes the stems")
    pytest.importorskip("demucs", reason="demucs is the phase 0 separation engine")

    tmp = tmp_path_factory.mktemp("local-separation")
    source = synth(tmp / "clip.wav", seconds=2.0)
    out = tmp / "out"
    return LocalDemucsSeparator().separate(source, out), out


def test_local_separation_returns_four_encoded_stems(local_result):
    """The acceptance criterion, on the backend that costs nothing to run."""
    result, _ = local_result

    assert set(result.stems) == set(STEM_NAMES)
    for kind, path in result.stems.items():
        assert path.is_file(), f"{kind} was not written"
        assert path.suffix == f".{STEM_FORMAT}"
        assert path.stat().st_size > 0, f"{kind} is empty"


def test_local_separation_uses_no_gpu_seconds(local_result):
    """It runs on the CPU, so billing it any GPU time would corrupt the one
    number that says how much credit is left."""
    result, _ = local_result

    assert result.gpu_seconds is None
    assert result.timings["separation_s"] > 0


def test_the_four_stems_are_different_audio(local_result):
    """Cheap guard against the wiring bug where one file is written four times,
    which looks exactly like success until someone presses "remove vocals"."""
    result, _ = local_result

    contents = {path.read_bytes() for path in result.stems.values()}

    assert len(contents) == 4, "the stems are not four distinct pieces of audio"


def test_the_intermediate_wavs_are_not_left_behind(local_result):
    """Four uncompressed stems is ~120MB per song; the compressed set is ~15MB."""
    _, out = local_result

    assert not list(out.glob("*.wav")), "uncompressed intermediates survived"


def test_local_separation_reports_a_failure_rather_than_crashing(tmp_path: Path):
    pytest.importorskip("demucs")
    not_audio = tmp_path / "broken.wav"
    not_audio.write_bytes(b"RIFF____WAVEfmt ")

    with pytest.raises(SeparationError):
        LocalDemucsSeparator().separate(not_audio, tmp_path / "out")


# --- the remote backend, against a fake -------------------------------------


class FakeRemote:
    """Stands in for the deployed Modal function, shaped like its real return."""

    def __init__(self, stems: dict[str, bytes], in_container_s: float = 7.5) -> None:
        self._stems = stems
        self._in_container_s = in_container_s
        self.calls: list[tuple[str, int]] = []

    def remote(self, filename: str, data: bytes) -> dict:
        self.calls.append((filename, len(data)))
        return {
            "cold_start": False,
            "stems": self._stems,
            "timings": {
                "boot_to_call_s": 1.1,
                "model_load_s": 2.2,
                "separation_s": 6.2,
                "encode_s": 3.3,
                "in_container_s": self._in_container_s,
            },
        }


@pytest.fixture
def fake_modal(monkeypatch: pytest.MonkeyPatch) -> FakeRemote:
    remote = FakeRemote({name: f"{name}-audio".encode() for name in STEM_NAMES})

    class FakeFunction:
        @staticmethod
        def from_name(app: str, function: str) -> FakeRemote:
            assert (app, function) == ("karuki-separation", "separate")
            return remote

    monkeypatch.setitem(
        __import__("sys").modules, "modal", type("modal", (), {"Function": FakeFunction})
    )
    return remote


def test_the_remote_backend_writes_the_returned_stems(tmp_path: Path, fake_modal: FakeRemote):
    source = tmp_path / "song.wav"
    source.write_bytes(b"pretend audio")

    result = ModalSeparator().separate(source, tmp_path / "out")

    assert set(result.stems) == set(STEM_NAMES)
    assert result.stems["vocals"].read_bytes() == b"vocals-audio"
    assert fake_modal.calls == [("song.wav", len(b"pretend audio"))]


def test_the_remote_backend_reports_gpu_seconds(tmp_path: Path, fake_modal: FakeRemote):
    """Chapter 7: the only way to know how much of the $1 credit is left."""
    source = tmp_path / "song.wav"
    source.write_bytes(b"pretend audio")

    result = ModalSeparator().separate(source, tmp_path / "out")

    assert result.gpu_seconds == 7.5
    assert result.timings["separation_s"] == 6.2


def test_a_remote_failure_becomes_a_separation_error(tmp_path: Path, monkeypatch):
    """Chapter 7 forbids an automatic retry on a GPU step - it costs double
    credit - so the failure has to reach the caller intact."""

    class Exploding:
        @staticmethod
        def from_name(app: str, function: str):
            raise RuntimeError("modal is unreachable")

    monkeypatch.setitem(
        __import__("sys").modules, "modal", type("modal", (), {"Function": Exploding})
    )
    source = tmp_path / "song.wav"
    source.write_bytes(b"pretend audio")

    with pytest.raises(SeparationError):
        ModalSeparator().separate(source, tmp_path / "out")

"""T-1.6 and T-3.3: one call takes a song key and leaves four stems in storage.

The local CPU backend is exercised for real - it is free, which is the point of
it existing. The Modal backend is exercised against a fake: a real call spends
money out of a $1/month credit (docs/phase0/quotas.md), and a test suite that
quietly bills the project is a bad test suite.

What the fake checks is the part that is ours, and since T-3.3 that part is the
*links*. The GPU is handed one signed link to read the source and four to write
the stems; no audio and no credential goes with the call. That is what these
assertions are about.
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
    SeparationUnavailable,
    get_separator,
)
from packages.providers.storage import LocalStorage, StoredObject

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg encodes the stems")

SOURCE_KEY = "songs/abc/normalised.wav"
TARGETS = {name: f"songs/abc/stems/{name}.{STEM_FORMAT}" for name in STEM_NAMES}


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


def test_a_result_missing_a_stem_is_refused():
    """Three of four is not a usable song, and the mixer has four faders."""
    with pytest.raises(SeparationError) as caught:
        Separated(stems={"vocals": StoredObject(key="a", bytes=1)}, backend="fake")

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
    storage = LocalStorage(tmp / "storage")
    storage.put(SOURCE_KEY, synth(tmp / "clip.wav", seconds=2.0))
    return LocalDemucsSeparator().separate(storage, SOURCE_KEY, TARGETS), storage


def test_local_separation_leaves_four_encoded_stems_in_storage(local_result):
    """The acceptance criterion, on the backend that costs nothing to run."""
    result, storage = local_result

    assert set(result.stems) == set(STEM_NAMES)
    for kind, stored in result.stems.items():
        assert stored.key == TARGETS[kind]
        assert storage.exists(stored.key), f"{kind} was not stored"
        assert stored.bytes > 0, f"{kind} is empty"


def test_local_separation_uses_no_gpu_seconds(local_result):
    """It runs on the CPU, so billing it any GPU time would corrupt the one
    number that says how much credit is left."""
    result, _ = local_result

    assert result.gpu_seconds is None
    assert result.timings["separation_s"] > 0


def test_the_four_stems_are_different_audio(local_result):
    """Cheap guard against the wiring bug where one file is written four times,
    which looks exactly like success until someone presses "remove vocals"."""
    _, storage = local_result

    contents = {storage.local_path(key).read_bytes() for key in TARGETS.values()}

    assert len(contents) == 4, "the stems are not four distinct pieces of audio"


def test_no_uncompressed_intermediates_reach_storage(local_result):
    """Four uncompressed stems is ~120MB per song; the compressed set is ~15MB.
    The wavs exist for a moment in a temporary directory and go no further."""
    _, storage = local_result

    stored = [p.name for p in storage.root.rglob("*") if p.is_file()]

    assert [name for name in stored if name.endswith(".wav")] == ["normalised.wav"]


def test_local_separation_reports_a_failure_rather_than_crashing(tmp_path: Path):
    pytest.importorskip("demucs")
    storage = LocalStorage(tmp_path / "storage")
    not_audio = tmp_path / "broken.wav"
    not_audio.write_bytes(b"RIFF____WAVEfmt ")
    storage.put(SOURCE_KEY, not_audio)

    with pytest.raises(SeparationError):
        LocalDemucsSeparator().separate(storage, SOURCE_KEY, TARGETS)


# --- the remote backend, against a fake -------------------------------------


class FakeRemote:
    """Stands in for the deployed Modal function, shaped like its real return."""

    def __init__(self, written: dict[str, int] | None = None, error: str | None = None) -> None:
        self.written = written or dict.fromkeys(STEM_NAMES, 1024)
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def remote(self, source_url: str, stem_urls: dict[str, str]) -> dict:
        self.calls.append((source_url, stem_urls))
        if self.error is not None:
            return {"written": {}, "error": self.error, "timings": {"in_container_s": 1.0}}
        return {
            "cold_start": False,
            "written": self.written,
            "timings": {
                "boot_to_call_s": 1.1,
                "fetch_s": 1.5,
                "model_load_s": 2.2,
                "separation_s": 6.2,
                "encode_s": 3.3,
                "upload_s": 1.9,
                "in_container_s": 7.5,
            },
        }


def install(monkeypatch: pytest.MonkeyPatch, remote: FakeRemote) -> FakeRemote:
    class FakeFunction:
        @staticmethod
        def from_name(app: str, function: str) -> FakeRemote:
            assert (app, function) == ("karuki-separation", "separate_to_storage")
            return remote

    monkeypatch.setitem(
        __import__("sys").modules, "modal", type("modal", (), {"Function": FakeFunction})
    )
    return remote


@pytest.fixture
def cloud(tmp_path: Path) -> LocalStorage:
    """Storage that hands out absolute links, as an object store does."""
    return LocalStorage(tmp_path / "storage", secret="s", base_url="https://storage.example")


@pytest.fixture
def fake_modal(monkeypatch: pytest.MonkeyPatch) -> FakeRemote:
    return install(monkeypatch, FakeRemote())


def test_the_gpu_is_given_links_and_never_the_audio(cloud: LocalStorage, fake_modal: FakeRemote):
    """T-3.3. Phase 0 sent 23MB in the call and got 15MB back; this sends two
    kinds of URL and a filename is not even among them."""
    ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)

    source_url, stem_urls = fake_modal.calls[0]
    assert source_url.startswith("https://storage.example/")
    assert set(stem_urls) == set(STEM_NAMES)
    for name, url in stem_urls.items():
        assert TARGETS[name] in url
        assert "sig=" in url


def test_an_upload_link_is_not_the_same_as_a_download_link(
    cloud: LocalStorage, fake_modal: FakeRemote
):
    """The GPU writes with these. A link that also read would be a wider grant
    than the job needs, and the method is inside the signature for that reason."""
    ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)

    _, stem_urls = fake_modal.calls[0]
    readable = cloud.signed_url(TARGETS["vocals"], 3600)

    assert stem_urls["vocals"].split("sig=")[1] != readable.split("sig=")[1]


def test_the_remote_backend_records_where_the_stems_went(
    cloud: LocalStorage, fake_modal: FakeRemote
):
    """It never sees the bytes, so the keys it asked for are what it reports."""
    result = ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)

    assert {name: stored.key for name, stored in result.stems.items()} == TARGETS
    assert result.stems["vocals"].bytes == 1024


def test_the_remote_backend_reports_gpu_seconds(cloud: LocalStorage, fake_modal: FakeRemote):
    """Chapter 7: the only way to know how much of the $1 credit is left."""
    result = ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)

    assert result.gpu_seconds == 7.5
    assert result.timings["separation_s"] == 6.2


def test_a_remote_run_that_reports_an_error_is_a_separation_error(
    cloud: LocalStorage, monkeypatch: pytest.MonkeyPatch
):
    """The GPU returns its failures as a value rather than raising, because on
    Modal a raised exception is a retry-shaped event and chapter 7 forbids an
    automatic retry of a step that costs credit."""
    install(monkeypatch, FakeRemote(error="RuntimeError: ffmpeg failed for bass"))

    with pytest.raises(SeparationError, match="ffmpeg failed"):
        ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)


def test_a_remote_run_missing_a_stem_is_refused(
    cloud: LocalStorage, monkeypatch: pytest.MonkeyPatch
):
    install(monkeypatch, FakeRemote(written={"vocals": 10, "drums": 10, "bass": 10}))

    with pytest.raises(SeparationError, match="other"):
        ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)


def test_an_unreachable_call_becomes_a_separation_error(
    cloud: LocalStorage, monkeypatch: pytest.MonkeyPatch
):
    class Exploding:
        @staticmethod
        def from_name(app: str, function: str):
            raise RuntimeError("modal is unreachable")

    monkeypatch.setitem(
        __import__("sys").modules, "modal", type("modal", (), {"Function": Exploding})
    )

    with pytest.raises(SeparationError):
        ModalSeparator().separate(cloud, SOURCE_KEY, TARGETS)


def test_links_only_this_machine_can_open_are_refused_before_the_call(
    tmp_path: Path, fake_modal: FakeRemote
):
    """A root-relative link means "this API", which from a rented container is
    nothing at all. Better a clear message than a timeout inside a paid call."""
    local_only = LocalStorage(tmp_path / "storage", secret="s")

    with pytest.raises(SeparationUnavailable, match="absolute links"):
        ModalSeparator().separate(local_only, SOURCE_KEY, TARGETS)

    assert fake_modal.calls == [], "nothing should have been sent"

"""The storage seam (packages/providers/storage.py).

These tests are about the contract the object store has to satisfy too, not
about the filesystem: keys, overwrite, prefix deletion, refusing a key that
would escape the root - and, since T-3.1, links that expire.
"""

import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from packages.providers.storage import (
    LocalStorage,
    SignatureError,
    StorageError,
    get_storage,
)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def source_file(tmp_path: Path, name: str = "src.bin", data: bytes = b"audio") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_put_stores_under_the_key_and_reports_its_size(storage: LocalStorage, tmp_path: Path):
    stored = storage.put("songs/abc/original.mp3", source_file(tmp_path, data=b"12345"))

    assert stored.key == "songs/abc/original.mp3"
    assert stored.bytes == 5
    assert storage.exists("songs/abc/original.mp3")


def test_put_replaces_an_existing_object(storage: LocalStorage, tmp_path: Path):
    storage.put("k/a.bin", source_file(tmp_path, "one.bin", b"first"))
    storage.put("k/a.bin", source_file(tmp_path, "two.bin", b"second-and-longer"))

    assert storage.local_path("k/a.bin").read_bytes() == b"second-and-longer"


def test_local_path_gives_a_real_file_for_ffmpeg(storage: LocalStorage, tmp_path: Path):
    storage.put("k/a.wav", source_file(tmp_path, data=b"riff"))

    assert storage.local_path("k/a.wav").is_file()


def test_local_path_on_a_missing_object_is_an_error(storage: LocalStorage):
    with pytest.raises(StorageError):
        storage.local_path("nope/missing.wav")


def test_delete_prefix_removes_a_whole_song(storage: LocalStorage, tmp_path: Path):
    """Chapter 9's deletion policy frees a song's files together."""
    for name in ("original.mp3", "normalised.wav", "stems/vocals.opus"):
        storage.put(f"songs/one/{name}", source_file(tmp_path, name.replace("/", "_")))
    storage.put("songs/two/original.mp3", source_file(tmp_path, "other.mp3"))

    removed = storage.delete_prefix("songs/one")

    assert removed == 3
    assert not storage.exists("songs/one/original.mp3")
    assert storage.exists("songs/two/original.mp3"), "deleted the wrong song"


def test_deleting_a_prefix_that_is_not_there_is_not_an_error(storage: LocalStorage):
    assert storage.delete_prefix("songs/never-existed") == 0


@pytest.mark.parametrize(
    "key",
    ["../escape.bin", "songs/../../escape.bin", "/absolute.bin", "songs//double.bin", ""],
)
def test_a_key_cannot_escape_the_root(storage: LocalStorage, tmp_path: Path, key: str):
    """Keys are derived from user input. On an object store `..` is just a
    character; on a filesystem it is a way out of the bucket."""
    with pytest.raises(StorageError):
        storage.put(key, source_file(tmp_path))


def test_the_root_is_created_if_it_does_not_exist(tmp_path: Path):
    root = tmp_path / "deep" / "nested" / "storage"

    LocalStorage(root)

    assert root.is_dir()


def signed(storage: LocalStorage, key: str, expires_in: int = 60) -> tuple[str, str, str]:
    """A signed URL, taken apart into what the endpoint receives."""
    query = parse_qs(urlparse(storage.signed_url(key, expires_in)).query)
    return key, query["expires"][0], query["sig"][0]


def test_a_signed_link_opens_the_object(storage: LocalStorage, tmp_path: Path):
    storage.put("songs/a/vocals.mp3", source_file(tmp_path, data=b"sung"))

    assert storage.open_signed(*signed(storage, "songs/a/vocals.mp3")).read_bytes() == b"sung"


def test_a_link_that_has_run_out_does_not_open_it(storage: LocalStorage, tmp_path: Path):
    storage.put("songs/a/vocals.mp3", source_file(tmp_path))

    with pytest.raises(SignatureError):
        storage.open_signed(*signed(storage, "songs/a/vocals.mp3", expires_in=-1))


def test_the_deadline_cannot_be_moved(storage: LocalStorage, tmp_path: Path):
    """It is inside the signed message, so editing it invalidates the link
    rather than extending it. The whole of T-3.1 rests on this."""
    storage.put("songs/a/vocals.mp3", source_file(tmp_path))
    key, expires, signature = signed(storage, "songs/a/vocals.mp3")

    with pytest.raises(SignatureError):
        storage.open_signed(key, str(int(expires) + 86_400), signature)


def test_a_signature_from_another_deployment_does_not_open_it(tmp_path: Path):
    """Two instances with different secrets must not honour each other's links -
    which is also what makes an unset secret safe rather than convenient."""
    mine = LocalStorage(tmp_path / "storage", secret="mine")
    theirs = LocalStorage(tmp_path / "storage", secret="theirs")
    mine.put("songs/a/vocals.mp3", source_file(tmp_path))

    with pytest.raises(SignatureError):
        mine.open_signed(*signed(theirs, "songs/a/vocals.mp3"))


def test_a_signed_link_cannot_carry_a_key_out_of_the_root(storage: LocalStorage):
    """The key on an incoming request is outright untrusted, whatever it is
    signed with."""
    with pytest.raises(StorageError):
        storage.open_signed("../../secrets.txt", str(int(time.time()) + 60), "whatever")


def test_the_url_points_at_the_public_base_when_there_is_one(tmp_path: Path):
    storage = LocalStorage(tmp_path / "storage", secret="s", base_url="https://karuki.example/")

    url = storage.signed_url("songs/a/vocals.mp3", 60)

    assert url.startswith("https://karuki.example/api/v1/files/songs/a/vocals.mp3?")


def test_an_unset_secret_is_random_rather_than_absent(tmp_path: Path):
    """An empty secret must not mean a signature anyone can compute."""
    one = LocalStorage(tmp_path / "one")
    two = LocalStorage(tmp_path / "two")

    assert one.secret and one.secret != two.secret


def test_the_backend_is_chosen_by_name(tmp_path: Path):
    assert isinstance(get_storage("local", root=tmp_path), LocalStorage)


def test_an_unknown_backend_is_refused_rather_than_guessed(tmp_path: Path):
    with pytest.raises(StorageError, match="unknown storage backend"):
        get_storage("r2", root=tmp_path)

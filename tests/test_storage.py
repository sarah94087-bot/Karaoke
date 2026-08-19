"""The storage seam (packages/providers/storage.py).

D-12 is deferred, so phase 1 writes to disk. These tests are about the contract
an object store will have to satisfy, not about the filesystem: keys, overwrite,
prefix deletion, and refusing a key that would escape the root.
"""

from pathlib import Path

import pytest

from packages.providers.storage import LocalStorage, StorageError


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

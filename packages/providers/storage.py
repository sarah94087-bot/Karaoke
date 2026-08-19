"""The storage seam.

D-12 (which object store) is deferred to phase 3: Cloudflare R2 was rejected for
requiring a payment method, and no replacement is chosen. Phase 1 therefore
writes to a local directory, behind the same interface the object store will
implement, so that the decision costs an afternoon rather than a rewrite. This
is also what chapter 11 asks for outright - everything the system does must be
possible to run locally.

Keys look like `songs/<song_id>/original.mp3`, i.e. object-store keys, not paths.
LocalStorage maps them onto directories; an S3-compatible backend will not have
to.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    bytes: int


class Storage(Protocol):
    """What the rest of the system is allowed to assume about storage.

    Deliberately small. Every method here has an obvious S3/R2 equivalent; a
    method that does not would be the thing that makes the provider swap hard.
    """

    def put(self, key: str, source: Path) -> StoredObject:
        """Store the contents of `source` under `key`, replacing any existing object."""
        ...

    def delete_prefix(self, prefix: str) -> int:
        """Remove every object under `prefix`. Returns how many were removed."""
        ...

    def exists(self, key: str) -> bool: ...

    def local_path(self, key: str) -> Path:
        """A real file for the object, for handing to ffmpeg or Demucs.

        On an object store this will mean downloading to a temporary file; the
        callers are written as if it always does.
        """
        ...


def _validate(key: str) -> str:
    """Keys are object keys, so the traversal a path would allow is not allowed.

    Worth being strict here rather than trusting callers: a key derived from an
    uploaded filename is user input, and `..` in it would escape the root.
    """
    if not key or key.startswith("/") or "\\" in key:
        raise StorageError(f"invalid storage key: {key!r}")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StorageError(f"invalid storage key: {key!r}")
    return key


@dataclass
class LocalStorage:
    """Phase 1's implementation: a directory on disk."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _validate(key)

    def put(self, key: str, source: Path) -> StoredObject:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # move when we can: the caller's file is a temporary this takes over,
        # and copying a 30MB wav for no reason is a slow way to be tidy.
        try:
            shutil.move(str(source), destination)
        except OSError:
            shutil.copy2(source, destination)
        return StoredObject(key=key, bytes=destination.stat().st_size)

    def delete_prefix(self, prefix: str) -> int:
        target = self._path(prefix)
        if not target.exists():
            return 0
        removed = sum(1 for p in target.rglob("*") if p.is_file())
        shutil.rmtree(target)
        return removed

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def local_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"no such object: {key}")
        return path

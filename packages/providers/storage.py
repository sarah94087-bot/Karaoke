"""The storage seam.

D-12 (which object store) is decided in phase 3: Cloudflare R2 was rejected for
requiring a payment method (`docs/phase0/quotas.md`), and Backblaze B2 - which
speaks the S3 API - is the replacement. `storage_s3.py` is that backend; this
module holds the contract, the local implementation, and the signing both of
them share.

Keys look like `songs/<song_id>/original.mp3`, i.e. object-store keys, not paths.
LocalStorage maps them onto directories; the S3 backend does not have to.

**Reading is always through a URL that expires** (T-3.1). On S3 that is a
presigned GET, computed from the credentials and never touching the network. On
disk there is no such thing, so `LocalStorage` signs the same promise itself -
an HMAC over the key and the expiry - and `apps/api/routers/files.py` is what
checks it. That keeps chapter 11's rule (everything runs locally) true without
making the local path the weaker one: a stem is unreachable without a live
signature in both.
"""

import hashlib
import hmac
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

# What an object's bytes are, by key suffix. It matters on both backends and for
# a reason a local run hides: an object store keeps the Content-Type it was
# given at upload time and serves it back forever, so a wrong one is written
# into the bucket rather than corrected on the way out.
CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}

DEFAULT_CONTENT_TYPE = "application/octet-stream"


def content_type(key: str) -> str:
    suffix = key[key.rfind(".") :].lower() if "." in key else ""
    return CONTENT_TYPES.get(suffix, DEFAULT_CONTENT_TYPE)


class StorageError(RuntimeError):
    pass


class SignatureError(StorageError):
    """A link that was not signed by us, or that has run out."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    bytes: int


class Storage(Protocol):
    """What the rest of the system is allowed to assume about storage.

    Deliberately small. Every method here has an obvious S3 equivalent; a method
    that does not would be the thing that makes the provider swap hard.
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

        On an object store this means downloading to a temporary file; the
        callers are written as if it always does.
        """
        ...

    def signed_url(self, key: str, expires_in: int) -> str:
        """A URL that serves the object and stops working after `expires_in` seconds."""
        ...


def validate_key(key: str) -> str:
    """Keys are object keys, so the traversal a path would allow is not allowed.

    Worth being strict here rather than trusting callers: a key derived from an
    uploaded filename is user input, and `..` in it would escape the root. The
    same check guards the *incoming* key on a signed request, where it is
    outright untrusted.
    """
    if not key or key.startswith("/") or "\\" in key:
        raise StorageError(f"invalid storage key: {key!r}")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StorageError(f"invalid storage key: {key!r}")
    return key


def sign(secret: str, key: str, expires: int) -> str:
    """The signature carried by a LocalStorage URL.

    The expiry is *inside* the signed message, which is the whole point: a
    client that edits `expires=` in the address bar invalidates the signature it
    was given rather than extending it.
    """
    message = f"{key}\n{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def check_signature(secret: str, key: str, expires: str, signature: str, *, now: int) -> None:
    """Raise unless `signature` is ours and the link has not run out."""
    try:
        deadline = int(expires)
    except (TypeError, ValueError) as exc:
        raise SignatureError("malformed expiry") from exc
    if not hmac.compare_digest(sign(secret, key, deadline), signature or ""):
        raise SignatureError("bad signature")
    # Checked after the signature on purpose: answering "expired" to an
    # unsigned guess would confirm that the key exists.
    if deadline <= now:
        raise SignatureError("link expired")


def _default_secret() -> str:
    """A per-process secret when none is configured.

    Deliberately not a constant: an unset secret must not mean a signature
    anyone can compute. The cost is that URLs handed out before a restart stop
    working, which a player recovers from by re-reading the song. Chapter 9
    budgets one instance, so this is a local-development default rather than a
    production one - set KARUKI_SIGNING_SECRET there.
    """
    return os.getenv("KARUKI_SIGNING_SECRET") or secrets.token_hex(32)


@dataclass
class LocalStorage:
    """A directory on disk, for local development and for the container.

    `base_url` is prepended to the signed URLs. Empty means a root-relative URL,
    which is what the web app already knows how to resolve, and which keeps the
    address correct whether the API is reached on localhost or on 127.0.0.1.
    """

    root: Path
    secret: str = field(default_factory=_default_secret)
    base_url: str = ""

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = self.base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

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

    def signed_url(self, key: str, expires_in: int) -> str:
        expires = int(time.time()) + int(expires_in)
        signature = sign(self.secret, validate_key(key), expires)
        path = quote(key, safe="/")
        return f"{self.base_url}/api/v1/files/{path}?expires={expires}&sig={signature}"

    def open_signed(self, key: str, expires: str, signature: str) -> Path:
        """The read half of `signed_url`, for the endpoint that serves it."""
        checked = validate_key(key)
        check_signature(self.secret, checked, expires, signature, now=int(time.time()))
        return self.local_path(checked)


def get_storage(
    backend: str,
    *,
    root: Path,
    secret: str = "",
    base_url: str = "",
    s3: "object | None" = None,
) -> Storage:
    """Pick a backend by name, the way the separation and lyrics seams do.

    `local` stays the default. Unlike the separation backend, the reason is not
    cost - B2 is free either way - but that a machine with no credentials must
    still be able to run the whole product, which is chapter 11's rule.
    """
    if backend == "local":
        return LocalStorage(root, secret=secret or _default_secret(), base_url=base_url)
    if backend == "s3":
        from .storage_s3 import S3Config, S3Storage

        if not isinstance(s3, S3Config):
            raise StorageError("the s3 backend needs an S3Config")
        return S3Storage(s3)
    raise StorageError(f"unknown storage backend: {backend!r}")

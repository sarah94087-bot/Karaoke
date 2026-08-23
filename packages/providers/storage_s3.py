"""The object store (D-12), spoken to over the S3 API.

Backblaze B2 is the provider: 10GB free, and - unlike Cloudflare R2, which
phase 0 rejected - the free tier is reachable without a payment method. Nothing
here is B2-specific, though; it is plain SigV4 against a path-style endpoint, so
Storj or any other S3-compatible store is a change of four environment
variables.

**No boto3.** The API image is on a free tier and boto3 with botocore is tens of
megabytes of it, for four verbs. Everything below is `hashlib`, `hmac` and
`urllib` - the same choice, for the same reason, as the hand-built multipart
body in `transcription.py`.

The one part worth reading twice is `presign`: a signed GET is *computed*, not
requested, so handing a player four stem URLs costs zero round trips to B2. That
is what makes T-3.1's "read only through a URL that expires" cheap enough to do
on every song open.
"""

import base64
import datetime as dt
import hashlib
import hmac
import logging
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .net import USER_AGENT, trust_system_certificates
from .storage import StorageError, StoredObject, content_type, validate_key

log = logging.getLogger("karuki.storage")

ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"
# A presigned GET signs no body, which is what lets it be a plain browser
# request. The header-signed calls below hash their real payload.
UNSIGNED = "UNSIGNED-PAYLOAD"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint", self.endpoint.rstrip("/"))
        missing = [
            name
            for name in ("endpoint", "bucket", "region", "access_key_id", "secret_access_key")
            if not getattr(self, name)
        ]
        if missing:
            raise StorageError(f"object storage is not configured: missing {', '.join(missing)}")


def _quote_path(value: str) -> str:
    """S3 wants each path segment percent-encoded, and the separators left alone."""
    return quote(value, safe="/~")


def _quote_query(value: str) -> str:
    return quote(str(value), safe="-_.~")


def _canonical_query(params: dict[str, str]) -> str:
    return "&".join(f"{_quote_query(k)}={_quote_query(v)}" for k, v in sorted(params.items()))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    """The payload hash and the size, without holding the file in memory.

    SigV4 signs a hash of the body, which is why an upload cannot simply be
    streamed blind - but it can be read twice: once to hash, once to send.
    """
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    initial = f"AWS4{secret}".encode()
    return _hmac(_hmac(_hmac(_hmac(initial, date), region), SERVICE), "aws4_request")


def _string_to_sign(stamp: str, scope: str, canonical_request: str) -> str:
    return "\n".join([ALGORITHM, stamp, scope, _sha256(canonical_request.encode())])


@dataclass
class S3Storage:
    """The Storage protocol, against an S3-compatible endpoint.

    `opener` is the seam the tests use: every call in this class goes through
    it, so the suite exercises the real signing and the real request while
    spending nothing and reaching nobody. The same rule the GPU and the
    transcription providers have.
    """

    config: S3Config
    opener: Callable[..., Any] = urllib.request.urlopen
    # Generous on purpose. A four-minute song is a 47MB normalised wav, and at
    # the throughput measured to eu-central-003 the old 30s default timed out
    # part-way through the first real song - which surfaced as a 500 from our
    # own API on an upload that was working exactly as designed, only slowly.
    timeout: float = 300.0
    # Storage services are allowed transient failures, and the S3 API documents
    # these as retryable. B2 answered one live PUT with a 500. Retrying a
    # transfer is not the automatic retry chapter 7 forbids - that one is a
    # second GPU call, and it costs credit; this costs a few seconds.
    attempts: int = 4
    _cache: dict[str, Path] = field(default_factory=dict, repr=False)
    _cache_dir: Path | None = field(default=None, repr=False)

    # -- signing -------------------------------------------------------------

    def _host(self) -> str:
        return self.config.endpoint.split("://", 1)[-1].split("/", 1)[0]

    def _uri(self, key: str) -> str:
        """Path-style addressing: /<bucket>/<key>, or /<bucket> for the bucket itself.

        Virtual-host style would need the bucket name to be DNS-safe and the
        endpoint to be rewritten per provider; path style is one string and both
        B2 and Storj accept it.
        """
        return f"/{_quote_path(self.config.bucket)}" + (f"/{_quote_path(key)}" if key else "")

    def presign(
        self,
        key: str,
        expires_in: int,
        *,
        method: str = "GET",
        now: dt.datetime | None = None,
    ) -> str:
        """A URL for one method on one key that stops working after `expires_in`.

        Query-string authentication, so the browser needs no headers: loading a
        stem stays an ordinary request a `fetch` or an `<audio>` element can
        make, and uploading one is an ordinary `PUT` with the file as the body.

        The method is part of the canonical request, so a read link is not a
        write link - S3 gets this right for the same reason the local backend
        had to be taught it.
        """
        moment = now or dt.datetime.now(dt.UTC)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        date = stamp[:8]
        scope = f"{date}/{self.config.region}/{SERVICE}/aws4_request"
        params = {
            "X-Amz-Algorithm": ALGORITHM,
            "X-Amz-Credential": f"{self.config.access_key_id}/{scope}",
            "X-Amz-Date": stamp,
            "X-Amz-Expires": str(int(expires_in)),
            "X-Amz-SignedHeaders": "host",
        }
        uri = self._uri(validate_key(key))
        canonical = "\n".join(
            [
                method.upper(),
                uri,
                _canonical_query(params),
                f"host:{self._host()}\n",
                "host",
                UNSIGNED,
            ]
        )
        params["X-Amz-Signature"] = hmac.new(
            _signing_key(self.config.secret_access_key, date, self.config.region),
            _string_to_sign(stamp, scope, canonical).encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{self.config.endpoint}{uri}?{_canonical_query(params)}"

    def _signed_headers(
        self,
        method: str,
        uri: str,
        params: dict[str, str],
        body: bytes,
        *,
        now: dt.datetime | None = None,
        payload_hash: str | None = None,
    ) -> dict[str, str]:
        moment = now or dt.datetime.now(dt.UTC)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        date = stamp[:8]
        scope = f"{date}/{self.config.region}/{SERVICE}/aws4_request"
        payload_hash = payload_hash or _sha256(body)
        headers = {
            "host": self._host(),
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": stamp,
        }
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        canonical = "\n".join(
            [method, uri, _canonical_query(params), canonical_headers, signed, payload_hash]
        )
        signature = hmac.new(
            _signing_key(self.config.secret_access_key, date, self.config.region),
            _string_to_sign(stamp, scope, canonical).encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["Authorization"] = (
            f"{ALGORITHM} Credential={self.config.access_key_id}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        )
        headers["User-Agent"] = USER_AGENT
        return headers

    # -- requests ------------------------------------------------------------

    RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})

    def _call(
        self,
        method: str,
        key: str,
        *,
        params: dict[str, str] | None = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        source: Path | None = None,
    ) -> bytes:
        """One request, retried on the failures a storage service is allowed to have.

        `source` uploads a file without reading it into memory: the hash is
        computed in chunks and the open handle is the request body. The API runs
        on a free tier with a few hundred MB of RAM, and a 47MB `bytes` per
        upload is the kind of thing that works until two songs arrive at once.
        """
        trust_system_certificates()
        params = params or {}
        uri = self._uri(key)
        url = f"{self.config.endpoint}{uri}"
        if params:
            url = f"{url}?{_canonical_query(params)}"
        what = key or self.config.bucket

        if source is not None:
            payload_hash, size = _sha256_file(source)
        else:
            payload_hash, size = _sha256(body), len(body)

        last: Exception | None = None
        for attempt in range(self.attempts):
            signed = self._signed_headers(method, uri, params, b"", payload_hash=payload_hash)
            extra = dict(headers or {})
            if source is not None:
                extra["Content-Length"] = str(size)
            handle = source.open("rb") if source is not None else None
            request = urllib.request.Request(
                url,
                data=handle if handle is not None else (body or None),
                method=method,
                headers={**signed, **extra},
            )
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    return bytes(response.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:400].decode("utf-8", "replace")
                last = StorageError(f"{method} {what} failed: {exc.code} {detail}")
                if exc.code not in self.RETRYABLE:
                    raise last from exc
            except OSError as exc:
                last = StorageError(f"{method} {what} failed: {exc}")
            finally:
                if handle is not None:
                    handle.close()
            if attempt < self.attempts - 1:
                log.warning("%s %s failed, retrying: %s", method, what, last)
                time.sleep(2**attempt)
        raise last if last is not None else StorageError(f"{method} {what} failed")

    # -- the protocol --------------------------------------------------------

    def put(self, key: str, source: Path) -> StoredObject:
        checked = validate_key(key)
        source = Path(source)
        size = source.stat().st_size
        # The content type is named explicitly, because urllib's default for a
        # request with a body is `application/x-www-form-urlencoded` - and a
        # live run showed B2 storing exactly that on four stems. It is not a
        # header the signature covers, so nothing else would have complained.
        self._call("PUT", checked, headers={"Content-Type": content_type(checked)}, source=source)
        stale = self._cache.pop(checked, None)
        if stale is not None:
            # The key was replaced - re-running separation does exactly this -
            # so the copy this process downloaded earlier is now the wrong audio.
            stale.unlink(missing_ok=True)
        return StoredObject(key=checked, bytes=size)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            params = {"list-type": "2", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            root = ET.fromstring(self._call("GET", "", params=params).decode("utf-8"))
            keys += [node.text or "" for node in root.findall("s3:Contents/s3:Key", NS)]
            truncated = (root.findtext("s3:IsTruncated", "false", NS) or "").lower() == "true"
            token = root.findtext("s3:NextContinuationToken", None, NS)
            if not truncated or not token:
                return keys

    def delete_prefix(self, prefix: str) -> int:
        removed = 0
        for key in self.list_keys(validate_key(prefix)):
            self._call("DELETE", key)
            self._cache.pop(key, None)
            removed += 1
        return removed

    def exists(self, key: str) -> bool:
        try:
            self._call("HEAD", validate_key(key))
        except StorageError:
            return False
        return True

    def local_path(self, key: str) -> Path:
        """Download once per process, because ffmpeg and Demucs want a real file.

        One job asks for the same normalised wav several times; paying for that
        download once is the difference between a step and a stall.
        """
        checked = validate_key(key)
        cached = self._cache.get(checked)
        if cached is not None and cached.is_file():
            return cached
        if self._cache_dir is None:
            self._cache_dir = Path(tempfile.mkdtemp(prefix="karuki-s3-"))
        target = self._cache_dir / hashlib.sha256(checked.encode()).hexdigest()[:16]
        target.write_bytes(self._call("GET", checked))
        self._cache[checked] = target
        return target

    def signed_url(self, key: str, expires_in: int) -> str:
        return self.presign(key, expires_in)

    # -- the bucket itself ---------------------------------------------------

    def set_cors(self, origins: list[str], *, max_age: int = 3600) -> None:
        """Let those origins upload and download directly (T-3.2).

        Without this the browser refuses before it ever asks B2: a presigned URL
        is perfectly valid and the fetch is still cross-origin. `PUT` is here
        for the upload and `GET`/`HEAD` for the player, and the origins are the
        API's own `KARUKI_CORS_ORIGINS`, so the two lists cannot drift.

        In code rather than in the console because a rule nobody can re-apply is
        a rule that gets lost at the next deployment.
        """
        rules = "".join(
            f"<CORSRule><AllowedOrigin>{origin}</AllowedOrigin>"
            "<AllowedMethod>GET</AllowedMethod>"
            "<AllowedMethod>HEAD</AllowedMethod>"
            "<AllowedMethod>PUT</AllowedMethod>"
            "<AllowedHeader>*</AllowedHeader>"
            "<ExposeHeader>ETag</ExposeHeader>"
            f"<MaxAgeSeconds>{int(max_age)}</MaxAgeSeconds></CORSRule>"
            for origin in origins
        )
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?><CORSConfiguration>{rules}</CORSConfiguration>'
        )
        self._call(
            "PUT",
            "",
            params={"cors": ""},
            body=body.encode(),
            # Required on this subresource by S3 and by B2. Not part of the
            # signature, so a missing one fails as a 400 rather than a 403.
            headers={
                "Content-Type": "application/xml",
                "Content-MD5": base64.b64encode(hashlib.md5(body.encode()).digest()).decode(),
            },
        )

    def get_cors(self) -> list[str]:
        """The origins the bucket currently allows, for reading back a change."""
        try:
            raw = self._call("GET", "", params={"cors": ""})
        except StorageError:
            return []
        root = ET.fromstring(raw.decode("utf-8"))
        return [
            node.text or ""
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "AllowedOrigin"
        ]

    def signed_upload_url(self, key: str, expires_in: int) -> str:
        """Where the browser PUTs the file (T-3.2).

        The bytes never touch the API. What the API keeps is the right to say
        *which* key may be written and *for how long* - that is the whole of the
        authorisation, and it is why this is computed here rather than by
        handing the browser a credential.
        """
        return self.presign(key, expires_in, method="PUT")

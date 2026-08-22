"""The object-store backend (packages/providers/storage_s3.py).

**No test reaches the network**, the same rule the GPU and the transcription
providers have - which here also means no test needs an account or a bucket.
Every call goes through the injected opener, so the suite asserts the request
this code would really have sent: the method, the URL, the payload hash and the
Authorization header.

What these tests cannot prove is that B2 *accepts* the signature; only a live
call can, and that is what the task's live verification is for. What they do
prove is the part a live call would hide: that the expiry is inside the signed
material, so a link cannot be extended by editing it.
"""

import datetime as dt
import io
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from packages.providers.storage import StorageError
from packages.providers.storage_s3 import S3Config, S3Storage

CONFIG = S3Config(
    endpoint="https://s3.us-west-004.backblazeb2.com",
    bucket="karuki-songs",
    region="us-west-004",
    access_key_id="004abcdef0000000000000001",
    secret_access_key="K004xxxxxxxxxxxxxxxxxxxxxxxxxxx",
)

NOON = dt.datetime(2026, 8, 22, 12, 0, 0, tzinfo=dt.UTC)

LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents><Key>songs/one/original.mp3</Key></Contents>
  <Contents><Key>songs/one/stems/vocals.mp3</Key></Contents>
</ListBucketResult>"""


class Response(io.BytesIO):
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FakeOpener:
    """Records every request and answers with whatever it was queued."""

    def __init__(self, *bodies: bytes) -> None:
        self.requests: list[object] = []
        self.bodies = list(bodies) or [b""]

    def __call__(self, request: object, timeout: float = 0) -> Response:
        self.requests.append(request)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return Response(body)

    @property
    def last(self) -> object:
        return self.requests[-1]


def store(*bodies: bytes) -> tuple[S3Storage, FakeOpener]:
    opener = FakeOpener(*bodies)
    return S3Storage(CONFIG, opener=opener), opener


# -- presigning --------------------------------------------------------------


def test_a_presigned_url_carries_everything_s3_needs_and_nothing_else():
    url = S3Storage(CONFIG).presign("songs/a/vocals.mp3", 3600, now=NOON)

    parts = urlparse(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "s3.us-west-004.backblazeb2.com"
    assert parts.path == "/karuki-songs/songs/a/vocals.mp3", "path-style addressing"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["X-Amz-SignedHeaders"] == ["host"], "a browser sends no signed headers"
    assert query["X-Amz-Credential"] == [
        f"{CONFIG.access_key_id}/20260822/us-west-004/s3/aws4_request"
    ]
    assert len(query["X-Amz-Signature"][0]) == 64


def test_presigning_is_computed_and_never_calls_the_service():
    """Four stems per song open, so a round trip per link would be a round trip
    too many."""
    storage, opener = store()

    storage.signed_url("songs/a/vocals.mp3", 60)

    assert opener.requests == []


def _signature(url: str) -> str:
    return parse_qs(urlparse(url).query)["X-Amz-Signature"][0]


def test_the_expiry_is_inside_the_signature():
    """T-3.1's actual promise: a link cannot be extended by editing it."""
    storage = S3Storage(CONFIG)

    assert _signature(storage.presign("k/a.mp3", 60, now=NOON)) != _signature(
        storage.presign("k/a.mp3", 86400, now=NOON)
    )


def test_a_different_key_is_a_different_signature():
    storage = S3Storage(CONFIG)

    assert _signature(storage.presign("k/a.mp3", 60, now=NOON)) != _signature(
        storage.presign("k/b.mp3", 60, now=NOON)
    )


def test_the_same_request_signs_the_same_way():
    """Determinism given the clock, which is what makes the rest testable."""
    storage = S3Storage(CONFIG)

    assert storage.presign("k/a.mp3", 60, now=NOON) == storage.presign("k/a.mp3", 60, now=NOON)


def test_a_key_that_would_escape_the_bucket_is_refused():
    with pytest.raises(StorageError):
        S3Storage(CONFIG).presign("../secrets/a.mp3", 60)


# -- writing and reading -----------------------------------------------------


def test_put_sends_the_bytes_and_hashes_them(tmp_path: Path):
    source = tmp_path / "vocals.mp3"
    source.write_bytes(b"audio")
    storage, opener = store()

    stored = storage.put("songs/a/vocals.mp3", source)

    request = opener.last
    assert request.method == "PUT"
    assert request.full_url == (
        "https://s3.us-west-004.backblazeb2.com/karuki-songs/songs/a/vocals.mp3"
    )
    assert request.data == b"audio"
    assert stored.bytes == 5
    # sha256 of b"audio": the payload hash is part of what is signed, so an
    # unsigned-payload shortcut here would be a silently weaker write.
    assert request.headers["X-amz-content-sha256"] == (
        "6ed8919ce20490a5e3ad8630a4fab69475297abd07db73918dd5f36fcfaeb11b"
    )
    assert request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=")


def test_put_names_the_content_type(tmp_path: Path):
    """A live run found four stems in the bucket as
    `application/x-www-form-urlencoded` - urllib's default for a request with a
    body. An object store keeps what it was given and serves it back forever,
    so this is written into the bucket rather than fixed on the way out."""
    source = tmp_path / "vocals.mp3"
    source.write_bytes(b"audio")
    storage, opener = store()

    storage.put("songs/a/vocals.mp3", source)

    assert opener.last.headers["Content-type"] == "audio/mpeg"


def test_an_object_that_is_not_audio_is_handed_over_as_bytes(tmp_path: Path):
    source = tmp_path / "words.json"
    source.write_bytes(b"{}")
    storage, opener = store()

    storage.put("songs/a/words.json", source)

    assert opener.last.headers["Content-type"] == "application/octet-stream"


def test_local_path_downloads_once_and_keeps_the_file(tmp_path: Path):
    """One job asks for the same normalised wav several times."""
    storage, opener = store(b"RIFF....")

    first = storage.local_path("songs/a/normalised.wav")
    second = storage.local_path("songs/a/normalised.wav")

    assert first == second
    assert first.read_bytes() == b"RIFF...."
    assert len(opener.requests) == 1


def test_replacing_an_object_drops_the_copy_this_process_downloaded(tmp_path: Path):
    """A re-run of separation writes the same key with different audio."""
    storage, opener = store(b"first")
    downloaded = storage.local_path("songs/a/vocals.mp3")
    source = tmp_path / "new.mp3"
    source.write_bytes(b"second")

    storage.put("songs/a/vocals.mp3", source)

    assert not downloaded.exists()
    opener.bodies = [b"second"]
    assert storage.local_path("songs/a/vocals.mp3").read_bytes() == b"second"


def test_list_keys_reads_the_listing():
    storage, opener = store(LISTING.encode())

    keys = storage.list_keys("songs/one")

    assert keys == ["songs/one/original.mp3", "songs/one/stems/vocals.mp3"]
    assert parse_qs(urlparse(opener.last.full_url).query)["prefix"] == ["songs/one"]


def test_list_keys_follows_the_continuation_token():
    """A song is a handful of objects, but deletion walks a whole prefix and a
    truncated first page would silently leave files behind - and files left
    behind are the storage quota filling up for no reason."""
    page_one = LISTING.replace(
        "<IsTruncated>false</IsTruncated>",
        "<IsTruncated>true</IsTruncated><NextContinuationToken>more</NextContinuationToken>",
    )
    page_two = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents><Key>songs/one/stems/bass.mp3</Key></Contents>
</ListBucketResult>"""
    storage, opener = store(page_one.encode(), page_two.encode())

    keys = storage.list_keys("songs/one")

    assert keys[-1] == "songs/one/stems/bass.mp3"
    assert len(opener.requests) == 2
    assert parse_qs(urlparse(opener.last.full_url).query)["continuation-token"] == ["more"]


def test_delete_prefix_removes_every_object_it_listed():
    storage, opener = store(LISTING.encode(), b"", b"")

    removed = storage.delete_prefix("songs/one")

    assert removed == 2
    deleted = [r.full_url for r in opener.requests if r.method == "DELETE"]
    assert deleted == [
        "https://s3.us-west-004.backblazeb2.com/karuki-songs/songs/one/original.mp3",
        "https://s3.us-west-004.backblazeb2.com/karuki-songs/songs/one/stems/vocals.mp3",
    ]


def test_exists_is_a_head_request():
    storage, opener = store()

    assert storage.exists("songs/a/vocals.mp3") is True
    assert opener.last.method == "HEAD"


def test_an_object_that_is_not_there_does_not_exist():
    def missing(request: object, timeout: float = 0) -> Response:
        raise urllib.error.HTTPError(
            "https://example", 404, "Not Found", {}, io.BytesIO(b"<Error/>")
        )

    assert S3Storage(CONFIG, opener=missing).exists("songs/a/vocals.mp3") is False


def test_a_refused_request_is_a_storage_error_with_the_service_reply_in_it():
    """The message is what an operator reads when a key is wrong, so it carries
    the status and B2's own explanation rather than a stack trace."""

    def refused(request: object, timeout: float = 0) -> Response:
        raise urllib.error.HTTPError(
            "https://example",
            403,
            "Forbidden",
            {},
            io.BytesIO(b"<Error><Code>SignatureDoesNotMatch</Code></Error>"),
        )

    with pytest.raises(StorageError, match="403.*SignatureDoesNotMatch"):
        S3Storage(CONFIG, opener=refused).local_path("songs/a/vocals.mp3")


# -- the bucket's own settings -----------------------------------------------


def test_set_cors_asks_the_bucket_for_exactly_the_origins_it_was_given():
    """Without this rule the browser refuses before B2 is ever asked: the
    presigned URL is valid and the request is still cross-origin."""
    storage, opener = store()

    storage.set_cors(["http://localhost:3000"], max_age=3600)

    request = opener.last
    body = request.data.decode()
    assert request.full_url.endswith("/karuki-songs?cors=")
    assert "<AllowedOrigin>http://localhost:3000</AllowedOrigin>" in body
    assert "<AllowedMethod>PUT</AllowedMethod>" in body, "the upload"
    assert "<AllowedMethod>GET</AllowedMethod>" in body, "the player"
    assert "<MaxAgeSeconds>3600</MaxAgeSeconds>" in body
    assert request.headers["Content-md5"], "S3 requires it on this subresource"


def test_get_cors_reads_the_origins_back():
    listing = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CORSConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<CORSRule><AllowedOrigin>http://localhost:3000</AllowedOrigin></CORSRule>"
        "</CORSConfiguration>"
    )
    storage, _ = store(listing.encode())

    assert storage.get_cors() == ["http://localhost:3000"]


def test_a_bucket_with_no_rule_reads_as_no_origins():
    """B2 answers a bucket with no CORS configuration with an error, and "none"
    is the honest reading of it - not a crash on the way to setting one."""

    def missing(request: object, timeout: float = 0) -> Response:
        raise urllib.error.HTTPError(
            "https://example", 404, "Not Found", {}, io.BytesIO(b"<Error/>")
        )

    assert S3Storage(CONFIG, opener=missing).get_cors() == []


# -- configuration -----------------------------------------------------------


def test_a_half_configured_bucket_says_which_half_is_missing():
    """A deployment with three of the five values set should fail at startup
    with the names of the other two, not at the first upload with a 403."""
    with pytest.raises(StorageError, match="region, secret_access_key"):
        S3Config(
            endpoint="https://s3.us-west-004.backblazeb2.com",
            bucket="karuki-songs",
            region="",
            access_key_id="004abc",
            secret_access_key="",
        )

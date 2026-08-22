"""Serving a signed link, for the local storage backend.

With the `s3` backend this router is never reached: a presigned URL points at
the object store and the audio never passes through the API at all, which is
what chapter 3 means by "the API never handles audio". Locally there is no
object store to point at, so this is the endpoint the signature was made for.

It takes no session and no user. That is deliberate and it is the whole design
of T-3.1: the *link* carries the authority, for a fixed time, which is exactly
what a presigned URL is. What replaces the old `/songs/{id}/stems/{kind}` is
therefore not a different route to the same file - it is a route that cannot be
guessed, cannot be shared past its expiry, and behaves the same way on both
backends.
"""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse

from packages.providers.storage import (
    LocalStorage,
    SignatureError,
    StorageError,
    content_type,
)

from ..config import settings
from ..deps import StorageDep
from ..errors import ApiError

log = logging.getLogger("karuki.api")

router = APIRouter(tags=["files"])

CHUNK_BYTES = 1024 * 1024


@router.get(
    "/files/{key:path}",
    response_class=FileResponse,
    summary="An object, if the link is still valid",
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "The object."},
        403: {"description": "The signature is wrong, or the link has expired."},
    },
)
async def get_file(
    storage: StorageDep,
    key: str,
    expires: str = Query(description="Unix seconds, part of what is signed."),
    sig: str = Query(description="HMAC over the key and the expiry."),
) -> FileResponse:
    if not isinstance(storage, LocalStorage):  # pragma: no cover - s3 never links here
        raise ApiError("link_invalid", "this deployment does not serve files", status_code=404)

    try:
        path = storage.open_signed(key, expires, sig)
    except SignatureError as exc:
        # One code for both halves. Telling a caller which of "wrong signature"
        # and "expired" they hit is telling them whether the key exists.
        raise ApiError("link_invalid", str(exc), status_code=403) from exc
    except StorageError as exc:
        raise ApiError("file_missing", str(exc), status_code=410) from exc

    return FileResponse(
        path,
        # The same table the object store is given at upload time, so a stem
        # arrives as audio/mpeg on either backend.
        media_type=content_type(key),
        # Cached for as long as the link lives, and no longer: the object itself
        # never changes, but a response cached past the expiry would be served
        # from disk by a link that is no longer valid.
        headers={"Cache-Control": f"private, max-age={settings.signed_url_ttl}"},
    )


@router.put(
    "/files/{key:path}",
    status_code=status.HTTP_201_CREATED,
    summary="Store an object, if the link is still valid",
    responses={
        403: {"description": "The signature is wrong, for another method, or expired."},
        413: {"description": "The body is larger than the upload limit."},
    },
)
async def put_file(
    request: Request,
    storage: StorageDep,
    key: str,
    expires: str = Query(description="Unix seconds, part of what is signed."),
    sig: str = Query(description="HMAC over the method, the key and the expiry."),
) -> dict[str, object]:
    """The local stand-in for a browser PUT straight into the bucket (T-3.2).

    With the `s3` backend the browser never comes here: the signed upload URL
    points at B2 and the bytes do not pass through this process at all. This is
    what keeps that flow runnable on a machine with no bucket, and it is held to
    the same rules - the signature covers the method, so a link handed out to
    read a stem cannot be used to overwrite one.
    """
    if not isinstance(storage, LocalStorage):  # pragma: no cover - s3 never links here
        raise ApiError("link_invalid", "this deployment does not accept uploads here", 404)

    with tempfile.TemporaryDirectory(prefix="karuki-put-") as tmp:
        landing = Path(tmp) / "body"
        written = 0
        with landing.open("wb") as sink:
            async for chunk in request.stream():
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    # Refused as the bytes arrive, which is the same rule
                    # T-1.5's upload has and for the same reason: Content-Length
                    # is a claim, and one instance cannot be talked into holding
                    # a gigabyte.
                    raise ApiError(
                        "file_too_large",
                        f"the file is larger than {settings.max_upload_bytes // (1024 * 1024)}MB",
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    )
                sink.write(chunk)
        if written == 0:
            raise ApiError("empty_file", "the file is empty")

        try:
            stored = storage.accept_signed(key, expires, sig, landing)
        except SignatureError as exc:
            raise ApiError("link_invalid", str(exc), status_code=403) from exc
        except StorageError as exc:
            raise ApiError("invalid_request", str(exc)) from exc

    return {"key": stored.key, "bytes": stored.bytes}

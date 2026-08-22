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

from fastapi import APIRouter, Query
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

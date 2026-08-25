"""Adding a song from a link (D-01, T-4.1).

Phase 4 is specified as a module that can be switched off with one flag, and
this router is the visible half of that: `create_app` only includes it when
`KARUKI_IMPORT` names a resolver, so with the flag off the path is not routed,
not in the OpenAPI document, and not offered by the web app - which asks
`/system/features` rather than being told separately. Nothing else in the API
imports this file.

The bytes land the same way an upload's do. `POST /songs/import` downloads to a
temporary file and hands it to the *same* `_ingest` the two upload routes use,
so normalising, deduplication, the quota and the job are one implementation and
not three. The only things an import adds to a song are where it came from
(`source_type=url`) and a title that arrives from the source rather than from a
file name.

The API carrying these bytes is a deliberate exception to chapter 3's "the API
never handles audio", and a narrow one: there is nobody else to carry them. A
browser cannot fetch a third-party address and PUT it to the bucket - that is
what CORS prevents - and doing it on the GPU function would spend credit on a
download that needs no GPU. One transfer, bounded by the same limit an upload
has, on a request the user is waiting on anyway.
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from packages.core.enums import SourceType
from packages.providers.import_source import Importer, SourceError, SourceUnavailable

from ..config import settings
from ..deps import RunnerDep, SessionDep, StorageDep, UserDep
from ..errors import ApiError

# The upload route's own helpers, on purpose: an import that normalised or
# deduplicated even slightly differently would be a second definition of what a
# song is. Private names, and this is the one module allowed to reach for them.
from .songs import ACCEPTED_SUFFIXES, SongCreated, _ingest, _within_quota

router = APIRouter(tags=["songs"])


class ImportRequest(BaseModel):
    url: str = Field(
        description="An http(s) link. Which links work depends on the resolvers this "
        "deployment has switched on - see GET /system/features.",
        examples=["https://example.com/song.mp3"],
    )


def get_importer(request: Request) -> Importer:
    importer: Importer | None = getattr(request.app.state, "importer", None)
    if importer is None or not importer.enabled:  # pragma: no cover - unrouted when off
        raise ApiError("import_disabled", "importing from a link is turned off here", 404)
    return importer


@router.post(
    "/songs/import",
    response_model=SongCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a song from a link and start the work",
    responses={
        200: {"description": "The same audio was already here; the existing song is returned."}
    },
)
async def import_song(
    request: Request,
    response: Response,
    session: SessionDep,
    storage: StorageDep,
    runner: RunnerDep,
    user_id: UserDep,
    body: ImportRequest,
) -> SongCreated:
    importer = get_importer(request)
    url = body.url.strip()
    if not url:
        raise ApiError("invalid_request", "no link was given")

    # Before the transfer rather than after it, which is what `upload-url` does
    # with the declared size and for the same reason: a song that is going to be
    # refused should be refused before anybody waits for it. The concurrency
    # limit is checked again inside `_ingest`, after the deduplication.
    await _within_quota(session, user_id, concurrency=False)

    with tempfile.TemporaryDirectory(prefix="karuki-import-") as tmp:
        try:
            # In a thread: this is a download of up to the upload limit, and
            # T-3.5 measured what a long blocking read on the event loop costs
            # on the single instance chapter 9 budgets - 39.5 seconds in which
            # nothing else in the process answered, including /system/health.
            imported = await asyncio.to_thread(
                importer.fetch, url, Path(tmp), settings.max_upload_bytes
            )
        except SourceUnavailable as exc:
            # The operator's problem, not the link's (T-1.7's distinction).
            raise ApiError(exc.code, str(exc), status_code=503) from exc
        except SourceError as exc:
            raise ApiError(exc.code, str(exc)) from exc

        if imported.suffix not in ACCEPTED_SUFFIXES:
            raise ApiError(
                "unsupported_format", f"{imported.suffix or 'that file type'} is not supported"
            )

        return await _ingest(
            session,
            storage,
            runner,
            response,
            imported.path,
            user_id=user_id,
            filename=f"{imported.title}{imported.suffix}",
            suffix=imported.suffix,
            title=imported.title,
            # Only the resolvers that read a *page* have this; the `direct` one
            # has a file name and nothing else, and says so by sending None -
            # which is what lets the file's own tags win over it (T-4.2).
            artist=imported.artist,
            source_type=SourceType.URL,
            # The address as the source gave it back, so a redirect chain or a
            # share link is recorded as the thing it actually resolved to.
            source_ref=imported.source_url or url,
        )

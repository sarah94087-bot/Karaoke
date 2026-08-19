"""Songs: for now, getting one into the system (T-1.5).

The rest of chapter 6's song endpoints arrive with the tasks that need them.

Note that this takes the file through the API, which is *not* what chapter 6
describes: there, the browser uploads straight to object storage with a signed
URL, so the API never carries the bytes. That shape depends on D-12, which is
deferred, and on there being an object store at all. Uploading through the API
is the phase 1 stand-in; the seam that makes swapping it cheap is
packages/providers/storage.py, and the response shape here does not depend on
which way the bytes travelled.
"""

import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from packages.audio.normalize import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    AudioError,
    ToolMissing,
    normalise,
)
from packages.core.enums import SongStatus, SourceType
from packages.core.models import Song
from packages.providers.storage import Storage

from ..config import settings
from ..deps import SessionDep, StorageDep
from ..errors import ApiError

router = APIRouter(tags=["songs"])

# What the browser is allowed to send. ffprobe is the real gate - an extension
# proves nothing - but rejecting on it first avoids spending a decode on a PDF.
ACCEPTED_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".mp4"}

CHUNK_BYTES = 1024 * 1024


class SongCreated(BaseModel):
    """What the upload screen needs to move to the progress screen."""

    id: uuid.UUID
    title: str
    duration_sec: int
    status: str
    is_playable: bool
    sample_rate: int = Field(description="Always 44100; the whole point of normalising.")
    channels: int = Field(description="Always 2.")
    already_existed: bool = Field(
        description="True when the same audio was uploaded before and was reused rather than "
        "processed again. Chapter 9 caps new songs per month, so this matters to the user."
    )


def _title_from(filename: str | None) -> str:
    if not filename:
        return "ללא שם"
    # Hebrew filenames are the normal case here, so no transliteration or
    # slugging - the stem of the name is the best title we have until tags or
    # the user say otherwise.
    return Path(filename).stem.strip() or "ללא שם"


async def _spool(upload: UploadFile, destination: Path, limit: int) -> int:
    """Stream the upload to disk, refusing to buffer an unbounded body in RAM.

    The size limit is enforced here, as the bytes arrive, rather than by reading
    Content-Length: the header is a claim, and the one backend instance chapter 9
    budgets for cannot afford to be talked into holding a gigabyte.
    """
    written = 0
    with destination.open("wb") as sink:
        while chunk := await upload.read(CHUNK_BYTES):
            written += len(chunk)
            if written > limit:
                raise ApiError(
                    "file_too_large",
                    f"the file is larger than {limit // (1024 * 1024)}MB",
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                )
            sink.write(chunk)
    if written == 0:
        raise ApiError("empty_file", "the file is empty")
    return written


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


@router.post(
    "/songs/upload",
    response_model=SongCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a local file and normalise it",
    responses={
        200: {"description": "The same audio was already here; the existing song is returned."}
    },
)
async def upload_song(
    response: Response,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File(description="Any common audio format.")],
) -> SongCreated:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ApiError("unsupported_format", f"{suffix or 'that file type'} is not supported")

    with tempfile.TemporaryDirectory(prefix="karuki-upload-") as tmp:
        work = Path(tmp)
        original = work / f"original{suffix}"
        await _spool(file, original, settings.max_upload_bytes)

        normalised = work / "normalised.wav"
        try:
            info = normalise(original, normalised)
        except ToolMissing as exc:
            # Not the user's fault, and not something a retry fixes.
            raise ApiError("audio_tooling_missing", str(exc), status_code=503) from exc
        except AudioError as exc:
            raise ApiError(exc.code, str(exc)) from exc

        # Hash the *normalised* audio, not the upload: the same song as mp3 and
        # as m4a is the same song, and re-separating it would burn GPU credit to
        # produce identical stems.
        content_hash = _sha256(normalised)
        existing = await session.scalar(select(Song).where(Song.content_hash == content_hash))
        if existing is not None:
            # Nothing was created, so 201 would be a lie the client may act on.
            response.status_code = status.HTTP_200_OK
            return _created(existing, already_existed=True)

        song = Song(
            id=uuid.uuid4(),
            title=_title_from(file.filename),
            source_type=SourceType.FILE,
            source_ref=file.filename,
            content_hash=content_hash,
            duration_sec=int(info.duration_sec),
            status=SongStatus.PENDING,
        )
        session.add(song)
        # Flushed rather than committed: if storage fails below, the row must not
        # survive to point at files that are not there.
        await session.flush()

        _store(storage, song.id, original, normalised)
        await session.commit()

    return _created(song, already_existed=False)


def _store(storage: Storage, song_id: uuid.UUID, original: Path, normalised: Path) -> None:
    """Keep the upload as well as the normalised copy.

    Chapter 7 requires every stage to be re-runnable on its own from saved
    intermediates. Normalisation is a stage, so its input has to survive - and
    when a format turns out to convert badly, the original is the only way to
    find out why.
    """
    prefix = f"songs/{song_id}"
    try:
        storage.put(f"{prefix}/original{original.suffix}", original)
        storage.put(f"{prefix}/normalised.wav", normalised)
    except Exception:
        storage.delete_prefix(prefix)
        raise


def _created(song: Song, already_existed: bool) -> SongCreated:
    return SongCreated(
        id=song.id,
        title=song.title,
        duration_sec=song.duration_sec or 0,
        status=song.status,
        is_playable=song.is_playable,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=TARGET_CHANNELS,
        already_existed=already_existed,
    )

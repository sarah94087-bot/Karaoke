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
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from packages.audio.normalize import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    AudioError,
    ToolMissing,
    normalise,
)
from packages.core import jobs as job_service
from packages.core.enums import SongStatus, SourceType, StemKind
from packages.core.models import Job, Song, UserSongSettings
from packages.core.settings import get_settings, save_settings
from packages.core.stems import stems_for
from packages.providers.storage import Storage

from ..config import settings
from ..deps import RunnerDep, SessionDep, StorageDep
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
    job_id: uuid.UUID | None = Field(
        default=None,
        description="The processing job, started immediately. Poll GET /jobs/{id}. Null when "
        "the song already existed and no new work was needed.",
    )


class SongJob(BaseModel):
    """The processing state of a song, as the library row needs it."""

    id: uuid.UUID
    state: str
    current_step: str | None
    progress: int
    error_code: str | None


class LibrarySong(BaseModel):
    """One row of the library screen (chapter 8)."""

    id: uuid.UUID
    title: str
    artist: str | None
    duration_sec: int | None
    status: str
    is_playable: bool = Field(
        description="D-28: true once the stems are encoded. A song can be playable while it is "
        "still processing, and the library has to show that rather than just 'processing'."
    )
    lyrics_status: str
    created_at: datetime
    job: SongJob | None = Field(
        default=None, description="The most recent job, or null for a song that never had one."
    )


class Library(BaseModel):
    songs: list[LibrarySong]
    total: int


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


@router.get(
    "/songs",
    response_model=Library,
    summary="The songs in the library, newest first",
)
async def list_songs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Library:
    """Chapter 6 describes `GET /library`, which is per-user and needs D-16.

    Until auth exists this returns every song, which is the same thing while
    there is one user. The shape is the one `/library` will have, so the screen
    that consumes it does not change when the decision lands.
    """
    total = await session.scalar(select(func.count()).select_from(Song)) or 0
    songs = list(
        await session.scalars(
            select(Song).order_by(Song.created_at.desc()).limit(limit).offset(offset)
        )
    )

    # One extra query rather than a correlated subquery per row. The quota is
    # ten new songs a month, so the page is small by construction, and a
    # DISTINCT ON would tie the query to Postgres for no measurable gain.
    latest: dict[uuid.UUID, Job] = {}
    if songs:
        for job in await session.scalars(
            select(Job)
            .where(Job.song_id.in_([song.id for song in songs]))
            .order_by(Job.created_at.asc())
        ):
            latest[job.song_id] = job  # ascending, so the last write is the newest

    return Library(
        total=total,
        songs=[_library_song(song, latest.get(song.id)) for song in songs],
    )


def _library_song(song: Song, job: Job | None) -> LibrarySong:
    return LibrarySong(
        id=song.id,
        title=song.title,
        artist=song.artist,
        duration_sec=song.duration_sec,
        status=song.status,
        is_playable=song.is_playable,
        lyrics_status=song.lyrics_status,
        created_at=song.created_at,
        job=None
        if job is None
        else SongJob(
            id=job.id,
            state=job.state,
            current_step=job.current_step,
            progress=job.progress,
            error_code=job.error_code,
        ),
    )


class StemLink(BaseModel):
    kind: str = Field(examples=["vocals", "drums", "bass", "other"])
    url: str = Field(
        description="A signed link to the audio, valid for KARUKI_SIGNED_URL_TTL seconds. "
        "Absolute with the object store, root-relative with the local backend."
    )
    format: str
    bytes: int


class PlayerSettings(BaseModel):
    """How this person likes to sing this song (chapter 5).

    The ranges are documented but not validated, deliberately. The player saves
    on every change, and a save must never fail a user's session - a tempo of
    1.5000001 arriving from a float slider should be stored as 1.5, not turned
    into a 422 that the auto-save has no way to report. Out-of-range values are
    clamped in packages/core/settings.py, and the database's CHECK constraints
    are the backstop.
    """

    key_shift: int = Field(default=0, description="Semitones. Chapter 8's range is -6..+6.")
    tempo_ratio: float = Field(default=1.0, description="Chapter 8's range is 0.5..1.5.")
    stem_volumes: dict[str, float] | None = Field(
        default=None, description='Per stem, 0..1. e.g. {"vocals": 0}'
    )
    lyric_offset_ms: int = Field(
        default=0,
        description="T-2.7: how far this person nudged the whole song's words, in milliseconds. "
        "Positive shows them later. Clamped rather than rejected, like every other setting.",
    )


def _as_settings(row: UserSongSettings | None) -> PlayerSettings:
    if row is None:
        return PlayerSettings()
    return PlayerSettings(
        key_shift=row.key_shift,
        tempo_ratio=float(row.tempo_ratio),
        stem_volumes=row.stem_volumes_json,
        lyric_offset_ms=row.lyric_offset_ms,
    )


class SongDetail(BaseModel):
    """A song and its four stems - everything the player needs to open."""

    id: uuid.UUID
    title: str
    artist: str | None
    duration_sec: int | None
    status: str
    is_playable: bool
    lyrics_status: str
    original_key: str | None
    bpm: float | None
    stems: list[StemLink]
    settings: PlayerSettings = Field(
        description="Saved player settings, or the defaults. Sent with the song so opening one "
        "is a single request rather than two."
    )


@router.get(
    "/songs/{song_id}",
    response_model=SongDetail,
    summary="A song and its stems",
)
async def get_song(session: SessionDep, storage: StorageDep, song_id: uuid.UUID) -> SongDetail:
    """Chapter 6 hands out *signed* URLs here, and since T-3.1 that is what
    these are: a link that carries its own authority and stops working after
    `KARUKI_SIGNED_URL_TTL`. The player is unchanged by which backend signed it
    - an S3 presigned URL is absolute, a local one is root-relative, and both
    are "fetch what you are given"."""
    song = await session.get(Song, song_id)
    if song is None:
        raise ApiError("song_not_found", "no such song", status_code=status.HTTP_404_NOT_FOUND)

    found = await stems_for(session, song.id)
    saved = await get_settings(session, uuid.UUID(settings.dev_user_id), song.id)
    order = {str(kind): index for index, kind in enumerate(StemKind)}
    return SongDetail(
        id=song.id,
        title=song.title,
        artist=song.artist,
        duration_sec=song.duration_sec,
        status=song.status,
        is_playable=song.is_playable,
        lyrics_status=song.lyrics_status,
        original_key=song.original_key,
        bpm=float(song.bpm) if song.bpm is not None else None,
        settings=_as_settings(saved),
        stems=[
            StemLink(
                kind=stem.kind,
                url=storage.signed_url(stem.storage_key, settings.signed_url_ttl),
                format=stem.format,
                bytes=stem.bytes,
            )
            for stem in sorted(found, key=lambda stem: order.get(stem.kind, 99))
        ],
    )


@router.put(
    "/songs/{song_id}/settings",
    response_model=PlayerSettings,
    summary="Save the player settings for a song",
)
async def put_settings(
    session: SessionDep, song_id: uuid.UUID, body: PlayerSettings
) -> PlayerSettings:
    """Chapter 6 calls this `PUT /library/{song_id}/settings`; that path is
    per-user and needs D-16, and the body is the same either way.

    Called on every change in the player, so it is an upsert: two saves can be
    in flight at once - a fader moved while a key change is still posting - and
    a read-modify-write would let the older one win.
    """
    if await session.get(Song, song_id) is None:
        raise ApiError("song_not_found", "no such song", status_code=status.HTTP_404_NOT_FOUND)

    saved = await save_settings(
        session,
        uuid.UUID(settings.dev_user_id),
        song_id,
        key_shift=body.key_shift,
        tempo_ratio=body.tempo_ratio,
        stem_volumes=body.stem_volumes,
        lyric_offset_ms=body.lyric_offset_ms,
    )
    return _as_settings(saved)


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
    runner: RunnerDep,
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

        # Chapter 6: creating a song starts the work and returns a job_id
        # immediately. The job is queued inside the transaction and only
        # scheduled once it is committed - a task that raced the commit would
        # look up a job that is not there yet.
        job = await job_service.create_job(session, song, uuid.UUID(settings.dev_user_id))
        await session.commit()

    runner.schedule(job.id)
    return _created(song, already_existed=False, job_id=job.id)


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


def _created(song: Song, already_existed: bool, job_id: uuid.UUID | None = None) -> SongCreated:
    return SongCreated(
        id=song.id,
        title=song.title,
        duration_sec=song.duration_sec or 0,
        status=song.status,
        is_playable=song.is_playable,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=TARGET_CHANNELS,
        already_existed=already_existed,
        job_id=job_id,
    )

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
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.audio.normalize import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    AudioError,
    ToolMissing,
    normalise,
)
from packages.audio.tags import MAX_LENGTH as MAX_NAME_LENGTH
from packages.audio.tags import clean, read_tags
from packages.core import jobs as job_service
from packages.core import quota
from packages.core.enums import SongStatus, SourceType, StemKind
from packages.core.metadata import Details, details_for, edited_now
from packages.core.models import Job, Song, UserSongSettings
from packages.core.settings import get_settings, save_settings
from packages.core.stems import stems_for
from packages.providers.storage import Storage, StorageError

from ..config import settings
from ..deps import RunnerDep, SessionDep, StorageDep, UserDep
from ..errors import ApiError
from ..ownership import owned_song
from ..runner import JobRunner

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


class UploadTicket(BaseModel):
    """Where to PUT the file, and for how long (chapter 6's upload-url)."""

    key: str = Field(description="Hand this back to POST /songs once the PUT has finished.")
    url: str = Field(description="Signed. Absolute with the object store, relative without.")
    method: str = "PUT"
    expires_in: int
    max_bytes: int = Field(description="What the server will accept, whatever was declared.")


class UploadUrlRequest(BaseModel):
    filename: str = Field(description="Used for the suffix and, later, for the title.")
    bytes: int = Field(
        ge=1,
        description="What the browser says the file weighs. A claim, checked again after the "
        "upload against the object that actually arrived.",
    )


class SongFromUpload(BaseModel):
    """Chapter 6's `POST /songs`: the bytes are already in storage."""

    upload_key: str
    filename: str


class Library(BaseModel):
    songs: list[LibrarySong]
    total: int


def _details(
    original: Path, filename: str | None, title: str | None, artist: str | None
) -> Details:
    """What to call the song, from the file itself where possible (T-4.2).

    The file name was the only source until T-4.2 and is now the last resort:
    almost every audio file carries a title and an artist inside it, and a tag
    is something somebody wrote down where a file name is a guess. Reading them
    cannot fail the upload - `read_tags` answers with empty fields for a file it
    cannot parse, and `normalise` has already refused anything that is not audio.
    """
    return details_for(
        filename=filename,
        tags=read_tags(original),
        title_hint=title,
        artist_hint=artist,
    )


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
    user_id: UserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Library:
    """Chapter 6's `GET /library`: this user's songs, newest first.

    T-1.10 built this returning every song and said so at the time - with one
    user it was the same list, and the response shape was already the one
    `/library` would need. T-3.7 is where the filter arrives, and the screen
    that consumes it did not change.
    """
    mine = Song.user_id == user_id
    total = await session.scalar(select(func.count()).select_from(Song).where(mine)) or 0
    songs = list(
        await session.scalars(
            select(Song).where(mine).order_by(Song.created_at.desc()).limit(limit).offset(offset)
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
async def get_song(
    session: SessionDep, storage: StorageDep, user_id: UserDep, song_id: uuid.UUID
) -> SongDetail:
    """Chapter 6 hands out *signed* URLs here, and since T-3.1 that is what
    these are: a link that carries its own authority and stops working after
    `KARUKI_SIGNED_URL_TTL`. The player is unchanged by which backend signed it
    - an S3 presigned URL is absolute, a local one is root-relative, and both
    are "fetch what you are given"."""
    song = await owned_song(session, song_id, user_id)

    found = await stems_for(session, song.id)
    saved = await get_settings(session, user_id, song.id)
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
    session: SessionDep, user_id: UserDep, song_id: uuid.UUID, body: PlayerSettings
) -> PlayerSettings:
    """Chapter 6 calls this `PUT /library/{song_id}/settings`; that path is
    per-user and needs D-16, and the body is the same either way.

    Called on every change in the player, so it is an upsert: two saves can be
    in flight at once - a fader moved while a key change is still posting - and
    a read-modify-write would let the older one win.
    """
    await owned_song(session, song_id, user_id)

    saved = await save_settings(
        session,
        user_id,
        song_id,
        key_shift=body.key_shift,
        tempo_ratio=body.tempo_ratio,
        stem_volumes=body.stem_volumes,
        lyric_offset_ms=body.lyric_offset_ms,
    )
    return _as_settings(saved)


class SongDetailsIn(BaseModel):
    """A correction to the name or the artist (T-4.2).

    Both are optional and mean different things by their absence: a field that
    is not in the body is left alone, and `artist: ""` clears it. That
    distinction is why this is a PATCH and not a PUT - the screen sends what
    was edited, and a PUT would make "I only changed the artist" indistinguish-
    able from "the title is now blank".
    """

    title: str | None = None
    artist: str | None = None


class SongDetails(BaseModel):
    id: uuid.UUID
    title: str
    artist: str | None
    duration_sec: int | None = Field(
        description="Measured off the normalised audio, and deliberately not editable: it is "
        "the one field here that is a measurement rather than a claim."
    )
    details_edited: bool = Field(
        description="True once a person has typed here. Everything that fills these fields "
        "automatically - tags, the importer, the open lyrics database - stops when it is."
    )


@router.patch(
    "/songs/{song_id}",
    response_model=SongDetails,
    summary="Correct the name or the artist",
)
async def patch_song(
    session: SessionDep, user_id: UserDep, song_id: uuid.UUID, body: SongDetailsIn
) -> SongDetails:
    """The "correctable by hand" half of T-4.2.

    Loud rather than clamped, unlike the player settings (T-1.16): this is a
    button somebody pressed with a name they typed, and quietly storing
    something other than what they wrote is worse than saying it did not go in.

    Any correction here stamps `details_edited_at`, which turns off every
    automatic write for this song - including the one the lyrics database makes
    minutes later, which is the one that could otherwise land on top of a
    person's work while they are looking at the screen.
    """
    song = await owned_song(session, song_id, user_id)
    sent = body.model_fields_set

    if "title" in sent:
        title = clean(body.title)
        if title is None:
            raise ApiError("song_title_empty", "a song needs a name")
        if len(body.title or "") > MAX_NAME_LENGTH:
            raise ApiError("song_name_too_long", f"names are up to {MAX_NAME_LENGTH} characters")
        song.title = title

    if "artist" in sent:
        if len(body.artist or "") > MAX_NAME_LENGTH:
            raise ApiError("song_name_too_long", f"names are up to {MAX_NAME_LENGTH} characters")
        # Cleared rather than refused: "I do not know who this is" is a real
        # answer, and the field started empty for most songs anyway.
        song.artist = clean(body.artist)

    if sent:
        song.details_edited_at = edited_now()
    await session.commit()

    return SongDetails(
        id=song.id,
        title=song.title,
        artist=song.artist,
        duration_sec=song.duration_sec,
        details_edited=song.details_edited_at is not None,
    )


# Chapter 6's direct-upload pair. `uploads/<token>/original<suffix>` is the only
# shape of key these will sign or accept: the client chooses none of it, so a
# ticket cannot be talked into writing over a stem.
UPLOAD_KEY = re.compile(r"^uploads/[0-9a-f-]{36}/original(\.[a-z0-9]{1,5})$")


@router.delete(
    "/songs/{song_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a song and everything it occupies",
)
async def delete_song(
    session: SessionDep, storage: StorageDep, user_id: UserDep, song_id: uuid.UUID
) -> Response:
    """Chapter 6's `DELETE /library/{song_id}` - "including freeing storage".

    Storage first, then the row. The other order can leave objects nobody has a
    row for, which is a bucket that fills up with things no screen can show and
    no user can delete. A row without its objects is recoverable by
    reprocessing; objects without a row are not findable at all.
    """
    song = await owned_song(session, song_id, user_id)
    storage.delete_prefix(f"songs/{song.id}")
    await session.delete(song)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/songs/{song_id}/played",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Note that this song was sung",
)
async def mark_played(session: SessionDep, user_id: UserDep, song_id: uuid.UUID) -> Response:
    """What makes "least played" and "not played for six months" mean anything.

    Called when playback actually starts, not when the page opens: opening a
    song to fix a line in the lyrics editor is not singing it, and chapter 9
    deletes on the strength of this.
    """
    song = await owned_song(session, song_id, user_id)
    song.last_played_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/songs/upload-url",
    response_model=UploadTicket,
    summary="Where to send the file",
)
async def upload_url(
    session: SessionDep, storage: StorageDep, user_id: UserDep, body: UploadUrlRequest
) -> UploadTicket:
    """Chapter 6: the file goes straight to storage, not through the API.

    What this hands out is narrow on purpose - one key, one method, one hour.
    The API keeps the right to say *where* the bytes may land and *for how
    long*, and gives away nothing else; there is no credential in the browser.
    """
    suffix = Path(body.filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ApiError("unsupported_format", f"{suffix or 'that file type'} is not supported")
    if body.bytes > settings.max_upload_bytes:
        # Refusing on the declared size saves the upload rather than the check:
        # the real size is checked again in POST /songs, because this number is
        # a claim from the browser.
        raise ApiError(
            "file_too_large",
            f"the file is larger than {settings.max_upload_bytes // (1024 * 1024)}MB",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    # Before the bytes, not after: refusing a song once it is in the bucket has
    # already spent the transfer the limit exists to protect (D-30). Not the
    # concurrency limit, though - see check_can_add.
    await _within_quota(session, user_id, concurrency=False)

    key = f"uploads/{uuid.uuid4()}/original{suffix}"
    return UploadTicket(
        key=key,
        url=storage.signed_upload_url(key, settings.signed_url_ttl),
        expires_in=settings.signed_url_ttl,
        max_bytes=settings.max_upload_bytes,
    )


@router.post(
    "/songs",
    response_model=SongCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a song from an uploaded file and start the work",
    responses={
        200: {"description": "The same audio was already here; the existing song is returned."}
    },
)
async def create_song(
    response: Response,
    session: SessionDep,
    storage: StorageDep,
    runner: RunnerDep,
    user_id: UserDep,
    body: SongFromUpload,
) -> SongCreated:
    """The second half of the direct upload, and chapter 6's `POST /songs`.

    The bytes are in storage already. They are read once here, because
    normalising is ffmpeg's job and ffmpeg needs a file - so with the object
    store this downloads what the browser just uploaded. That is one transfer,
    not the 30MB through the API that T-1.5 did, and it is what D-14 and the
    hashing both need.
    """
    if not UPLOAD_KEY.match(body.upload_key):
        raise ApiError("invalid_request", "that is not an upload key")

    try:
        original = storage.local_path(body.upload_key)
    except StorageError as exc:
        # The usual cause is a PUT that never happened or that failed silently.
        raise ApiError("upload_not_found", "no file was uploaded under that key", 404) from exc

    prefix = body.upload_key.rsplit("/", 1)[0]
    if original.stat().st_size > settings.max_upload_bytes:
        # The size was a claim when the ticket was issued; this is the object
        # that actually arrived. Removed rather than left sitting in the bucket.
        storage.delete_prefix(prefix)
        raise ApiError(
            "file_too_large",
            f"the file is larger than {settings.max_upload_bytes // (1024 * 1024)}MB",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    try:
        created = await _ingest(
            session,
            storage,
            runner,
            response,
            original,
            user_id=user_id,
            filename=body.filename,
            # From the key we issued, not from the file the backend handed back.
            suffix=Path(body.upload_key).suffix,
        )
    finally:
        # Whatever happened, the staging copy has no further use: it was either
        # taken over by the song or it is a file nobody will look for again.
        storage.delete_prefix(prefix)
    return created


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
    user_id: UserDep,
    file: Annotated[UploadFile, File(description="Any common audio format.")],
) -> SongCreated:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ApiError("unsupported_format", f"{suffix or 'that file type'} is not supported")

    with tempfile.TemporaryDirectory(prefix="karuki-upload-") as tmp:
        original = Path(tmp) / f"original{suffix}"
        await _spool(file, original, settings.max_upload_bytes)

        return await _ingest(
            session,
            storage,
            runner,
            response,
            original,
            user_id=user_id,
            filename=file.filename,
            suffix=suffix,
        )


async def _ingest(
    session: AsyncSession,
    storage: Storage,
    runner: JobRunner,
    response: Response,
    original: Path,
    *,
    user_id: uuid.UUID,
    filename: str | None,
    suffix: str,
    title: str | None = None,
    artist: str | None = None,
    source_type: SourceType = SourceType.FILE,
    source_ref: str | None = None,
) -> SongCreated:
    """Normalise, dedup, record, and start the job.

    Shared by every way in - T-1.5's upload through the API, T-3.2's upload
    straight to storage, and T-4.1's import from a link - because everything
    from here on is the same work: only how the bytes arrived differs, and that
    difference ends at this line. What a caller may still say is where the audio
    came from and what to call it: an importer knows the title from the source
    and has no file name to guess it from.
    """
    with tempfile.TemporaryDirectory(prefix="karuki-ingest-") as tmp:
        normalised = Path(tmp) / "normalised.wav"
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
        # Scoped to the owner (T-3.7). Dedup across users would hand whoever
        # uploads a song second the first person's row - their title, their
        # stems, and the knowledge that somebody else has that song.
        existing = await session.scalar(
            select(Song).where(Song.content_hash == content_hash, Song.user_id == user_id)
        )
        if existing is not None:
            # Nothing was created, so 201 would be a lie the client may act on.
            response.status_code = status.HTTP_200_OK
            return _created(existing, already_existed=True)

        # After the dedup, not before (T-3.8). A song the user already has
        # creates no job, no stems and no bytes, so refusing it for "a song is
        # already being processed" would be refusing something that costs
        # nothing. Checked here rather than only when the ticket was issued
        # because a ticket lasts an hour and the answer can change in it.
        await _within_quota(session, user_id)

        details = _details(original, filename, title, artist)
        song = Song(
            id=uuid.uuid4(),
            user_id=user_id,
            title=details.title,
            artist=details.artist,
            source_type=source_type,
            source_ref=source_ref if source_ref is not None else filename,
            content_hash=content_hash,
            duration_sec=int(info.duration_sec),
            status=SongStatus.PENDING,
        )
        session.add(song)
        # Flushed rather than committed: if storage fails below, the row must not
        # survive to point at files that are not there.
        await session.flush()

        _store(storage, song.id, original, normalised, suffix)

        # Chapter 6: creating a song starts the work and returns a job_id
        # immediately. The job is queued inside the transaction and only
        # scheduled once it is committed - a task that raced the commit would
        # look up a job that is not there yet.
        job = await job_service.create_job(session, song, user_id)
        await session.commit()

    runner.schedule(job.id)
    return _created(song, already_existed=False, job_id=job.id)


async def _within_quota(
    session: AsyncSession, user_id: uuid.UUID, *, concurrency: bool = True
) -> None:
    """Chapter 9's limits, in the shape the rest of this API refuses things.

    D-30: never a silent stop that looks like a fault. The code names which
    limit it was, and `details` carries the numbers, so the screen can say how
    much is used out of how much - and, when the disk is full, offer the songs
    to remove.
    """
    try:
        await quota.check_can_add(session, user_id, concurrency=concurrency)
    except quota.QuotaExceeded as exc:
        raise ApiError(
            exc.code,
            str(exc),
            status_code=status.HTTP_409_CONFLICT,
            details={"used": exc.used, "limit": exc.limit},
        ) from exc


def _store(
    storage: Storage, song_id: uuid.UUID, original: Path, normalised: Path, suffix: str
) -> None:
    """Keep the upload as well as the normalised copy.

    Chapter 7 requires every stage to be re-runnable on its own from saved
    intermediates. Normalisation is a stage, so its input has to survive - and
    when a format turns out to convert badly, the original is the only way to
    find out why.

    The suffix is passed in rather than read off `original.suffix`, which a live
    run is what caught: with the object store, `local_path` hands back a cache
    file named by a hash and with no extension at all, and the original landed
    in the bucket as `original` - no format on it, and `application/octet-stream`
    when it is read back. On disk the same code was right by accident.
    """
    prefix = f"songs/{song_id}"
    try:
        storage.put(f"{prefix}/original{suffix}", original)
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

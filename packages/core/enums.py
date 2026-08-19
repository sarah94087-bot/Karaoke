"""The vocabularies the database and the API agree on.

Stored as strings with a CHECK constraint rather than as native Postgres enum
types. Two reasons: `ALTER TYPE ... ADD VALUE` is awkward to write a downgrade
for, and D-15 (which managed Postgres) is still open, so the schema stays as
portable as it can cheaply be.
"""

from enum import StrEnum


class SourceType(StrEnum):
    """Where the audio came from. D-01: both, starting from a file."""

    FILE = "file"
    URL = "url"


class SongStatus(StrEnum):
    """Coarse lifecycle of the song itself.

    Deliberately *not* the same axis as `Song.is_playable`. Chapter 5 calls that
    separation the heart of staged readiness (D-28): a song is playable once the
    stems are encoded, which is well before it is `READY` with aligned lyrics.
    Collapsing the two into one field is the mistake this comment exists to
    prevent.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class LyricsStatus(StrEnum):
    """How good the timing is, or why there is none.

    Chapter 7: a transcription failure is not a job failure. The song still
    plays, the lyrics are marked missing, and the user is invited to the editor.
    """

    PENDING = "pending"
    LINE = "line"
    WORD = "word"
    MISSING = "missing"


class StemKind(StrEnum):
    """The four Demucs outputs (D-06)."""

    VOCALS = "vocals"
    DRUMS = "drums"
    BASS = "bass"
    OTHER = "other"


class JobState(StrEnum):
    """Coarse state. `Job.current_step` carries the detail.

    Chapter 5 gives a job both a `state` and a `current_step`; this is the split
    between them. The state is what the library screen colours a row by, and
    what a "one concurrent job per user" quota counts. The step is what the
    progress screen names in Hebrew.
    """

    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class JobStep(StrEnum):
    """The pipeline stages of chapter 7, in order.

    Transcription is split because D-29 runs it twice - on the mix immediately,
    and on the separated vocals afterwards - and picks the better transcript.
    Knowing which of the two is in flight is the difference between an honest
    progress screen and a guess.
    """

    INGESTING = "ingesting"
    SEPARATING = "separating"
    TRANSCRIBING_MIX = "transcribing_mix"
    TRANSCRIBING_VOCALS = "transcribing_vocals"
    ENCODING = "encoding"
    ALIGNING = "aligning"

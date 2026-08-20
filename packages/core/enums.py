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
    """The pipeline stages of chapter 7, in the order the job *reports* them.

    Reports, not runs: D-29 starts the transcription of the mix while the
    separation is still going, precisely so the words are ready when the stems
    are. It is only named as a step in the case where the job is actually
    waiting on it, which is why it sits after `ENCODING` here even though it
    began long before. A progress bar that goes backwards reads as a bug even
    when nothing is wrong.

    Transcription is split in two because D-29 runs it twice - on the mix, which
    is available immediately, and on the separated vocals, which are far better
    (T-0.4.2 measured the mix returning 39% of the words). Knowing which of the
    two is in flight is the difference between an honest progress screen and a
    guess.
    """

    INGESTING = "ingesting"
    SEPARATING = "separating"
    ENCODING = "encoding"
    TRANSCRIBING_MIX = "transcribing_mix"
    TRANSCRIBING_VOCALS = "transcribing_vocals"
    ALIGNING = "aligning"


class LyricsSource(StrEnum):
    """Where a set of lyrics came from (chapter 5).

    It is stored per version, not per song, because a song usually has more than
    one: an ASR transcript that the user then corrected leaves both rows, and
    "who wrote this line" is the difference between a timing worth trusting and
    one worth re-running. D-29 runs the transcription twice, on the mix and on
    the separated vocals, so those two are separate values rather than one `asr`.
    """

    DB = "db"
    MIX_ASR = "mix_asr"
    VOCALS_ASR = "vocals_asr"
    MANUAL = "manual"

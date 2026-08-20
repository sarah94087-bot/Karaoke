"""Working out a song's tempo and key, and putting them on the row.

Both are measured from the **normalised mix**, not from the stems.

That is the opposite of what this file did at first, and the change came from
measuring rather than from reasoning. The plausible story was that stems must
help - tempo from the drums where onsets are unambiguous, key from bass and
other instruments without a vocal line sliding between notes. On the two real
songs to hand, the measurement said otherwise:

    source        song A              song B
    full mix      D    (conf 0.359)   Dm  (conf 0.173)
    other+bass    Am   (conf 0.033)   Dm  (conf 0.025)
    other only    Bm   (conf 0.365)   Dm  (conf 0.025)
    bass only     C    (conf 0.042)   Am  (conf 0.068)

The full mix wins on confidence every time, and `other+bass` even disagrees with
it on song A. In hindsight the reason is not subtle: a bass line is mostly roots
and octaves, so mixing it in at equal weight skews the chroma away from the
distribution the Krumhansl-Kessler profiles were calibrated on - which is
ordinary music, heard whole.

Tempo came out identical from the drums and from the mix on both songs (132.5
and 129.2), so there was no case for the extra decode.

Failure here is not job failure. Chapter 7 takes that position for transcription
and alignment, and the same reasoning applies: a song whose tempo we could not
measure is still a song you can sing.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from packages.audio.analyse import Analysis, analyse
from packages.audio.decode import decode
from packages.core.models import Song
from packages.core.stems import normalised_key
from packages.providers.storage import Storage

log = logging.getLogger("karuki.analysis")

# The whole song is not needed to find a tempo or a key, and ninety seconds cost
# about two. Analysing a four-minute song in full would make this the slowest
# step after separation, for no better answer.
ANALYSIS_SECONDS = 90.0


def analyse_song_audio(storage: Storage, song: Song) -> Analysis | None:
    """Measure tempo and key from the song's normalised audio."""
    key = normalised_key(song.id)
    if not storage.exists(key):
        return None

    decoded = decode(storage.local_path(key), max_seconds=ANALYSIS_SECONDS)
    return analyse(decoded, decoded)


async def analyse_song(session: AsyncSession, storage: Storage, song: Song) -> Analysis | None:
    """Analyse and record. Returns None when nothing could be measured.

    Never raises: the caller is the pipeline, and a job that has already produced
    four stems must not fail because a tempo could not be found.
    """
    try:
        result = analyse_song_audio(storage, song)
    except Exception:
        log.exception("analysis failed for song %s", song.id)
        return None

    if result is None:
        log.info("song %s could not be analysed; leaving bpm and key empty", song.id)
        return None

    if result.bpm is not None:
        song.bpm = result.bpm
    if result.key is not None:
        song.original_key = result.key
    await session.flush()
    return result

"""Transcription, behind one call: audio in, text with timestamps out (T-2.3).

D-27 chose a hosted service over local Whisper, and phase 0 measured why: the
same song took 429s on this machine's CPU and 9.4-16.3s on Groq - 26 to 45 times
faster - with noticeably better Hebrew. It is the free tier, with **no card**,
which chapter 1 requires, and phase 0 read the real quota off the response
headers rather than off a marketing page: 2,000 requests a day against an
expected 30 a month.

Two things phase 0 also found, which shape what this returns:

* **Groq's time boundaries are not to be trusted as they are.** It reported
  `0.0s` for a song whose singing starts at 15.3s, having transcribed the
  instrumental intro as `תודה רבה`. So the segments come back with the
  `no_speech_prob` and `avg_logprob` the model reports, and the filtering
  happens in `packages/lyrics/transcript.py` where it can be tested.
* **It repeats itself** - four exactly duplicated segments in one song. Same
  place, same reason.

`packages/lyrics/matching.py` decides what a lyrics row means; this file only
speaks HTTP.
"""

import dataclasses
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from packages.providers.net import USER_AGENT, trust_system_certificates

log = logging.getLogger("karuki.transcription")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Phase 0 verified this model on real Hebrew singing (docs/phase0/quotas.md).
GROQ_MODEL = "whisper-large-v3"
# Groq's free tier refuses a larger upload. A four-minute vocals stem at 128k is
# about 4MB, and chapter 9 caps a song at eight minutes, so this is headroom
# rather than a limit anyone will meet - but a clear refusal beats a 413 from
# somebody else's server.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Phase 0: 9.4-16.3s for a 2:13 song. A minute is far past anything healthy and
# still short enough that a hung call does not hold a job open all afternoon.
TIMEOUT_SECONDS = float(os.getenv("KARUKI_TRANSCRIPTION_TIMEOUT", "120"))


class TranscriptionError(RuntimeError):
    """The transcription failed.

    Chapter 7: this is never a job failure. The stems are already made, the user
    can already sing, and the words fall back to the editor.
    """


class TranscriptionUnavailable(TranscriptionError):
    """This deployment cannot transcribe at all - no key, or no backend.

    Deliberately distinct, the same way `SeparationUnavailable` is: one is an
    operator's problem and the other is the recording's, and telling a user
    their song could not be transcribed when nobody configured a key would be a
    lie.
    """


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int | None = None


@dataclass(frozen=True)
class Segment:
    """One stretch of speech as the model heard it.

    `no_speech_prob` and `avg_logprob` are carried rather than acted on here.
    They are the only evidence there is for phase 0's hallucinated intro, and
    the code that uses them belongs where it can be tested without a network.
    """

    text: str
    start_ms: int
    end_ms: int
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float | None = None
    avg_logprob: float | None = None


@dataclass(frozen=True)
class Transcript:
    """Everything one run produced, plus what it cost."""

    segments: list[Segment]
    text: str
    language: str | None
    duration_sec: float | None
    model: str
    backend: str
    elapsed_sec: float
    # `x-ratelimit-*` as the server reports it. Phase 0's rule: the published
    # number is a claim, the header is a measurement.
    rate_limit: dict[str, str] = field(default_factory=dict)


class Transcriber(Protocol):
    """What the rest of the code may assume about a transcription service."""

    name: str

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript: ...


class NoTranscriber:
    """No service configured. Says so rather than pretending to find nothing."""

    name = "none"

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
        raise TranscriptionUnavailable(
            "no transcription backend is configured (KARUKI_TRANSCRIPTION_BACKEND)"
        )


def multipart(fields: list[tuple[str, str]], name: str, audio: Path) -> tuple[bytes, str]:
    """Build a multipart/form-data body for one file and some plain fields.

    Hand-rolled because the standard library has an encoder for reading
    multipart and none for writing it, and because the alternative is adding an
    HTTP client to an image that has to stay small. `fields` is a list rather
    than a dict on purpose: `timestamp_granularities[]` is sent twice.
    """
    boundary = f"----karuki{uuid.uuid4().hex}"
    line_break = b"\r\n"
    body = bytearray()

    for key, value in fields:
        body += b"--" + boundary.encode() + line_break
        body += f'Content-Disposition: form-data; name="{key}"'.encode() + line_break
        body += line_break
        body += value.encode("utf-8") + line_break

    content_type = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
    body += b"--" + boundary.encode() + line_break
    body += (
        f'Content-Disposition: form-data; name="{name}"; filename="{audio.name}"'.encode()
        + line_break
    )
    body += f"Content-Type: {content_type}".encode() + line_break
    body += line_break
    body += audio.read_bytes() + line_break
    body += b"--" + boundary.encode() + b"--" + line_break

    return bytes(body), f"multipart/form-data; boundary={boundary}"


class GroqTranscriber:
    """Groq's hosted `whisper-large-v3` (D-27).

    `urllib` again, for the reason `lyrics_catalogue.py` gives: one request, and
    the API image has to stay deployable on a free tier.
    """

    name = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        url: str = GROQ_URL,
        model: str = GROQ_MODEL,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        self.url = url
        self.model = model
        self.timeout = timeout

    def transcribe(self, audio: Path, language: str | None = None) -> Transcript:
        if not self.api_key:
            raise TranscriptionUnavailable(
                "GROQ_API_KEY is not set; see .env.example (the key never goes in the repo)"
            )
        if not audio.is_file():
            raise TranscriptionError(f"{audio} is not a file")
        if not audio.suffix:
            # The service types the upload by the filename in the multipart
            # body, and answers a name with no extension with a 400 that
            # describes the accepted *types* rather than the missing suffix.
            # Said here instead, because the request is otherwise perfect and
            # the reply reads like the audio is wrong.
            raise TranscriptionError(
                f"{audio.name} has no extension: the service reads the format from the "
                "filename, so the file has to keep the suffix of the object it came from"
            )

        size = audio.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise TranscriptionError(
                f"{audio.name} is {size / 1e6:.1f}MB; the service takes at most "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f}MB"
            )

        fields = [
            ("model", self.model),
            ("response_format", "verbose_json"),
            # Zero, not the default: this is a transcript that a person will
            # correct, and a model inventing a more fluent line makes their job
            # harder rather than easier.
            ("temperature", "0"),
            ("timestamp_granularities[]", "segment"),
            # Words are what T-2.5 needs for a word-level highlight. Asking for
            # them costs nothing; a service that ignores the field simply
            # returns none, and line-level is a normal outcome (chapter 7).
            ("timestamp_granularities[]", "word"),
        ]
        if language:
            fields.append(("language", language))

        body, content_type = multipart(fields, "file", audio)
        trust_system_certificates()
        request = urllib.request.Request(  # noqa: S310 - https, from a constant
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
                # Not decoration. Groq's edge answers the default
                # `Python-urllib/3.11` with 403 on a GET, and on this POST it
                # drops the connection instead - which arrives as
                # `EOF occurred in violation of protocol` and looks for all the
                # world like a TLS problem. See packages/providers/net.py.
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
                headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if "ratelimit" in key.lower()
                }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403):
                raise TranscriptionUnavailable(f"{self.name} refused the key: {detail}") from exc
            raise TranscriptionError(f"{self.name} answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise TranscriptionError(f"{self.name} could not be reached: {exc}") from exc

        elapsed = time.perf_counter() - started
        transcript = parse_verbose_json(payload, backend=self.name, elapsed_sec=elapsed)
        log.info(
            "%s transcribed %s (%.1fs audio) in %.1fs into %d segments",
            self.name,
            audio.name,
            transcript.duration_sec or 0.0,
            elapsed,
            len(transcript.segments),
        )
        return dataclasses.replace(transcript, rate_limit=headers)


def _ms(seconds: float | str | None) -> int:
    """Seconds from a JSON body, as milliseconds. Anything unreadable is 0.

    A service that sends `"15.3"` where it promised `15.3` should not take the
    whole transcript down with it.
    """
    try:
        return max(0, round(float(seconds or 0) * 1000))
    except (TypeError, ValueError):
        return 0


def parse_verbose_json(payload: dict, *, backend: str, elapsed_sec: float = 0.0) -> Transcript:
    """Whisper's `verbose_json`, as OpenAI defined it and Groq answers it.

    Words arrive as one flat list for the whole recording rather than inside
    their segments, so they are handed back to the segment whose span contains
    them. Doing it by time is what makes this work for any service that speaks
    this shape, which is the point of having a seam at all.
    """
    segments_in = payload.get("segments") or []
    words_in = payload.get("words") or []

    words = [
        Word(
            text=str(word.get("word") or word.get("text") or "").strip(),
            start_ms=_ms(word.get("start")),
            end_ms=_ms(word.get("end")) if word.get("end") is not None else None,
        )
        for word in words_in
        if str(word.get("word") or word.get("text") or "").strip()
    ]

    segments: list[Segment] = []
    for raw in segments_in:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start, end = _ms(raw.get("start")), _ms(raw.get("end"))
        segments.append(
            Segment(
                text=text,
                start_ms=start,
                end_ms=max(end, start),
                words=[word for word in words if start <= word.start_ms < max(end, start + 1)],
                no_speech_prob=raw.get("no_speech_prob"),
                avg_logprob=raw.get("avg_logprob"),
            )
        )

    return Transcript(
        segments=segments,
        text=str(payload.get("text") or "").strip(),
        language=payload.get("language"),
        duration_sec=payload.get("duration"),
        model=str(payload.get("model") or GROQ_MODEL),
        backend=backend,
        elapsed_sec=elapsed_sec,
    )


BACKENDS: dict[str, type] = {
    "groq": GroqTranscriber,
    "none": NoTranscriber,
}


def get_transcriber(backend: str = "groq") -> Transcriber:
    """Groq by default.

    Unlike the separator, whose default is local because a stray remote run
    spends GPU credit, this one costs nothing but a request out of 2,000 a day.
    A deployment with no key gets `TranscriptionUnavailable` at the moment it
    tries, which is a clearer signal than silently having no lyrics.
    """
    try:
        return BACKENDS[backend]()
    except KeyError:
        raise TranscriptionError(
            f"unknown transcription backend {backend!r}; expected one of {sorted(BACKENDS)}"
        ) from None

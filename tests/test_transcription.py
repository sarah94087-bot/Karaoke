"""The transcription client (T-2.3): a file goes in, timed text comes back.

**No test here spends a request.** The free tier is 2,000 a day and phase 0
measured the expected usage at 30 a month; a suite that transcribed on every run
would be the thing that exhausted it. `urlopen` is replaced, which also lets the
request itself be inspected - the model, the response format and the two
`timestamp_granularities[]` are as much a part of the contract as the answer is.
"""

import io
import json
import urllib.error
from email.parser import BytesParser
from pathlib import Path

import pytest

from packages.providers import transcription
from packages.providers.transcription import (
    GroqTranscriber,
    NoTranscriber,
    TranscriptionError,
    TranscriptionUnavailable,
    get_transcriber,
    multipart,
    parse_verbose_json,
)

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "hebrew",
    "duration": 133.2,
    "text": "שורה ראשונה שורה שנייה",
    "segments": [
        {
            "id": 0,
            "start": 15.3,
            "end": 19.1,
            "text": " שורה ראשונה",
            "avg_logprob": -0.21,
            "no_speech_prob": 0.02,
        },
        {
            "id": 1,
            "start": 19.1,
            "end": 23.4,
            "text": " שורה שנייה",
            "avg_logprob": -0.30,
            "no_speech_prob": 0.05,
        },
    ],
    "words": [
        {"word": "שורה", "start": 15.3, "end": 15.9},
        {"word": "ראשונה", "start": 15.9, "end": 17.2},
        {"word": "שורה", "start": 19.1, "end": 19.6},
        {"word": "שנייה", "start": 19.6, "end": 20.4},
    ],
}


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "vocals.mp3"
    path.write_bytes(b"not really an mp3, and nothing here decodes it")
    return path


class FakeResponse(io.BytesIO):
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        self.close()


@pytest.fixture
def sent(monkeypatch) -> list:
    """Replaces the network and records what would have gone over it."""
    captured: list = []

    def fake_urlopen(request, timeout=None):
        captured.append(request)
        return FakeResponse(
            VERBOSE_JSON,
            {"x-ratelimit-remaining-requests": "1999", "content-type": "application/json"},
        )

    monkeypatch.setattr(transcription.urllib.request, "urlopen", fake_urlopen)
    return captured


def parts_of(request) -> dict[str, list[str]]:
    """Read the multipart body back the way a server would."""
    raw = f"Content-Type: {request.headers['Content-type']}\r\n\r\n".encode() + request.data
    fields: dict[str, list[str]] = {}
    for part in BytesParser().parsebytes(raw).get_payload():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename():
            fields.setdefault("__file__", []).append(part.get_filename())
        else:
            fields.setdefault(name, []).append(part.get_payload().strip())
    return fields


def test_a_file_goes_in_and_timed_text_comes_back(audio: Path, sent: list):
    """T-2.3's acceptance criterion."""
    transcript = GroqTranscriber(api_key="test").transcribe(audio, language="he")

    assert [segment.text for segment in transcript.segments] == ["שורה ראשונה", "שורה שנייה"]
    assert [segment.start_ms for segment in transcript.segments] == [15_300, 19_100]
    assert [segment.end_ms for segment in transcript.segments] == [19_100, 23_400]


def test_words_are_handed_to_the_segment_they_fall_inside(audio: Path, sent: list):
    """Whisper returns one flat list of words for the whole recording; a player
    needs them with their line."""
    transcript = GroqTranscriber(api_key="test").transcribe(audio)

    assert [word.text for word in transcript.segments[0].words] == ["שורה", "ראשונה"]
    assert [word.text for word in transcript.segments[1].words] == ["שורה", "שנייה"]
    assert transcript.segments[0].words[0].start_ms == 15_300


def test_the_request_asks_for_what_this_project_needs(audio: Path, sent: list):
    """The model and the format are part of the contract: phase 0 verified
    `whisper-large-v3` specifically, and `verbose_json` is the only response
    format that carries times at all."""
    GroqTranscriber(api_key="test").transcribe(audio, language="he")

    fields = parts_of(sent[0])
    assert fields["model"] == ["whisper-large-v3"]
    assert fields["response_format"] == ["verbose_json"]
    assert fields["language"] == ["he"]
    assert fields["temperature"] == ["0"]
    assert fields["timestamp_granularities[]"] == ["segment", "word"]
    assert fields["__file__"] == ["vocals.mp3"]


def test_the_key_travels_in_the_header_and_not_in_the_url(audio: Path, sent: list):
    """A key in a query string ends up in somebody's access log."""
    GroqTranscriber(api_key="secret-key").transcribe(audio)

    assert sent[0].headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in sent[0].full_url


def test_the_quota_headers_are_kept(audio: Path, sent: list):
    """Phase 0's rule: the published number is a claim, the header is a
    measurement. It found the Modal credit was $1 rather than $30 that way."""
    transcript = GroqTranscriber(api_key="test").transcribe(audio)

    assert transcript.rate_limit["x-ratelimit-remaining-requests"] == "1999"
    assert "content-type" not in transcript.rate_limit


def test_no_key_is_unavailable_rather_than_failed(audio: Path):
    """An operator's problem, not the recording's - the same distinction
    separation makes between unavailable and failed."""
    with pytest.raises(TranscriptionUnavailable):
        GroqTranscriber(api_key="").transcribe(audio)


def test_a_rejected_key_is_also_unavailable(audio: Path, monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr(transcription.urllib.request, "urlopen", refuse)

    with pytest.raises(TranscriptionUnavailable):
        GroqTranscriber(api_key="wrong").transcribe(audio)


def test_a_server_error_is_a_transcription_failure(audio: Path, monkeypatch):
    def fail(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO(b"boom"))

    monkeypatch.setattr(transcription.urllib.request, "urlopen", fail)

    with pytest.raises(TranscriptionError) as raised:
        GroqTranscriber(api_key="test").transcribe(audio)

    assert not isinstance(raised.value, TranscriptionUnavailable)


def test_a_file_too_large_is_refused_before_it_is_uploaded(audio: Path, monkeypatch, sent: list):
    """A clear refusal here beats a 413 from somebody else's server after
    thirty seconds of upload."""
    monkeypatch.setattr(transcription, "MAX_UPLOAD_BYTES", 4)

    with pytest.raises(TranscriptionError):
        GroqTranscriber(api_key="test").transcribe(audio)

    assert sent == [], "nothing should have been sent"


def test_a_missing_file_is_a_clear_error(tmp_path: Path):
    with pytest.raises(TranscriptionError):
        GroqTranscriber(api_key="test").transcribe(tmp_path / "nothing.mp3")


def test_a_file_with_no_extension_is_refused_before_it_is_sent(tmp_path: Path, sent: list):
    """T-3.10. The service reads the format from the filename, and answers a
    name with no suffix by listing the types it accepts - which reads as "your
    audio is wrong" for a file that is perfectly good. The object store's
    downloaded copy was named by a hash, so every cloud transcription failed
    this way while every local one worked."""
    audio = tmp_path / "29e30bfa4274c47e"
    audio.write_bytes(b"ID3")

    with pytest.raises(TranscriptionError, match="extension"):
        GroqTranscriber(api_key="test").transcribe(audio)

    assert sent == [], "nothing should have been sent"


def test_the_multipart_body_carries_the_bytes_unchanged(tmp_path: Path):
    """The one part of an HTTP client that is easy to get subtly wrong."""
    audio = tmp_path / "a.mp3"
    audio.write_bytes(bytes(range(256)))

    body, content_type = multipart([("model", "m")], "file", audio)

    assert "boundary=" in content_type
    assert bytes(range(256)) in body


def test_a_response_with_no_words_is_still_a_transcript():
    """Line-level is a normal outcome, not a failure (chapter 7)."""
    payload = dict(VERBOSE_JSON)
    payload.pop("words")

    transcript = parse_verbose_json(payload, backend="test")

    assert len(transcript.segments) == 2
    assert transcript.segments[0].words == []


def test_an_empty_segment_is_not_a_line():
    transcript = parse_verbose_json(
        {"segments": [{"start": 1, "end": 2, "text": "   "}]}, backend="test"
    )

    assert transcript.segments == []


def test_the_backend_is_chosen_by_name():
    assert get_transcriber("groq").name == "groq"
    assert isinstance(get_transcriber("none"), NoTranscriber)
    with pytest.raises(TranscriptionError):
        get_transcriber("something-else")


def test_the_none_backend_says_it_cannot_rather_than_finding_nothing(audio: Path):
    with pytest.raises(TranscriptionUnavailable):
        NoTranscriber().transcribe(audio)

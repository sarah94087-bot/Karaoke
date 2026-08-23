"""T-1.8's acceptance criterion: a client sees the steps change in real time.

Read as a real client would - a raw HTTP stream parsed as it arrives - rather
than by inspecting the generator. The framing is the part a browser's
EventSource is fussy about, and a test that skips it proves nothing about
whether a browser could read this.
"""

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.providers.separation import STEM_NAMES, Separated, SeparationError
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

UPLOAD = f"{API_PREFIX}/songs/upload"


class SlowSeparator:
    """Slow enough that a client can connect while the job is mid-flight."""

    name = "slow"

    def __init__(self, delay: float = 0.4, error: Exception | None = None) -> None:
        self.delay = delay
        self.error = error
        self.started = threading.Event()

    def separate(self, storage, source_key: str, targets: dict[str, str]) -> Separated:
        self.started.set()
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        with tempfile.TemporaryDirectory(prefix="stub-stems-") as tmp:
            stems = {}
            for name in STEM_NAMES:
                path = Path(tmp) / f"{name}.mp3"
                path.write_bytes(name.encode())
                stems[name] = storage.put(targets[name], path)
        return Separated(stems=stems, backend=self.name)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def build(storage: LocalStorage, separator: object) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)
    client.__enter__()
    app.state.runner.separator = separator
    app.state.runner.storage = storage
    return client


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Dedup would otherwise skip the second upload in a module."""


def synth(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate=44100",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def upload(client: TestClient, path: Path) -> str:
    with path.open("rb") as handle:
        response = client.post(UPLOAD, files={"file": (path.name, handle, "audio/mpeg")})
    return response.json()["job_id"]


def read_events(client: TestClient, job_id: str, limit: int = 40) -> list[tuple[str, dict]]:
    """Parse the stream the way EventSource does: blank line ends a message."""
    events: list[tuple[str, dict]] = []
    with client.stream("GET", f"{API_PREFIX}/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        name: str | None = None
        for line in response.iter_lines():
            if line.startswith(":") or line.startswith("retry:"):
                continue
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                assert name is not None, "data arrived without an event name"
                events.append((name, json.loads(line.removeprefix("data: "))))
                name = None
                if events[-1][0] in ("ready", "failed") or len(events) >= limit:
                    break
    return events


@pytest.fixture
def client(schema: None, storage: LocalStorage) -> Iterator[TestClient]:
    # Slow enough that a client connecting straight after the upload arrives
    # while the job is genuinely mid-flight, which is the case worth testing.
    running = build(storage, SlowSeparator(delay=0.5))
    yield running
    running.__exit__(None, None, None)


def test_a_client_sees_the_steps_change(client: TestClient, tmp_path: Path):
    """The acceptance criterion."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    events = read_events(client, job_id)

    assert events, "the stream produced nothing"
    assert events[0][0] == "snapshot", "the first message must say where things stand"
    assert events[0][1]["state"] == "running"
    assert events[-1][0] == "ready", events


def test_playable_arrives_before_ready(client: TestClient, tmp_path: Path):
    """Chapter 6 names this event specifically, and D-28 is the reason: it is
    the moment the user may start singing."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    names = [name for name, _ in read_events(client, job_id)]

    assert "playable" in names, names
    assert names.index("playable") < names.index("ready")


def test_the_playable_event_says_the_song_is_playable(client: TestClient, tmp_path: Path):
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    events = dict(read_events(client, job_id))

    assert events["playable"]["is_playable"] is True


def test_progress_only_goes_forward(client: TestClient, tmp_path: Path):
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    values = [payload["progress"] for _, payload in read_events(client, job_id)]

    assert values == sorted(values), values
    assert values[-1] == 100


def test_the_steps_are_named(client: TestClient, tmp_path: Path):
    """The progress screen renders these in Hebrew, so they have to arrive."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    steps = {payload["current_step"] for _, payload in read_events(client, job_id)}

    assert {"separating", "encoding"} <= steps, steps


def test_the_stream_closes_when_the_job_is_done(client: TestClient, tmp_path: Path):
    """A stream that stays open after `ready` holds a connection for nothing."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))
    read_events(client, job_id)

    events = read_events(client, job_id)

    assert len(events) == 1, "a finished job should say so once and close"


def test_reconnecting_to_a_finished_job_still_fires_the_ready_handler(
    client: TestClient, tmp_path: Path
):
    """The catch-up message is `ready`, not `snapshot`.

    A client that reconnects after the job finished would otherwise have to
    special-case the snapshot to notice; this way the same handler works however
    late it arrives.
    """
    job_id = upload(client, synth(tmp_path / "t.mp3"))
    read_events(client, job_id)

    events = read_events(client, job_id)

    assert events[0][0] == "ready"
    assert events[0][1]["state"] == "ready"


def test_a_late_client_is_told_where_things_stand(client: TestClient, tmp_path: Path):
    """Someone who opens the progress screen after the job started must not be
    left waiting for a change that may never come."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))
    read_events(client, job_id)

    events = read_events(client, job_id)

    assert events[0][1]["progress"] == 100
    assert events[0][1]["is_playable"] is True


def test_a_failure_reaches_the_stream_with_its_code(storage: LocalStorage, tmp_path: Path, schema):
    failing = build(storage, SlowSeparator(delay=0.05, error=SeparationError("the GPU went away")))
    try:
        job_id = upload(failing, synth(tmp_path / "t.mp3"))

        events = read_events(failing, job_id)

        assert events[-1][0] == "failed"
        assert events[-1][1]["error_code"] == "separation_failed"
    finally:
        failing.__exit__(None, None, None)


def test_streaming_an_unknown_job_is_a_404_with_a_code(client: TestClient):
    response = client.get(f"{API_PREFIX}/jobs/{uuid.uuid4()}/events")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_the_stream_is_not_cached_or_buffered(client: TestClient, tmp_path: Path):
    """A buffering proxy delivers the whole stream at the end, which is
    indistinguishable from the feature not working."""
    job_id = upload(client, synth(tmp_path / "t.mp3"))

    with client.stream("GET", f"{API_PREFIX}/jobs/{job_id}/events") as response:
        headers = response.headers
        response.close()

    assert "no-cache" in headers["cache-control"]
    assert headers["x-accel-buffering"] == "no"


def test_the_stream_is_documented(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert f"{API_PREFIX}/jobs/{{job_id}}/events" in paths

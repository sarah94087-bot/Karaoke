"""Jobs through the API, including what a restart looks like from outside.

The upload endpoint now starts a job, which is chapter 6's `POST /songs`
behaviour: the work begins and a job_id comes back immediately. The separator is
replaced with a stub so these stay fast; the real one has its own tests.
"""

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.core.enums import JobState
from packages.providers.separation import STEM_NAMES, Separated, SeparationError
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

UPLOAD = f"{API_PREFIX}/songs/upload"


class StubSeparator:
    name = "stub"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def separate(
        self, storage, source_key: str, targets: dict[str, str], on_started=None
    ) -> Separated:
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
    # The runner is built in the lifespan from configuration; swap in the stub
    # so the tests do not run Demucs.
    app.state.runner.separator = separator
    app.state.runner.storage = storage
    return client


@pytest.fixture
def client(schema: None, storage: LocalStorage) -> Iterator[TestClient]:
    running = build(storage, StubSeparator())
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Leave the library empty; dedup would otherwise skip the second upload."""


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


def upload(client: TestClient, path: Path):
    with path.open("rb") as handle:
        return client.post(UPLOAD, files={"file": (path.name, handle, "audio/mpeg")})


def wait_for(client: TestClient, job_id: str, states: set[str], tries: int = 100) -> dict:
    """Poll the status endpoint until the job settles.

    Polling rather than awaiting the task: this is the same thing the progress
    screen does before T-1.8 gives it a stream, so it is worth exercising.
    """
    import time

    for _ in range(tries):
        body = client.get(f"{API_PREFIX}/jobs/{job_id}").json()
        if body["state"] in states:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job never reached {states}: {body}")


def test_uploading_starts_a_job_and_returns_its_id(client: TestClient, tmp_path: Path):
    """Chapter 6: creating a song returns a job_id immediately."""
    body = upload(client, synth(tmp_path / "t.mp3")).json()

    assert body["job_id"], "no job was started for the upload"
    uuid.UUID(body["job_id"])


def test_a_job_reaches_ready_and_the_song_becomes_playable(client: TestClient, tmp_path: Path):
    job_id = upload(client, synth(tmp_path / "t.mp3")).json()["job_id"]

    final = wait_for(client, job_id, {JobState.READY, JobState.FAILED})

    assert final["state"] == JobState.READY, final
    assert final["progress"] == 100
    assert final["is_playable"] is True


def test_the_status_endpoint_reports_the_step(client: TestClient, tmp_path: Path):
    job_id = upload(client, synth(tmp_path / "t.mp3")).json()["job_id"]

    final = wait_for(client, job_id, {JobState.READY, JobState.FAILED})

    assert final["current_step"] is None, "a finished job is not on a step"
    assert final["attempts"] == 1


def test_an_unknown_job_is_a_404_with_a_code(client: TestClient):
    response = client.get(f"{API_PREFIX}/jobs/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_a_failed_job_reports_its_code(storage: LocalStorage, schema: None, tmp_path: Path):
    client = build(storage, StubSeparator(SeparationError("the GPU went away")))
    try:
        job_id = upload(client, synth(tmp_path / "t.mp3")).json()["job_id"]

        final = wait_for(client, job_id, {JobState.READY, JobState.FAILED})

        assert final["state"] == JobState.FAILED
        assert final["error_code"] == "separation_failed"
        assert final["is_playable"] is False
    finally:
        client.__exit__(None, None, None)


def test_a_ready_job_cannot_be_retried(client: TestClient, tmp_path: Path):
    """Retrying a success would spend GPU credit to produce what already exists."""
    job_id = upload(client, synth(tmp_path / "t.mp3")).json()["job_id"]
    wait_for(client, job_id, {JobState.READY, JobState.FAILED})

    response = client.post(f"{API_PREFIX}/jobs/{job_id}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_not_retryable"


def test_a_failed_job_can_be_retried_and_then_succeed(
    storage: LocalStorage, schema: None, tmp_path: Path
):
    """Chapter 7 makes the retry the user's decision; this is that decision
    working."""
    failing = build(storage, StubSeparator(SeparationError("transient")))
    try:
        job_id = upload(failing, synth(tmp_path / "t.mp3")).json()["job_id"]
        wait_for(failing, job_id, {JobState.FAILED})
    finally:
        failing.__exit__(None, None, None)

    working = build(storage, StubSeparator())
    try:
        response = working.post(f"{API_PREFIX}/jobs/{job_id}/retry")
        assert response.status_code == 200

        final = wait_for(working, job_id, {JobState.READY, JobState.FAILED})
        assert final["state"] == JobState.READY
        assert final["attempts"] == 2, "the deliberate retry was not counted"
    finally:
        working.__exit__(None, None, None)


def test_a_restart_does_not_leave_a_job_running_forever(
    storage: LocalStorage, schema: None, tmp_path: Path, connect
):
    """The acceptance criterion, from outside: kill the process mid-job and the
    status a user is polling has to change to something they can act on."""
    # A job stuck in `running`, exactly as a crash would leave it.
    client = build(storage, StubSeparator())
    try:
        job_id = upload(client, synth(tmp_path / "t.mp3")).json()["job_id"]
        wait_for(client, job_id, {JobState.READY, JobState.FAILED})
        with connect() as conn:
            conn.execute(
                "update jobs set state = 'running', error_code = null, progress = 50 where id = %s",
                [job_id],
            )
    finally:
        client.__exit__(None, None, None)

    # The restart.
    restarted = build(storage, StubSeparator())
    try:
        body = restarted.get(f"{API_PREFIX}/jobs/{job_id}").json()

        assert body["state"] == JobState.FAILED
        assert body["error_code"] == "interrupted"
    finally:
        restarted.__exit__(None, None, None)


def test_the_job_endpoints_are_documented(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert f"{API_PREFIX}/jobs/{{job_id}}" in paths
    assert f"{API_PREFIX}/jobs/{{job_id}}/retry" in paths

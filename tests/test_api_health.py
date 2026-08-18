"""T-1.2: the FastAPI skeleton answers, and the docs load.

The health endpoint is the keep-alive target (D-26), so these tests guard the
two properties that matter about it: it answers, and it stays cheap.
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.main import create_app
from apps.api.request_id import HEADER
from apps.api.routers import system as system_module


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize("path", [f"{API_PREFIX}/system/health", "/system/health"])
def test_health_answers_on_both_paths(client: TestClient, path: str):
    """The prefixed path is the documented one; the bare path is what the
    container HEALTHCHECK and the keep-alive cron are pointed at."""
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["uptime_sec"] >= 0


def test_health_is_cheap():
    """No database, no storage, no outbound call.

    A keep-alive that wakes Postgres a few hundred times a day defeats its own
    purpose, so this asserts on the imports rather than trusting the comment in
    the module.
    """
    imported = set(vars(system_module))
    forbidden = {"db", "session", "engine", "storage", "httpx", "requests"}

    assert not (imported & forbidden), f"health module reaches for {imported & forbidden}"


def test_every_response_carries_a_request_id(client: TestClient):
    response = client.get(f"{API_PREFIX}/system/health")

    assert response.headers[HEADER]


def test_an_inbound_request_id_is_honoured(client: TestClient):
    """So a report from the web app and a line in the API log share one string."""
    response = client.get(f"{API_PREFIX}/system/health", headers={HEADER: "abc123"})

    assert response.headers[HEADER] == "abc123"


def test_request_ids_differ_between_requests(client: TestClient):
    first = client.get(f"{API_PREFIX}/system/health").headers[HEADER]
    second = client.get(f"{API_PREFIX}/system/health").headers[HEADER]

    assert first != second


def test_errors_carry_the_code_and_request_id(client: TestClient):
    response = client.get(f"{API_PREFIX}/system/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "http_404"
    assert body["request_id"] == response.headers[HEADER]


def test_docs_load(client: TestClient):
    for path in ("/docs", "/redoc"):
        assert client.get(path).status_code == 200, f"{path} did not load"


def test_openapi_documents_the_prefixed_health_path(client: TestClient):
    schema = client.get("/openapi.json").json()

    assert f"{API_PREFIX}/system/health" in schema["paths"]
    # The unprefixed alias is an operational detail, not part of the API.
    assert "/system/health" not in schema["paths"]


def test_an_unhandled_error_still_carries_a_request_id():
    """The 500 is the response you most need to be able to trace back to a log."""
    app = create_app()

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_boom", headers={HEADER: "trace-me"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["request_id"] == "trace-me"
    assert response.headers[HEADER] == "trace-me"
    assert "kaboom" not in response.text, "internal detail must not leak to the client"

"""T-3.12: what gets reported, what does not, and what happens with no DSN.

The value of these is mostly the negative cases. Monitoring is the one feature
whose failure is silent by construction - if it reports nothing, everything
looks calm - so the things worth pinning down are that it stays off when it
should, that it never breaks the request it is reporting on, and that the two
options which cost money or privacy are the ones this project turned off.
"""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.providers import monitoring


@pytest.fixture(autouse=True)
def quiet() -> None:
    monitoring._reset_for_tests(False)


class FakeSdk(types.ModuleType):
    """Stands in for `sentry_sdk`, recording what it was asked to do."""

    def __init__(self) -> None:
        super().__init__("sentry_sdk")
        self.init_kwargs: dict = {}
        self.captured: list[BaseException] = []
        self.tags: dict[str, str] = {}
        sdk = self

        class Scope:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def set_tag(self, key: str, value: str) -> None:
                sdk.tags[key] = value

        self.new_scope = Scope

    def init(self, **kwargs) -> None:
        self.init_kwargs = kwargs

    def capture_exception(self, exc: BaseException) -> None:
        self.captured.append(exc)


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> FakeSdk:
    fake = FakeSdk()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    return fake


def test_no_dsn_means_no_monitoring(sdk: FakeSdk):
    """Chapter 11: the whole product runs on a machine with no accounts on it."""
    assert monitoring.init_monitoring("", "local") is False
    assert sdk.init_kwargs == {}

    monitoring.capture(RuntimeError("nobody hears this"))

    assert sdk.captured == []


def test_a_dsn_starts_the_sdk_with_tracing_and_pii_off(sdk: FakeSdk):
    """Both are quota and privacy decisions rather than defaults: the free plan
    is 5,000 errors a month, and a report is not a place for a user's token or
    their song titles."""
    assert monitoring.init_monitoring("https://k@example.ingest.sentry.io/1", "production") is True

    assert sdk.init_kwargs["traces_sample_rate"] == 0.0
    assert sdk.init_kwargs["send_default_pii"] is False
    assert sdk.init_kwargs["environment"] == "production"


def test_a_missing_sdk_is_not_a_crash(monkeypatch: pytest.MonkeyPatch):
    """The local venv was built by hand in phase 0 and is not installed from
    pyproject, so "DSN set, package absent" is an ordinary developer state."""
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    assert monitoring.init_monitoring("https://k@example.ingest.sentry.io/1", "local") is False


def test_captured_errors_carry_their_tags(sdk: FakeSdk):
    monitoring.init_monitoring("https://k@example.ingest.sentry.io/1", "production")
    error = RuntimeError("boom")

    monitoring.capture(error, request_id="abc123", path="/api/v1/songs")

    assert sdk.captured == [error]
    assert sdk.tags == {"request_id": "abc123", "path": "/api/v1/songs"}


def test_a_broken_reporter_does_not_break_the_request(sdk: FakeSdk, caplog):
    """Reporting runs inside the handler for a request that has already gone
    wrong. Failing there would turn one bad response into no response."""
    monitoring.init_monitoring("https://k@example.ingest.sentry.io/1", "production")

    def explode(_: BaseException) -> None:
        raise RuntimeError("the monitoring service is down")

    sdk.capture_exception = explode  # type: ignore[method-assign]

    monitoring.capture(RuntimeError("boom"))  # must not raise


# --- the probe --------------------------------------------------------------


def test_the_error_probe_is_a_404_without_a_token():
    """Unset is what a skipped Render variable also looks like, so the safe
    state has to be the default one."""
    client = TestClient(create_app())

    assert client.get("/api/v1/system/error").status_code == 404
    assert client.get("/api/v1/system/error?token=guess").status_code == 404


def test_the_error_probe_raises_when_the_token_matches(monkeypatch: pytest.MonkeyPatch):
    """Chapter 14's checklist item, in one request: a deliberate error."""
    import dataclasses

    from apps.api.config import Settings
    from apps.api.routers import system as system_router

    # `settings` is frozen, so the configuration is replaced rather than edited.
    monkeypatch.setattr(
        system_router, "settings", dataclasses.replace(Settings(), error_probe_token="s3cret")
    )
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/v1/system/error?token=s3cret")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # And it is traceable: the 500 path is the one that reports (T-1.2 put the
    # id on every response for this reason).
    assert response.headers["X-Request-ID"]

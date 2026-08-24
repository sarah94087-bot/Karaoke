"""T-3.10: what a production deployment refuses to start without.

Both of these are the same argument, and both were paid for. A missing
environment variable is discovered either at startup, in one line, or by a user
whose song fails for a reason nothing on their screen can explain - and on
Render the second is the *likely* one, because a variable declared `sync: false`
and left blank is skipped without a word. That has now happened twice on this
project: `KARUKI_CORS_ORIGINS`, which answered every preflight with a 400, and
`MODAL_TOKEN_ID`, which failed every job at the separation step.

`settings` is frozen, so each test builds the environment it means and rebuilds
the object from it.
"""

import dataclasses

import pytest

from apps.api import main
from apps.api.config import Settings


def production(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Point `main.settings` at a production configuration with these values."""
    values: dict[str, str] = {
        "environment": "production",
        "supabase_url": "https://project.supabase.co",
        **overrides,
    }
    settings = dataclasses.replace(Settings(), **values)
    monkeypatch.setattr(main, "settings", settings)


def test_production_without_an_identity_provider_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
):
    """T-3.7: no SUPABASE_URL means every request is the same user, so the
    library everybody sees is everybody's."""
    production(monkeypatch, supabase_url="")

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        main.create_app()


def test_the_modal_backend_without_a_token_refuses_to_start(monkeypatch: pytest.MonkeyPatch):
    """The image has no torch by design (T-1.6), so `modal` is the only backend
    a deployment can separate with. Without credentials it is not a slow
    deployment, it is one that cannot process a single song."""
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    production(monkeypatch, separation_backend="modal")

    with pytest.raises(RuntimeError, match="MODAL_TOKEN_ID"):
        main.create_app()


def test_a_token_is_enough_to_start(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-1")
    production(monkeypatch, separation_backend="modal")

    assert main.create_app() is not None


def test_the_local_backend_needs_no_gpu_credentials(monkeypatch: pytest.MonkeyPatch):
    """Chapter 11's escape route: a deployment that separates on its own CPU is
    a choice somebody can make, and it has no Modal account behind it."""
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    production(monkeypatch, separation_backend="local")

    assert main.create_app() is not None

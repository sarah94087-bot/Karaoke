"""T-1.3: the local environment is API + Postgres from one command.

These are static checks on the compose file and the Dockerfile: CI on a free
tier does not get a Docker daemon, and running one here would put a multi-minute
image build in front of every test run. They are not a substitute for
`docker compose up` - that was done by hand when this landed - but they catch
the rot that happens in between: a renamed service, a healthcheck pointing at a
path that no longer exists, a dependency group the image no longer installs.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = ROOT / "infra" / "docker" / "compose.yaml"
DOCKERFILE_PATH = ROOT / "infra" / "docker" / "Dockerfile"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def instructions(dockerfile: str) -> list[str]:
    """The Dockerfile as logical instructions, backslash continuations joined."""
    joined = dockerfile.replace("\\\n", " ")
    return [ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.startswith("#")]


def test_compose_brings_up_api_and_db(compose: dict):
    assert set(compose["services"]) == {"api", "db"}


def test_db_is_postgres(compose: dict):
    assert compose["services"]["db"]["image"].startswith("postgres:")


def test_db_data_survives_a_restart(compose: dict):
    """Losing the database on every `down` would make T-1.4's migrations
    untestable in the one way that matters: that they run twice."""
    db = compose["services"]["db"]
    assert "db_data" in compose["volumes"]
    assert any(v.startswith("db_data:") for v in db["volumes"])


def test_api_waits_for_a_healthy_db(compose: dict):
    """`depends_on` alone only waits for the container to start, which is not
    the same as Postgres being ready to accept a connection."""
    assert "healthcheck" in compose["services"]["db"]
    assert compose["services"]["api"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_api_is_told_where_the_db_is(compose: dict):
    url = compose["services"]["api"]["environment"]["DATABASE_URL"]

    assert url.startswith("postgresql://")
    assert "@db:" in url, "must address the db service by its compose name"


def test_api_builds_from_the_repository_root(compose: dict):
    """The Dockerfile copies packages/ as well as apps/, so the context cannot
    be infra/docker."""
    build = compose["services"]["api"]["build"]

    assert build["dockerfile"] == "infra/docker/Dockerfile"
    assert (COMPOSE_PATH.parent / build["context"]).resolve() == ROOT


def test_healthchecks_use_the_unprefixed_path(dockerfile: str):
    """The container healthcheck is pointed at /system/health rather than the
    documented /api/v1 path, so a version prefix change does not silently break
    every container's liveness."""
    assert "/system/health" in dockerfile
    assert "/api/v1" not in dockerfile


def test_dependencies_are_installed_from_the_api_group(dockerfile: str):
    """The image generates its requirements from pyproject rather than keeping a
    parallel file, so there is nothing to drift. This checks both ends: the
    generator still resolves, and the Dockerfile still calls it."""
    generated = subprocess.run(
        [sys.executable, str(ROOT / "infra" / "docker" / "requirements.py"), "api"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert any(dep.startswith("fastapi") for dep in generated)
    assert any(dep.startswith("uvicorn") for dep in generated)
    assert "requirements.py api" in dockerfile


def test_the_requirements_generator_fails_loudly_on_a_bad_group():
    """A silent empty requirements file would produce an image that builds and
    then dies on the first import."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "infra" / "docker" / "requirements.py"), "nope"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0


def test_dependencies_are_a_separate_layer_from_the_code(dockerfile: str):
    """Chapter 10 asks for this explicitly: a router edit must not reinstall
    torch-sized wheels."""
    lines = instructions(dockerfile)
    install = next(i for i, ln in enumerate(lines) if ln.startswith("RUN") and "pip install" in ln)
    copy_code = next(i for i, ln in enumerate(lines) if ln.startswith("COPY apps/"))

    assert install < copy_code, "pip install must come before the code is copied"


def test_the_image_does_not_run_as_root(dockerfile: str):
    assert any(ln.startswith("USER ") for ln in instructions(dockerfile))


def test_the_build_context_excludes_the_phase0_audio(dockerfile: str):
    """~1GB of stems and inputs live under the build context root."""
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()

    for heavy in ("input/", "output/", ".venv/", ".git/"):
        assert heavy in ignored, f"{heavy} must not enter the build context"


def test_secrets_do_not_enter_the_image():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()

    for secret in (".env", "certs/"):
        assert secret in ignored

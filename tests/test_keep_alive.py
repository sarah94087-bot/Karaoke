"""T-3.11: the keep-alive workflow, checked the way the compose file is.

Static checks on a file nothing else can test: the schedule only runs on
GitHub, and by the time a wrong interval shows up it shows up as a user
waiting half a minute for a page. What can be checked here is that the numbers
still say what the reasoning below them says.

The numbers themselves come from two measurements: Render stops a free
instance after about **15 minutes** without a request, and a cold start of this
API took **32.7s** against 0.5s warm (T-3.10).
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "keep-alive.yml"

# Render's idle timeout. The ping interval has to leave room for more than one
# ping inside it, because GitHub's schedule is best-effort and late runs are
# ordinary rather than exceptional.
RENDER_IDLE_MINUTES = 15


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def schedules(workflow: dict) -> list[str]:
    # `on:` is YAML's `True` once parsed - the one place this file has to know
    # that a bare `on` is a boolean and not a string.
    triggers = workflow.get("on", workflow.get(True, {}))
    return [entry["cron"] for entry in triggers.get("schedule", [])]


def test_the_ping_fits_at_least_twice_inside_the_idle_window(workflow: dict):
    """One late run must not be enough to let the service fall asleep."""
    crons = schedules(workflow)

    assert crons, "no schedule: the workflow would only ever run by hand"
    for cron in crons:
        minutes = re.fullmatch(r"\*/(\d+) \* \* \* \*", cron)
        assert minutes, f"unexpected cron shape: {cron}"
        assert int(minutes.group(1)) * 2 <= RENDER_IDLE_MINUTES


def test_it_pings_the_unprefixed_health_path(workflow: dict):
    """T-1.2 keeps `/system/health` outside the versioned prefix precisely so
    the healthcheck and this cron are configured once and survive a change to
    it."""
    step = workflow["jobs"]["ping"]["steps"][0]
    url = step["env"]["URL"]

    assert url.startswith("https://")
    assert url.endswith("/system/health")
    assert "/api/v1" not in url


def test_it_can_be_run_by_hand(workflow: dict):
    """After a deploy, waiting up to five minutes to find out whether the
    service answers is five minutes of not knowing."""
    triggers = workflow.get("on", workflow.get(True, {}))

    assert "workflow_dispatch" in triggers


def test_the_ping_gets_no_permissions_on_the_repository(workflow: dict):
    """It reads one public URL. A scheduled job with a write token is a
    standing credential for a task that needs none."""
    assert workflow["permissions"] == {}


def test_one_ping_at_a_time(workflow: dict):
    """A backlog of queued pings after an outage would arrive as a burst on a
    service that is already having a bad time."""
    assert workflow["concurrency"]["group"] == "keep-alive"

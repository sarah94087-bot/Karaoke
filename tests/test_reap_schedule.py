"""T-3.13: chapter 9's deletion is actually scheduled, and safely.

The endpoint's behaviour is tested in `test_retention.py`. What is checked here
is the schedule itself, because a retention policy that nothing calls is a
policy in name only - and because the two mistakes available in this file are
both quiet ones: a token written into the workflow, and a schedule so eager
that a bug in the rule empties a library before anybody looks.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "reap.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_it_runs_daily(workflow: dict):
    """Daily against a six-month rule: never far behind, and cheap. This is
    also the one place GitHub's unreliable scheduler (T-3.11) does not matter -
    a run that lands hours late, or tomorrow, changes nothing."""
    triggers = workflow.get("on", workflow.get(True, {}))
    crons = [entry["cron"] for entry in triggers["schedule"]]

    assert len(crons) == 1
    minute, hour, *rest = crons[0].split()
    assert minute.isdigit() and hour.isdigit(), f"not a daily time: {crons[0]}"
    assert rest == ["*", "*", "*"]


def test_the_token_is_a_secret_and_not_in_the_file(workflow: dict):
    """This route deletes audio. A token in the repository would be a token
    anybody could read out of it."""
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    step = workflow["jobs"]["reap"]["steps"][0]

    assert step["env"]["TOKEN"] == "${{ secrets.KARUKI_MAINTENANCE_TOKEN }}"
    assert not re.search(r"token=[A-Za-z0-9+/=]{8,}", raw), "a literal token in the workflow"


def test_it_asks_the_deployment_and_not_the_database(workflow: dict):
    """The credentials for the database are not on a GitHub runner and should
    never be: the API already has them, so the schedule is one HTTP call."""
    step = workflow["jobs"]["reap"]["steps"][0]

    assert step["env"]["API"].startswith("https://")
    assert "/api/v1/system/reap" in step["run"]
    assert "apply=true" in step["run"], "a schedule that only ever describes the work"

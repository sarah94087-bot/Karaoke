"""Every code the API emits has Hebrew a user can read.

This is the test that makes the `code` field from T-1.2 worth having. The API
answers with `{"error": {"code": ...}}` on the theory that the web app turns the
code into Hebrew; nothing enforced that theory until here, and the failure it
prevents is the worst kind - a user seeing a blank space, or a raw
`separation_unavailable`, at the moment something has already gone wrong.

It runs from the Python side deliberately. The codes are defined here, so this
is where a new one is added and forgotten.
"""

import json
import re
from pathlib import Path

import pytest

from packages.core.enums import JobState, JobStep

ROOT = Path(__file__).resolve().parent.parent
DICTIONARIES = ROOT / "apps" / "web" / "src" / "i18n" / "dictionaries"

# Codes raised as `ApiError(...)` or `PipelineError(...)`, found by reading the
# source rather than by listing them here - a list would be the thing that goes
# stale, which is exactly what this file exists to catch.
CODE_CALL = re.compile(
    r"(?:ApiError|PipelineError|LyricsError|SourceError|SourceUnavailable)"
    r"\(\s*[\"']([a-z_]+)[\"']"
)
AUDIO_ERROR = re.compile(r"AudioError\(\s*[\"']([a-z_]+)[\"']")

SEARCHED = ("apps/api", "packages")

# Prefixed codes for framework errors (`http_404` and friends) are generated,
# not written, and the web app falls back to `errors.unknown` for them.
GENERATED_PREFIXES = ("http_",)


def dictionary(name: str) -> dict:
    path = DICTIONARIES / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{path} is missing; the web app has not been set up here")
    return json.loads(path.read_text(encoding="utf-8"))


def raised_codes() -> set[str]:
    found: set[str] = set()
    for area in SEARCHED:
        for source in (ROOT / area).rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            found.update(CODE_CALL.findall(text))
            found.update(AUDIO_ERROR.findall(text))
    return {code for code in found if not code.startswith(GENERATED_PREFIXES)}


def test_the_codes_were_actually_found():
    """A regex that matches nothing would make every test below pass."""
    codes = raised_codes()

    assert len(codes) > 10, f"only found {codes}; the search is not working"
    assert "song_too_long" in codes
    assert "separation_unavailable" in codes
    assert "invalid_lyrics" in codes


@pytest.mark.parametrize("language", ["he", "en"])
def test_every_error_code_has_a_message(language: str):
    messages = dictionary(language)["errors"]

    missing = sorted(code for code in raised_codes() if code not in messages)

    assert missing == [], f"no {language} text for: {', '.join(missing)}"


def test_there_is_a_fallback_for_a_code_the_web_app_does_not_know():
    """New codes will be added faster than translations. The user should still
    see a sentence rather than a blank."""
    assert dictionary("he")["errors"]["unknown"]


@pytest.mark.parametrize("language", ["he", "en"])
def test_every_job_step_has_a_name(language: str):
    """Chapter 8's progress screen names the live steps in Hebrew."""
    steps = dictionary(language)["job"]["step"]

    missing = sorted(str(step) for step in JobStep if str(step) not in steps)

    assert missing == [], f"no {language} name for: {', '.join(missing)}"


@pytest.mark.parametrize("language", ["he", "en"])
def test_every_job_state_has_a_name(language: str):
    """The library screen colours a row by this."""
    states = dictionary(language)["job"]["state"]

    missing = sorted(str(state) for state in JobState if str(state) not in states)

    assert missing == [], f"no {language} name for: {', '.join(missing)}"


def test_no_translation_is_left_untranslated_in_hebrew():
    """A key copied from English and not translated reads as finished work."""
    hebrew = re.compile(r"[֐-׿]")
    messages = dictionary("he")["errors"]

    without = sorted(code for code, text in messages.items() if not hebrew.search(text))

    assert without == [], f"these are not in Hebrew: {', '.join(without)}"


def test_the_playable_moment_has_words():
    """D-28's whole point is a button that lights up before the job is done, and
    it needs something written on it."""
    assert dictionary("he")["job"]["playable"]

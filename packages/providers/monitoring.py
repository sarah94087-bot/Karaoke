"""Error tracking (D-24, T-3.12), behind the same kind of seam as everything else.

Chapter 9 budgets one instance with no operator watching it, and chapter 11
requires the whole product to run on a machine with no accounts at all. Both
are satisfied the same way as `GROQ_API_KEY`: **no DSN means no monitoring**,
silently and by design, and nothing above this module knows whether errors are
being reported anywhere.

Why the real SDK here and a hand-written reporter in the browser
(`apps/web/src/lib/monitoring.ts`): the two sides are not the same trade. In
the API this is one small dependency in an image that already carries pyjwt,
and what it buys is stack traces, local variables and ASGI context on the one
thing you cannot reproduce - an error that happened once, to somebody else, in
production. In the browser it would be a large dependency added to an app whose
three runtime packages are the point (T-1.9), for an envelope that is a POST
with two JSON lines in it.

What is deliberately off:
- **`traces_sample_rate=0`.** D-24 asks for error tracking and product
  analytics; performance tracing is a third thing, with its own quota, and the
  free plan's 5,000 errors a month is what has to last.
- **`send_default_pii=False`.** Song titles are the user's words and the
  request carries their token. An error report is not a place for either.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("karuki.monitoring")

# Named rather than inline so the test that guards them is checking the values
# that are actually sent, not a copy of them.
SDK_OPTIONS: dict[str, Any] = {
    "traces_sample_rate": 0.0,
    "send_default_pii": False,
}

_enabled = False


def init_monitoring(dsn: str, environment: str, release: str | None = None) -> bool:
    """Start reporting, if there is somewhere to report to and something to do it.

    Returns whether monitoring is on, which is worth one line in the startup
    log: "errors are going nowhere" is a thing an operator should be told once,
    rather than discover the first time they go looking for an error.
    """
    global _enabled
    _enabled = False
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        # The local venv was built by hand in phase 0 and is not installed from
        # pyproject (see the note in CLAUDE.md), so this is a normal state on a
        # developer machine and must not be a crash.
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed; errors stay local")
        return False

    sentry_sdk.init(dsn=dsn, environment=environment, release=release, **SDK_OPTIONS)
    _enabled = True
    return True


def capture(exc: BaseException, **tags: str) -> None:
    """Report an exception that has already been handled.

    Every 500 in this service is turned into a response by the middleware
    (T-1.2), which means the exception never reaches the SDK's own ASGI
    handler - it has been dealt with by the time anything else could see it.
    So the reporting is explicit, and it carries the `request_id` the user was
    shown, which is what makes an error in the dashboard and a screenshot from
    a person the same incident.
    """
    if not _enabled:
        return
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for key, value in tags.items():
                scope.set_tag(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover - reporting must never break the request
        log.exception("could not report an error to the monitoring service")


def is_enabled() -> bool:
    return _enabled


def _reset_for_tests(enabled: bool = False) -> None:
    global _enabled
    _enabled = enabled

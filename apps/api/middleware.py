"""Cross-cutting request handling."""

import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .errors import error_body, json_error
from .request_id import HEADER, request_id_var

log = logging.getLogger("karuki.api")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns an id to every request and echoes it back on the response.

    An inbound `X-Request-ID` is honoured so that a proxy or the web app can
    supply its own id and have the two sides of a report line up.

    Unhandled exceptions are turned into a 500 here rather than by an app-level
    handler, because an app-level handler runs *outside* this middleware and
    would therefore produce the one response in the service with no request_id
    on it - exactly the response you most need to be able to trace.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(HEADER, "").strip()
        request_id = incoming[:64] or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled error [request_id=%s] %s %s",
                request_id,
                request.method,
                request.url.path,
            )
            # Generic on purpose: the detail belongs in the log above,
            # correlated by request_id, not in a response a user can read.
            response = json_error(
                500, error_body("internal_error", "something went wrong on our side")
            )
        finally:
            request_id_var.reset(token)
        response.headers[HEADER] = request_id
        return response

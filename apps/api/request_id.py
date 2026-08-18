"""Request correlation.

Chapter 6 asks for a `request_id` on every response. It is the only thing that
makes a user's "it failed" reportable: the id is in the response body, in the
response header, and (from T-1.7 onwards) on the job row, so one string ties the
browser, the API log and the remote GPU call together.

This module holds only the primitives, so that both the middleware that assigns
the id and the error handlers that quote it can import it without a cycle.
"""

from contextvars import ContextVar

HEADER = "X-Request-ID"

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    """The id of the request being served, or "" outside a request."""
    return request_id_var.get()

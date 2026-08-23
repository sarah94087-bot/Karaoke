"""One error shape for the whole API.

Chapter 9 is emphatic that a user must never see a silent stop that looks like a
fault, so failures carry a machine-readable `code` the web app maps to Hebrew
text, plus the `request_id` to quote in a report.
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .request_id import HEADER, current_request_id


class ApiError(Exception):
    """A failure with a code the web app can turn into Hebrew.

    HTTPException carries only a status and a string; chapter 9 needs the
    machine-readable code to survive to the client, because "you have run out of
    quota" and "that file is not audio" are both 400s and want very different
    screens.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # Numbers the screen needs to say something specific - "9 of 10 songs
        # this month" rather than "quota exceeded" (D-30). Absent on every other
        # error, which is why it is optional rather than an empty dict.
        self.details = details


def error_body(code: str, message: str, **extra: object) -> dict[str, object]:
    return {
        "error": {"code": code, "message": message, **extra},
        "request_id": current_request_id(),
    }


def json_error(status_code: int, body: dict[str, object]) -> JSONResponse:
    request_id = current_request_id()
    headers = {HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    """Replaces FastAPI's default `{"detail": ...}` bodies with the shape above.

    Registered against Starlette's HTTPException rather than FastAPI's subclass
    so that framework-raised errors - a 404 for an unrouted path, a 405 - are
    covered too, not only the ones our own handlers raise.
    """

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        extra = {"details": exc.details} if exc.details else {}
        return json_error(exc.status_code, error_body(exc.code, exc.message, **extra))

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return json_error(exc.status_code, error_body(f"http_{exc.status_code}", detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return json_error(
            422,
            error_body(
                "invalid_request",
                "the request body or query is invalid",
                fields=exc.errors(),
            ),
        )

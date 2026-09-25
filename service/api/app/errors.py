"""The error envelope of contracts/hub-api.md: `{error, message, ...details}`.

Messages are Bahasa; the web app shows them as they are. Codes are stable and the
web app branches on them (no_access → /no-access, conflict → reload toast,
ai_cap_reached → disabled Intake).
"""
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class HubError(Exception):
    status = 400
    code = "invalid"

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.message = message
        self.details = details


class Unauthenticated(HubError):
    status, code = 401, "unauthenticated"


class NoAccess(HubError):
    status, code = 403, "no_access"


class Forbidden(HubError):
    status, code = 403, "forbidden"


class NotFound(HubError):
    status, code = 404, "not_found"


class Conflict(HubError):
    status, code = 409, "conflict"


class Invalid(HubError):
    status, code = 422, "invalid"


class AiCapReached(HubError):
    status, code = 402, "ai_cap_reached"


def check_version(expected: Optional[int], actual: int, current: Dict[str, Any]) -> None:
    """Optimistic concurrency (FR-044): a stale version is refused with the current state."""
    if expected is None:
        raise Invalid("Versi data wajib dikirim.")
    if expected != actual:
        raise Conflict("Data sudah diubah orang lain. Muat ulang lalu coba lagi.", current=current)


def install(app: FastAPI) -> None:
    @app.exception_handler(HubError)
    async def _hub_error(_: Request, exc: HubError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "message": exc.message, **exc.details},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid", "message": "Data tidak valid.", "fields": exc.errors()},
        )

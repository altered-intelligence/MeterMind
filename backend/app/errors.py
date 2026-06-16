"""Consistent API error responses.

All app-level errors serialize to a single envelope::

    {"error": {"code": "<code>", "message": "<human message>"}}

with one of the stable ``ErrorCode`` values. Request-validation failures (FastAPI 422)
are mapped to ``validation_failed`` in the same shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    INVALID_PDF = "invalid_pdf"
    PARSER_FAILED = "parser_failed"
    DUPLICATE_BILL = "duplicate_bill"
    DATABASE_ERROR = "database_error"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    VALIDATION_FAILED = "validation_failed"


class ApiError(Exception):
    """Raise to return a structured error envelope with the given code + HTTP status."""

    def __init__(self, code: ErrorCode, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class DuplicateBillError(Exception):
    """Service-layer signal that an identical bill already exists (mapped to duplicate_bill).

    Carries the id of the bill it duplicates (when known) so a bulk import can link the
    duplicate row back to the already-stored bill, plus a human-readable, deterministic
    ``reason`` explaining *which* rule matched.
    """

    def __init__(self, message: str, *, existing_bill_id: Optional[int] = None) -> None:
        super().__init__(message)
        self.existing_bill_id = existing_bill_id
        self.reason = message


def _envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code.value, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_envelope(ErrorCode.VALIDATION_FAILED.value, "Request validation failed."),
        )

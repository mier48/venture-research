"""Formato de error único de la API: application/problem+json (8.1)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None):
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def problem(status: int, code: str, title: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return problem(exc.status, exc.code, exc.title, exc.detail)


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return problem(exc.status_code, code, str(exc.detail))
    return problem(500, "internal_error", "Error interno")


def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return problem(422, "validation_error", "La solicitud no supera la validación")

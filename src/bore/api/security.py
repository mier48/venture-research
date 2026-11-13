"""Seguridad web (10.3/10.4): validación de Host y Origin, Content-Type
application/json obligatorio en POST con cuerpo, y token CSRF para la UI."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

from bore.api.errors import problem
from bore.config import Settings

log = logging.getLogger("bore.security")

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token(settings: Settings) -> str:
    """Token CSRF determinista firmado con BORE_SECRET_KEY. Si falta la clave se
    genera una aleatoria por proceso (warning en arranque)."""
    key = settings.bore_secret_key or _process_secret()
    return hmac.new(key.encode(), b"bore-csrf", hashlib.sha256).hexdigest()


_SECRET: str | None = None


def _process_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = secrets.token_hex(32)
        log.warning(
            "BORE_SECRET_KEY no configurada: clave CSRF aleatoria por proceso "
            "(los formularios emitidos en otro proceso serán rechazados)"
        )
    return _SECRET


def csrf_valid(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, csrf_token(settings))


def _host_without_port(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


class SecurityMiddleware:
    """ASGI puro (sin BaseHTTPMiddleware) aplicado a toda la app."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        method = scope["method"]
        path = scope["path"]

        # Host permitido (todas las peticiones).
        host = _host_without_port(headers.get("host", ""))
        allowed_hosts = self.settings.allowed_hosts
        if allowed_hosts and host not in allowed_hosts:
            await problem(403, "forbidden_host", "Host no permitido")(scope, receive, send)
            return

        # Origin (si viene) en peticiones que cambian estado.
        if method in _STATE_CHANGING:
            origin = headers.get("origin")
            if origin and self.settings.allowed_origins:
                base = origin.rstrip("/")
                if base not in [o.rstrip("/") for o in self.settings.allowed_origins]:
                    resp = problem(403, "forbidden_origin", "Origin no permitido")
                    await resp(scope, receive, send)
                    return

            # POST JSON obligatorio en la API con cuerpo.
            if path.startswith("/api/"):
                content_type = headers.get("content-type", "").split(";")[0].strip()
                content_length = headers.get("content-length")
                has_body = content_length not in (None, "0") or (
                    headers.get("transfer-encoding") == "chunked"
                )
                if has_body and content_type != "application/json":
                    resp = problem(
                        415,
                        "unsupported_media_type",
                        "Se exige Content-Type: application/json",
                    )
                    await resp(scope, receive, send)
                    return

        await self.app(scope, receive, send)

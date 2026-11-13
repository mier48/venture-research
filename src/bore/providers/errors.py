"""Errores de proveedor traducidos a los códigos estables de 9.1.

Solo los errores transitorios (429, 5xx, timeouts) son reintentables; los de
autenticación y de contrato nunca se reintentan (9.2).
"""

from __future__ import annotations

from bore.domain.enums import ErrorCode


class ProviderError(Exception):
    """Fallo de un proveedor con código estable para la ejecución."""

    error_code: ErrorCode = ErrorCode.PROVIDER_ERROR
    transient: bool = False
    retry_after: float | None = None

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        if retry_after is not None:
            self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    error_code = ErrorCode.PROVIDER_AUTH


class ProviderRateLimitError(ProviderError):
    error_code = ErrorCode.RATE_LIMITED
    transient = True


class ProviderServerError(ProviderError):
    """5xx del proveedor: transitorio, reintentable."""

    transient = True


class ProviderTimeoutError(ProviderError):
    transient = True


class ContractViolationError(ProviderError):
    error_code = ErrorCode.CONTRACT_VIOLATION

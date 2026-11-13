"""Soporte común de los adaptadores OpenAI (3.4): traducción de excepciones a
los códigos de 9.1, configuración de trazas y uso agregado."""

from __future__ import annotations

from typing import Any

from bore.providers.contracts import UsageRecord
from bore.providers.errors import (
    ContractViolationError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)


def configure_tracing(settings: Any) -> None:
    """Trazas desactivadas por defecto; BORE_TRACING_ENABLED las activa y
    BORE_TRACE_INCLUDE_SENSITIVE_DATA controla su contenido (3.4.6)."""
    from agents import set_tracing_disabled

    set_tracing_disabled(not settings.bore_tracing_enabled)


def run_config(settings: Any) -> Any:
    from agents import RunConfig

    return RunConfig(trace_include_sensitive_data=settings.bore_trace_include_sensitive_data)


def usage_record(usage: Any, web_search_calls: int, *, model: str | None) -> UsageRecord:
    """Traduce agents.usage.Usage (con request_usage_entries) a UsageRecord."""
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    return UsageRecord(
        provider="openai",
        model=model,
        requests=getattr(usage, "requests", 0) or 0,
        tokens_input=getattr(usage, "input_tokens", 0) or 0,
        tokens_cached_input=cached,
        tokens_output=getattr(usage, "output_tokens", 0) or 0,
        tokens_reasoning=reasoning,
        web_search_calls=web_search_calls,
    )


def translate_exception(exc: BaseException) -> ProviderError:
    """SDK/OpenAI -> códigos de 9.1 (transitorios solo 429/5xx/timeout)."""
    import openai
    from agents import AgentsException, MaxTurnsExceeded, ModelBehaviorError

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProviderAuthError("Error de autenticación del proveedor")
    if isinstance(exc, openai.RateLimitError):
        retry_after = None
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers and headers.get("retry-after"):
            try:
                retry_after = float(headers["retry-after"])
            except ValueError:
                retry_after = None
        return ProviderRateLimitError("Proveedor saturado (429)", retry_after=retry_after)
    if isinstance(exc, openai.APITimeoutError | TimeoutError):
        return ProviderTimeoutError("Timeout del proveedor")
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return ProviderServerError(f"Error {exc.status_code} del proveedor")
    if isinstance(exc, openai.APIConnectionError):
        return ProviderServerError("Error de conexión con el proveedor")
    if isinstance(exc, ModelBehaviorError):
        return ContractViolationError(f"Salida no conforme al contrato: {exc}")
    if isinstance(exc, MaxTurnsExceeded):
        return ProviderError("Se agotaron los turnos del agente (max_turns)")
    if isinstance(exc, openai.APIStatusError | openai.BadRequestError):
        return ProviderError(f"Error del proveedor: {getattr(exc, 'status_code', '?')}")
    if isinstance(exc, AgentsException):
        return ProviderError(f"Error del SDK de agentes: {exc}")
    return ProviderError(f"Error interno del proveedor: {exc}")

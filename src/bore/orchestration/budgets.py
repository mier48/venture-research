"""Gestor de presupuesto por ejecución (9.2).

Comprobación previa antes de cada llamada y registro posterior real en
`provider_calls`. La versión rigurosa con estimación de coste máximo previo por
modelo llega en Fase 2; la interfaz ya está diseñada para ello.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from bore.domain.enums import StopReason
from bore.providers.contracts import UsageRecord

_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.toml"


class BudgetExceeded(Exception):
    def __init__(self, stop_reason: StopReason, detail: str) -> None:
        super().__init__(detail)
        self.stop_reason = stop_reason
        self.detail = detail


@dataclass(frozen=True)
class Pricing:
    pricing_version: str
    web_search_usd_per_1000: Decimal
    fallback_input: Decimal
    fallback_cached_input: Decimal
    fallback_output: Decimal
    models: dict[str, dict[str, Decimal]]

    def call_cost(self, model: str | None, usage: UsageRecord) -> tuple[Decimal, bool]:
        """Devuelve (coste_usd, estimado)."""
        search = Decimal(usage.web_search_calls) * self.web_search_usd_per_1000 / Decimal(1000)
        if model and model in self.models:
            rates = self.models[model]
            estimated = False
        else:
            rates = {
                "input": self.fallback_input,
                "cached_input": self.fallback_cached_input,
                "output": self.fallback_output,
            }
            estimated = True
        tokens_cost = (
            Decimal(usage.tokens_input - usage.tokens_cached_input) * rates["input"]
            + Decimal(usage.tokens_cached_input) * rates["cached_input"]
            + Decimal(usage.tokens_output) * rates["output"]
        ) / Decimal(1_000_000)
        return search + tokens_cost, estimated


def load_pricing(path: Path | None = None) -> Pricing:
    data = tomllib.loads((path or _PRICING_PATH).read_text(encoding="utf-8"))
    ws = data["web_search"]["usd_per_1000_calls"]
    fb = data["fallback"]
    models = {
        name: {
            "input": Decimal(str(m["usd_per_million_input_tokens"])),
            "cached_input": Decimal(str(m["usd_per_million_cached_input_tokens"])),
            "output": Decimal(str(m["usd_per_million_output_tokens"])),
        }
        for name, m in data.get("models", {}).items()
    }
    return Pricing(
        pricing_version=str(data["pricing_version"]),
        web_search_usd_per_1000=Decimal(str(ws)),
        fallback_input=Decimal(str(fb["usd_per_million_input_tokens"])),
        fallback_cached_input=Decimal(str(fb["usd_per_million_cached_input_tokens"])),
        fallback_output=Decimal(str(fb["usd_per_million_output_tokens"])),
        models=models,
    )


@dataclass
class BudgetManager:
    """Contabiliza uso y corta cuando se excede un límite de la ejecución."""

    max_usd: Decimal
    max_web_search_calls: int
    max_model_calls: int
    elapsed_seconds: Callable[[], float]
    max_duration_seconds: int
    web_search_calls: int = 0
    model_calls: int = 0
    cost_usd: Decimal = Decimal(0)
    search_budget_remaining: int = field(default=0)

    def check_time(self) -> None:
        if self.elapsed_seconds() > self.max_duration_seconds:
            raise BudgetExceeded(StopReason.TIME_EXCEEDED, "Duración máxima excedida")

    def check_before_call(self, *, web_searches: int) -> None:
        """Comprobación previa: si no queda margen, la llamada no se lanza."""
        self.check_time()
        if self.model_calls + 1 > self.max_model_calls:
            raise BudgetExceeded(
                StopReason.MODEL_CALL_QUOTA_EXCEEDED, "Cuota de llamadas al modelo agotada"
            )
        if self.web_search_calls + web_searches > self.max_web_search_calls:
            raise BudgetExceeded(StopReason.SEARCH_QUOTA_EXCEEDED, "Cuota de búsquedas web agotada")
        if web_searches > self.search_budget_remaining:
            raise BudgetExceeded(
                StopReason.SEARCH_QUOTA_EXCEEDED,
                "Reparto de búsquedas de la etapa agotado",
            )

    def record(self, usage: UsageRecord, cost: Decimal, *, search_budget_spent: int = 0) -> None:
        """Registro posterior: si una llamada supera lo previsto, la siguiente no se lanza."""
        self.model_calls += usage.requests
        self.web_search_calls += usage.web_search_calls
        self.cost_usd += cost
        self.search_budget_remaining = max(0, self.search_budget_remaining - search_budget_spent)
        if self.cost_usd > self.max_usd:
            raise BudgetExceeded(StopReason.BUDGET_EXCEEDED, "Presupuesto USD excedido")

"""Gestor de presupuesto por ejecución (9.2).

Comprobación previa: antes de cada llamada se estima su coste máximo (tarifas
de `config/pricing.toml` + `max_output_tokens` + estimación de entrada) contra
el `max_usd` restante, más las cuotas de búsquedas, llamadas y duración. Si una
llamada supera lo previsto, la siguiente no se lanza. Reparto de búsquedas por
etapa de la tabla 9.2, configurable en `limits.search_split`, más una reserva
para UNA ampliación por oportunidad.
"""

from __future__ import annotations

import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from bore.domain.enums import StageId, StopReason
from bore.providers.contracts import UsageRecord

_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.toml"

# Reparto por defecto de la tabla 9.2 (la suma no supera max_web_search_calls).
DEFAULT_SEARCH_SPLIT: dict[str, int] = {
    "sector_mapping": 6,
    "demand_research": 16,
    "shallow_screening_per_eligible": 2,
    "shallow_screening_max": 30,
    "market_validation_per_shortlisted": 10,
    "market_validation_max": 80,
    "adversarial_review_per_reviewed": 4,
    "adversarial_review_max": 24,
    "reserve": 24,
}


def _split(config: dict[str, Any]) -> dict[str, int]:
    merged = dict(DEFAULT_SEARCH_SPLIT)
    merged.update(config.get("limits", {}).get("search_split") or {})
    return merged


def stage_search_budget(
    ctx: Any,
    stage: StageId,
    *,
    eligible: int = 0,
    shortlisted: int = 0,
    reviewed: int = 0,
) -> int:
    """Búsquedas asignadas a la etapa según el reparto 9.2."""
    split = _split(ctx.config)
    if stage is StageId.SECTOR_MAPPING:
        return split["sector_mapping"]
    if stage is StageId.DEMAND_RESEARCH:
        return split["demand_research"]
    if stage is StageId.SHALLOW_SCREENING:
        return min(
            split["shallow_screening_per_eligible"] * eligible,
            split["shallow_screening_max"],
        )
    if stage is StageId.MARKET_VALIDATION:
        return min(
            split["market_validation_per_shortlisted"] * shortlisted,
            split["market_validation_max"],
        )
    if stage is StageId.ADVERSARIAL_REVIEW:
        return min(
            split["adversarial_review_per_reviewed"] * reviewed,
            split["adversarial_review_max"],
        )
    return 0


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
    reserve_remaining: int = field(default=0)
    pricing: Pricing | None = field(default=None)
    _reserve_used: set[uuid.UUID] = field(default_factory=set)
    _cut: bool = field(default=False)

    def check_time(self) -> None:
        if self.elapsed_seconds() > self.max_duration_seconds:
            raise BudgetExceeded(StopReason.TIME_EXCEEDED, "Duración máxima excedida")

    def estimated_call_cost(
        self,
        model: str | None,
        *,
        est_input_tokens: int,
        max_output_tokens: int,
        web_searches: int,
    ) -> Decimal:
        """Coste máximo estimado de la próxima llamada (9.2)."""
        usage = UsageRecord(
            provider="estimate",
            model=model,
            requests=1,
            tokens_input=est_input_tokens,
            tokens_output=max_output_tokens,
            web_search_calls=web_searches,
        )
        pricing = self.pricing or load_pricing()
        cost, _ = pricing.call_cost(model, usage)
        return cost

    def check_before_call(
        self,
        *,
        model: str | None = None,
        est_input_tokens: int = 3000,
        max_output_tokens: int = 2000,
        web_searches: int = 0,
        from_reserve: bool = False,
    ) -> None:
        """Comprobación previa: si no queda margen, la llamada no se lanza."""
        try:
            self._check_before_call(
                model=model,
                est_input_tokens=est_input_tokens,
                max_output_tokens=max_output_tokens,
                web_searches=web_searches,
                from_reserve=from_reserve,
            )
        except BudgetExceeded:
            self._cut = True
            raise

    def _check_before_call(
        self,
        *,
        model: str | None,
        est_input_tokens: int,
        max_output_tokens: int,
        web_searches: int,
        from_reserve: bool,
    ) -> None:
        if self._cut:
            raise BudgetExceeded(StopReason.BUDGET_EXCEEDED, "Corte de presupuesto previo")
        self.check_time()
        if self.model_calls + 1 > self.max_model_calls:
            raise BudgetExceeded(
                StopReason.MODEL_CALL_QUOTA_EXCEEDED, "Cuota de llamadas al modelo agotada"
            )
        if self.web_search_calls + web_searches > self.max_web_search_calls:
            raise BudgetExceeded(StopReason.SEARCH_QUOTA_EXCEEDED, "Cuota de búsquedas web agotada")
        if not from_reserve and web_searches > self.search_budget_remaining:
            raise BudgetExceeded(
                StopReason.SEARCH_QUOTA_EXCEEDED,
                "Reparto de búsquedas de la etapa agotado",
            )
        if from_reserve and web_searches > self.reserve_remaining:
            raise BudgetExceeded(
                StopReason.SEARCH_QUOTA_EXCEEDED,
                "Reserva de ampliaciones agotada",
            )
        estimated = self.estimated_call_cost(
            model,
            est_input_tokens=est_input_tokens,
            max_output_tokens=max_output_tokens,
            web_searches=web_searches,
        )
        if self.cost_usd + estimated > self.max_usd:
            raise BudgetExceeded(
                StopReason.BUDGET_EXCEEDED,
                f"Presupuesto USD insuficiente para la siguiente llamada "
                f"(estimada {estimated} USD)",
            )

    def spend_stage_searches(self, web_search_calls: int) -> None:
        self.search_budget_remaining = max(0, self.search_budget_remaining - web_search_calls)

    def reserve_available(self, opportunity_id: uuid.UUID) -> bool:
        """La reserva solo cubre UNA ampliación por oportunidad (9.2)."""
        return self.reserve_remaining > 0 and opportunity_id not in self._reserve_used

    def spend_reserve(self, opportunity_id: uuid.UUID | None, web_search_calls: int) -> None:
        if opportunity_id is not None:
            self._reserve_used.add(opportunity_id)
        self.reserve_remaining = max(0, self.reserve_remaining - web_search_calls)

    def record(self, usage: UsageRecord, cost: Decimal) -> None:
        """Registro posterior: si una llamada supera lo previsto, la siguiente no se lanza."""
        self.model_calls += usage.requests
        self.web_search_calls += usage.web_search_calls
        self.cost_usd += cost
        if self.cost_usd > self.max_usd:
            self._cut = True
            raise BudgetExceeded(StopReason.BUDGET_EXCEEDED, "Presupuesto USD excedido")

"""Construcción de agentes OpenAI Agents SDK por papel (3.4, 4.1).

- Agentes de investigación: única herramienta `WebSearchTool` con
  `user_location` por país y `search_context_size` configurable
  (`BORE_SEARCH_CONTEXT_SIZE`, defecto "medium"); salida texto;
  `ModelSettings(response_include=["web_search_call.action.sources"])` y
  `max_tool_calls` como límite blando vía `extra_args`.
- Agentes de análisis: sin herramientas, `output_type` Pydantic.
- Sin handoffs ni sesiones; `max_turns` configurable por llamada.
"""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings, WebSearchTool
from openai.types.responses.web_search_tool_param import UserLocation
from pydantic import BaseModel

from bore.agents.prompt_registry import render_prompt

# Papel -> variable de modelo. CriticalReviewer usa REVIEW (investigación y
# estructuración); EvidenceJudge y CriterionAssessor usan ANALYSIS (3.4).
ROLE_MODEL_ENV: dict[str, str] = {
    "sector_mapper": "research",
    "demand_researcher": "research",
    "demand_research": "research",
    "screener": "research",
    "market_validation": "research",
    "competition_researcher": "research",
    "gtm_analyst": "research",
    "tech_feasibility": "research",
    "critical_reviewer": "review",
    "idea_generator": "analysis",
    "dedup_screener": "analysis",
    "eligibility_assessor": "analysis",
    "evidence_judge": "analysis",
    "criterion_assessor": "analysis",
}

# Roles que usan WebSearchTool (los demás son análisis puros).
RESEARCH_ROLES: frozenset[str] = frozenset(
    {
        "sector_mapper",
        "demand_researcher",
        "screener",
        "market_validation",
        "competition_researcher",
        "gtm_analyst",
        "tech_feasibility",
        "critical_reviewer",
    }
)


def model_for_role(role: str, models: dict[str, str]) -> str:
    """models: {"research": ..., "analysis": ..., "review": ...}."""
    return models[ROLE_MODEL_ENV.get(role, "analysis")]


def build_research_agent(
    role: str,
    *,
    model: str,
    country: str,
    search_context_size: str = "medium",
    max_web_search_calls: int | None = None,
    context: dict[str, str] | None = None,
) -> Agent[Any]:
    """Agente de investigación con única herramienta WebSearchTool."""
    extra_args: dict[str, Any] = {}
    if max_web_search_calls is not None:
        # Límite blando (3.4.5): el recuento real se hace en el adaptador.
        extra_args["max_tool_calls"] = max_web_search_calls
    return Agent(
        name=f"bore_{role}",
        instructions=render_prompt(role, context or {}),
        model=model,
        tools=[
            WebSearchTool(
                user_location=UserLocation(type="approximate", country=country),
                search_context_size=search_context_size,  # type: ignore[arg-type]
            )
        ],
        model_settings=ModelSettings(
            response_include=["web_search_call.action.sources"],
            extra_args=extra_args or None,
        ),
    )


def build_analysis_agent(
    role: str,
    *,
    model: str,
    output_type: type[BaseModel],
    context: dict[str, str] | None = None,
) -> Agent[Any]:
    """Agente de estructuración: sin herramientas, salida tipada."""
    return Agent(
        name=f"bore_{role}",
        instructions=render_prompt(role, context or {}),
        model=model,
        tools=[],
        output_type=output_type,
    )

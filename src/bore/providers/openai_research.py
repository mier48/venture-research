"""Adaptador de investigación sobre OpenAI Agents SDK (3.4, 4.2).

Une las anotaciones `url_citation` (aportan título) con la lista de fuentes
`web_search_call.action.sources`, deduplica por `canonical_url` y asigna las
referencias S1…Sn aquí, nunca en el modelo. Ninguna URL procede de la prosa.
"""

from __future__ import annotations

import time
from typing import Any

from bore.agents import factory
from bore.domain.evidence import canonical_url
from bore.providers.contracts import ProviderSource, ResearchResult, ResearchTask, UsageRecord
from bore.providers.openai_common import (
    configure_tracing,
    run_config,
    translate_exception,
    usage_record,
)


class OpenAIResearchProvider:
    """ResearchProvider real: agente por papel + WebSearchTool hospedada."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._models = {
            "research": settings.bore_model_research,
            "analysis": settings.bore_model_analysis,
            "review": settings.bore_model_review,
        }
        self.last_trace_id: str | None = None
        configure_tracing(settings)

    async def research(self, task: ResearchTask) -> ResearchResult:
        from agents import Runner, trace

        agent = factory.build_research_agent(
            task.role,
            model=factory.model_for_role(task.role, self._models),
            country=task.country,
            search_context_size=self._settings.bore_search_context_size,
            max_web_search_calls=task.max_web_search_calls,
            context={
                "sector": task.input_text[:200],
                "country": task.country,
                "language": task.language,
            },
        )
        started = time.monotonic()
        try:
            with trace("bore.research", disabled=not self._settings.bore_tracing_enabled) as tr:
                result = await Runner.run(
                    agent,
                    task.input_text,
                    max_turns=self._settings.bore_agent_max_turns,
                    run_config=run_config(self._settings),
                )
            self.last_trace_id = getattr(tr, "trace_id", None)
        except Exception as exc:
            raise translate_exception(exc) from exc
        sources, queries, web_calls = _extract_sources_and_queries(result)
        usage = usage_record(
            result.context_wrapper.usage,
            web_calls,
            model=factory.model_for_role(task.role, self._models),
        )
        usage = UsageRecord(
            provider=usage.provider,
            model=usage.model,
            requests=usage.requests,
            tokens_input=usage.tokens_input,
            tokens_cached_input=usage.tokens_cached_input,
            tokens_output=usage.tokens_output,
            tokens_reasoning=usage.tokens_reasoning,
            web_search_calls=usage.web_search_calls,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        text = (
            result.final_output
            if isinstance(result.final_output, str)
            else str(result.final_output)
        )
        return ResearchResult(text=text, sources=sources, queries=queries, usage=usage)


def _extract_sources_and_queries(
    result: Any,
) -> tuple[tuple[ProviderSource, ...], tuple[str, ...], int]:
    """Fuentes de anotaciones + source list, dedup por canonical_url; refs S1…n."""
    by_canonical: dict[str, ProviderSource] = {}
    order: list[str] = []
    queries: list[str] = []
    web_calls = 0

    def add(url: str, title: str | None, origin: str) -> None:
        canon = canonical_url(url)
        if canon in by_canonical:
            # Las anotaciones aportan título: enriquecer la entrada existente.
            existing = by_canonical[canon]
            if title and not existing.title:
                by_canonical[canon] = ProviderSource(
                    ref=existing.ref, url=existing.url, title=title, origin=existing.origin
                )
            return
        order.append(canon)
        by_canonical[canon] = ProviderSource(
            ref=f"S{len(order)}",
            url=url,
            title=title,
            origin=origin,  # type: ignore[arg-type]
        )

    for response in result.raw_responses:
        for item in response.output:
            itype = getattr(item, "type", None)
            if itype == "web_search_call":
                web_calls += 1
                action = getattr(item, "action", None)
                for q in getattr(action, "queries", None) or []:
                    queries.append(q)
                q1 = getattr(action, "query", None)
                if q1:
                    queries.append(q1)
                for src in getattr(action, "sources", None) or []:
                    url = getattr(src, "url", None)
                    if url:
                        add(url, None, "source_list")
                opened = getattr(action, "url", None)
                if opened:
                    add(opened, None, "source_list")
            elif itype == "message":
                for part in getattr(item, "content", []) or []:
                    for ann in getattr(part, "annotations", []) or []:
                        if getattr(ann, "type", None) == "url_citation" and getattr(
                            ann, "url", None
                        ):
                            add(ann.url, getattr(ann, "title", None), "annotation")
    return (
        tuple(by_canonical[c] for c in order),
        tuple(dict.fromkeys(queries)),
        web_calls,
    )


def check_models_exist(settings: Any) -> list[str]:
    """Comprobación de arranque en modo real: `client.models.retrieve` por cada
    modelo BORE_MODEL_* (gratuito). Devuelve la lista de problemas encontrados.

    No puede garantizarse la compatibilidad con búsqueda web sin una llamada
    real: si el proveedor falla en la primera búsqueda, el error se traduce a
    `provider_error` con mensaje explícito (documentado en DECISIONS)."""
    import openai

    problems: list[str] = []
    models = {
        "BORE_MODEL_RESEARCH": settings.bore_model_research,
        "BORE_MODEL_ANALYSIS": settings.bore_model_analysis,
        "BORE_MODEL_REVIEW": settings.bore_model_review,
    }
    for env, model in models.items():
        if not model:
            problems.append(f"{env} no definido")
    if problems or not settings.openai_api_key:
        if not settings.openai_api_key:
            problems.append("OPENAI_API_KEY no definida")
        return problems
    client = openai.OpenAI(api_key=settings.openai_api_key)
    for env, model in models.items():
        if not model:
            continue
        try:
            client.models.retrieve(model)
        except openai.NotFoundError:
            problems.append(f"{env}: modelo '{model}' no existe")
        except openai.AuthenticationError:
            problems.append("OPENAI_API_KEY rechazada por el proveedor")
            break
        except openai.APIError as exc:
            problems.append(f"{env}: no se pudo comprobar el modelo ({exc})")
    return problems


_model_check_cache: dict[tuple[str, str, str], list[str]] = {}


async def check_models_exist_cached(settings: Any) -> list[str]:
    """Comprobación de arranque cacheada por tripleta de modelos (POST 8.1):
    primero la configuración, después `models.retrieve` (gratuito)."""
    import asyncio

    problems = missing_real_mode_config(settings)
    if problems:
        return problems
    key = (
        settings.bore_model_research,
        settings.bore_model_analysis,
        settings.bore_model_review,
    )
    if key not in _model_check_cache:
        _model_check_cache[key] = await asyncio.to_thread(check_models_exist, settings)
    return _model_check_cache[key]


def missing_real_mode_config(settings: Any) -> list[str]:
    """Validación sin red: modelos BORE_MODEL_* definidos y clave presente."""
    problems: list[str] = []
    if not settings.openai_api_key:
        problems.append("OPENAI_API_KEY no definida")
    for env, model in (
        ("BORE_MODEL_RESEARCH", settings.bore_model_research),
        ("BORE_MODEL_ANALYSIS", settings.bore_model_analysis),
        ("BORE_MODEL_REVIEW", settings.bore_model_review),
    ):
        if not model:
            problems.append(f"{env} no definido")
    return problems

"""Adaptadores OpenAI sin red: agents.testing.ScriptedModel cubre extracción
de fuentes (anotaciones + source list), dedup, recuento de búsquedas, usage,
traducción de excepciones y reintento de validación."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from agents import MaxTurnsExceeded
from agents.testing import ModelStep, ScriptedModel
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionWebSearch,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_function_web_search import (
    ActionSearch,
    ActionSearchSource,
)
from openai.types.responses.response_output_text import AnnotationURLCitation

from bore.agents.schemas import SectorMapOutput
from bore.providers.contracts import AnalysisTask, ResearchTask
from bore.providers.errors import (
    ContractViolationError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
from bore.providers.openai_analysis import OpenAIAnalysisProvider
from bore.providers.openai_common import translate_exception
from bore.providers.openai_research import OpenAIResearchProvider, _extract_sources_and_queries


def settings_with(model):
    return SimpleNamespace(
        openai_api_key="sk-test",
        bore_model_research=model,
        bore_model_analysis=model,
        bore_model_review=model,
        bore_search_context_size="medium",
        bore_agent_max_turns=5,
        bore_tracing_enabled=False,
        bore_trace_include_sensitive_data=False,
    )


def message_with_citation(text="Texto con cita", url="https://a.example.com/x", title="Título A"):
    return ResponseOutputMessage(
        id="m1",
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                type="output_text",
                text=text,
                annotations=[
                    AnnotationURLCitation(
                        type="url_citation",
                        url=url,
                        title=title,
                        start_index=0,
                        end_index=4,
                    )
                ],
            )
        ],
    )


def web_search_call(url="https://a.example.com/x", query="consulta"):
    return ResponseFunctionWebSearch(
        id="ws1",
        type="web_search_call",
        status="completed",
        action=ActionSearch(
            type="search",
            query=query,
            sources=[ActionSearchSource(type="url", url=url)],
        ),
    )


def research_task():
    return ResearchTask(
        role="sector_mapper",
        stage="sector_mapping",
        instructions="i",
        input_text="Sector: ejemplo. Mercado: ES.",
        country="ES",
        language="es-ES",
    )


async def test_sources_merged_deduped_and_counted():
    model = ScriptedModel(
        steps=[
            ModelStep(
                output=[
                    web_search_call("https://a.example.com/x", "q1"),
                    web_search_call("https://b.example.org/y?utm_source=x", "q2"),
                    message_with_citation(url="https://a.example.com/x", title="Título A"),
                    message_with_citation(url="https://c.example.net/z", title="Título C"),
                ],
                usage=Usage(requests=1, input_tokens=100, output_tokens=50),
            )
        ]
    )
    provider = OpenAIResearchProvider(settings_with(model))
    result = await provider.research(research_task())
    urls = [s.url for s in result.sources]
    assert urls == [
        "https://a.example.com/x",
        "https://b.example.org/y?utm_source=x",
        "https://c.example.net/z",
    ]
    assert [s.ref for s in result.sources] == ["S1", "S2", "S3"]
    # La anotación aporta título; la source list no lo tiene.
    assert result.sources[0].title == "Título A"
    assert result.sources[0].origin == "source_list"
    assert result.sources[2].origin == "annotation"
    assert result.usage.web_search_calls == 2
    assert result.usage.requests == 1
    assert result.usage.tokens_input == 100
    assert result.queries == ("q1", "q2")


async def test_analysis_retry_on_invalid_output():
    valid = SectorMapOutput(
        subsectors=["s"],
        actors=[],
        processes=[],
        problems=[],
        search_terms=[],
        claims=[],
    )
    model = ScriptedModel(
        steps=[
            ModelStep(output=[message_with_citation(text="no es json", url="https://x.es/a")]),
            ModelStep(
                output=[
                    message_with_citation(text=json.dumps(valid.model_dump()), url="https://x.es/b")
                ]
            ),
        ]
    )
    provider = OpenAIAnalysisProvider(settings_with(model))
    task = AnalysisTask(
        role="sector_mapper",
        stage="sector_mapping",
        instructions="i",
        input_text="datos",
        output_type=SectorMapOutput,
    )
    result = await provider.analyze(task)
    assert result.output.subsectors == ["s"]
    assert len(model.calls) == 2


async def test_analysis_double_failure_is_contract_violation():
    model = ScriptedModel(
        steps=[
            ModelStep(output=[message_with_citation(text="malo")]),
            ModelStep(output=[message_with_citation(text="peor")]),
        ]
    )
    provider = OpenAIAnalysisProvider(settings_with(model))
    task = AnalysisTask(
        role="sector_mapper",
        stage="sector_mapping",
        instructions="i",
        input_text="datos",
        output_type=SectorMapOutput,
    )
    with pytest.raises(ContractViolationError):
        await provider.analyze(task)


def rate_limit_error(retry_after=None):
    headers = httpx.Headers({"retry-after": retry_after} if retry_after else {})
    return openai.RateLimitError(
        "rate",
        response=httpx.Response(
            429, headers=headers, request=httpx.Request("POST", "https://api.x/v1")
        ),
        body=None,
    )


def test_exception_translation():
    assert isinstance(
        translate_exception(
            openai.AuthenticationError(
                "auth",
                response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                body=None,
            )
        ),
        ProviderAuthError,
    )
    err = translate_exception(rate_limit_error("3"))
    assert isinstance(err, ProviderRateLimitError) and err.retry_after == 3.0
    err = translate_exception(
        openai.InternalServerError(
            "srv",
            response=httpx.Response(500, request=httpx.Request("POST", "https://x")),
            body=None,
        )
    )
    assert err.transient
    assert isinstance(translate_exception(MaxTurnsExceeded("x")), ProviderError)
    assert not translate_exception(MaxTurnsExceeded("x")).transient


async def test_provider_translates_sdk_error():
    model = ScriptedModel(steps=[ModelStep.raise_error(rate_limit_error("1"))])
    provider = OpenAIResearchProvider(settings_with(model))
    with pytest.raises(ProviderRateLimitError):
        await provider.research(research_task())


def test_extract_counts_web_calls_only_from_items():
    result = SimpleNamespace(
        raw_responses=[SimpleNamespace(output=[web_search_call(), message_with_citation()])]
    )
    _, _, web_calls = _extract_sources_and_queries(result)
    assert web_calls == 1


# ---------------------------------------------------------------------------
# Contrato de salidas estructuradas (D42)
# ---------------------------------------------------------------------------

from agents import AgentOutputSchema  # noqa: E402

from bore.agents.schemas import ROLE_OUTPUT_TYPES  # noqa: E402

_EXAMPLE_JSON: dict[str, str] = {
    "sector_mapper": json.dumps(
        {
            "subsectors": ["gestión de alquileres"],
            "actors": ["propietarios"],
            "processes": ["contratación"],
            "problems": ["fricción documental"],
            "search_terms": ["alquiler software"],
            "claims": [
                {
                    "text": "La gestión manual genera fricción.",
                    "kind": "fact",
                    "criterion": "demand",
                }
            ],
        }
    ),
    "demand_researcher": json.dumps(
        {
            "demand_signals": ["búsquedas crecientes"],
            "information_gaps": ["precios opacos"],
            "claims": [{"text": "Hay quejas recurrentes.", "kind": "fact", "criterion": None}],
        }
    ),
    "idea_generator": json.dumps(
        {
            "ideas": [
                {
                    "name": "Idea ejemplo",
                    "sector_subcategory": "alquileres",
                    "customer_segment": "propietarios",
                    "buyer": "administrador",
                    "problem": "problema",
                    "solution": "solución",
                    "value_proposition": "propuesta",
                    "business_model": "suscripción",
                }
            ]
        }
    ),
    "dedup_screener": json.dumps({"groups": [[0, 1]]}),
    "eligibility_assessor": json.dumps(
        {
            "candidate_index": 0,
            "assessments": [{"rule": "not_software", "answer": "yes", "reason": "SaaS"}],
        }
    ),
    "screener": json.dumps(
        {"candidate_index": 0, "demand": 6, "distribution": 7, "competition": 5}
    ),
    "market_validation": json.dumps(
        {
            "competitors": [
                {
                    "name": "Competidor",
                    "category": "direct",
                    "price_value": 49.0,
                    "price_currency": "EUR",
                    "price_unit": "mes",
                    "price_country": "ES",
                }
            ],
            "claims": [
                {
                    "text": "Cobra 49 EUR/mes.",
                    "kind": "fact",
                    "criterion": "monetization",
                }
            ],
        }
    ),
    "evidence_judge": json.dumps({"verdict": "supports", "rationale": "encaja"}),
    "criterion_assessor": json.dumps(
        {
            "scores": [
                {
                    "criterion": "demand",
                    "score": 7,
                    "claims": [{"ref": "C1", "role": "evidence"}],
                }
            ]
        }
    ),
    "critical_reviewer": json.dumps(
        {
            "objections": ["mercado pequeño"],
            "recommended_changes": ["concretar comprador"],
            "final_verdict": "keep_researching",
        }
    ),
}


def test_role_output_types_cover_all_stage_roles():
    """Todo rol usado en una llamada do_analysis de stages.py tiene esquema."""
    import re
    from pathlib import Path

    from bore.orchestration import stages

    source = Path(stages.__file__).read_text(encoding="utf-8")
    roles = set(re.findall(r'do_analysis\(\s*"(\w+)"', source))
    assert roles
    assert roles <= set(ROLE_OUTPUT_TYPES), roles - set(ROLE_OUTPUT_TYPES)


@pytest.mark.parametrize("role", sorted(ROLE_OUTPUT_TYPES))
def test_output_schema_is_strict_compatible(role):
    """Cada esquema genera JSON schema estricto válido y valida un ejemplo."""
    schema = AgentOutputSchema(ROLE_OUTPUT_TYPES[role])
    js = schema.json_schema()
    assert isinstance(js, dict)
    assert schema.is_strict_json_schema
    schema.validate_json(_EXAMPLE_JSON[role])


async def test_analysis_uses_task_instructions_and_returns_typed_output():
    valid = SectorMapOutput(
        subsectors=["s"],
        actors=[],
        processes=[],
        problems=[],
        search_terms=[],
        claims=[],
    )
    model = ScriptedModel(
        steps=[
            ModelStep(
                output=[
                    message_with_citation(text=json.dumps(valid.model_dump()), url="https://x.es/b")
                ]
            )
        ]
    )
    provider = OpenAIAnalysisProvider(settings_with(model))
    task = AnalysisTask(
        role="sector_mapper",
        stage="sector_mapping",
        instructions="INSTRUCCIONES_UNICAS_XYZ",
        input_text="datos",
        output_type=SectorMapOutput,
    )
    result = await provider.analyze(task)
    assert isinstance(result.output, SectorMapOutput)
    assert result.output.subsectors == ["s"]
    assert model.first_call is not None
    assert model.first_call.system_instructions == "INSTRUCCIONES_UNICAS_XYZ"


async def test_analysis_requires_output_type():
    provider = OpenAIAnalysisProvider(settings_with(ScriptedModel()))
    task = AnalysisTask(
        role="sector_mapper",
        stage="sector_mapping",
        instructions="i",
        input_text="datos",
        output_type=None,
    )
    with pytest.raises(ContractViolationError):
        await provider.analyze(task)


async def test_research_adapter_passes_task_instructions_verbatim():
    """El adaptador real recibe task.instructions (fase research), que difiere
    de las instrucciones de la fase de estructuración del mismo papel."""
    from bore.agents.prompt_registry import render_prompt

    ctx = {"sector": "inmobiliario", "country": "DE", "language": "es-ES"}
    research_instr = render_prompt("sector_mapper", ctx, phase="research")
    structure_instr = render_prompt("sector_mapper", ctx, phase="structure")
    assert research_instr != structure_instr

    model = ScriptedModel(steps=[ModelStep(output=[message_with_citation()])])
    provider = OpenAIResearchProvider(settings_with(model))
    result = await provider.research(
        ResearchTask(
            role="sector_mapper",
            stage="sector_mapping",
            instructions=research_instr,
            input_text="Sector: inmobiliario. Mercado: DE.",
            country="DE",
            language="es-ES",
        )
    )
    assert result.usage.requests >= 0
    assert model.first_call is not None
    assert model.first_call.system_instructions == research_instr

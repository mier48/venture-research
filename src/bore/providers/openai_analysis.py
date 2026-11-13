"""Adaptador de estructuración sobre OpenAI Agents SDK (3.4, 9.2).

Salida tipada vía `output_type` Pydantic. Si la salida no valida, UN reintento
con el error de validación como contexto; si vuelve a fallar ->
`contract_violation` (ProviderError con error_code correspondiente).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from bore.agents import factory
from bore.providers.contracts import AnalysisResult, AnalysisTask
from bore.providers.errors import ContractViolationError
from bore.providers.openai_common import (
    configure_tracing,
    run_config,
    translate_exception,
    usage_record,
)

T = TypeVar("T", bound=BaseModel)


class OpenAIAnalysisProvider:
    """AnalysisProvider real: sin herramientas, salida tipada, un reintento."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._models = {
            "research": settings.bore_model_research,
            "analysis": settings.bore_model_analysis,
            "review": settings.bore_model_review,
        }
        configure_tracing(settings)

    async def analyze(self, task: AnalysisTask[T]) -> AnalysisResult[T]:
        from agents import Runner

        model = factory.model_for_role(task.role, self._models)
        agent = factory.build_analysis_agent(
            task.role,
            model=model,
            output_type=task.output_type or BaseModel,
            context={
                "sector": task.input_text[:200],
                "country": "ES",
                "language": "es-ES",
            },
        )
        input_text = task.input_text
        last_error: str | None = None
        total_usage: Any = None
        for attempt in range(2):
            try:
                result = await Runner.run(
                    agent,
                    input_text,
                    max_turns=self._settings.bore_agent_max_turns,
                    run_config=run_config(self._settings),
                )
            except Exception as exc:
                err = translate_exception(exc)
                if isinstance(err, ContractViolationError) and attempt == 0:
                    last_error = str(exc)
                    input_text = (
                        f"{task.input_text}\n\nLa salida anterior no validó el esquema: "
                        f"{last_error}\nCorrige y responde SOLO con el esquema válido."
                    )
                    continue
                raise err from exc
            usage = result.context_wrapper.usage
            if total_usage is None:
                total_usage = usage
            else:
                total_usage.add(usage)
            output = result.final_output
            if task.output_type is not None and not isinstance(output, task.output_type):
                try:
                    output = task.output_type.model_validate(output)
                except (ValidationError, TypeError) as exc:
                    if attempt == 0:
                        input_text = (
                            f"{task.input_text}\n\nLa salida anterior no validó el "
                            f"esquema: {exc}\nCorrige y responde SOLO con el esquema válido."
                        )
                        continue
                    raise ContractViolationError(
                        f"Salida de {task.role} no conforme tras reintento: {exc}"
                    ) from exc
            return AnalysisResult(
                output=output,
                usage=usage_record(
                    total_usage,
                    0,
                    model=model,
                ),
            )
        raise ContractViolationError(  # pragma: no cover
            f"Salida de {task.role} no conforme tras reintento: {last_error}"
        )

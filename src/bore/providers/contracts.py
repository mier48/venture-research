"""Contratos de proveedores (3.3). El dominio y el orquestador dependen solo de
estos tipos, nunca del SDK del proveedor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel

from bore.domain.enums import AccessStatus

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ProviderSource:
    ref: str  # "S1", "S2"… lo asigna el adaptador, no el modelo
    url: str
    title: str | None
    origin: Literal["annotation", "source_list", "synthetic"]


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    model: str | None
    requests: int = 0
    tokens_input: int = 0
    tokens_cached_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    web_search_calls: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class ResearchResult:
    text: str  # prosa del modelo: dato no confiable
    sources: tuple[ProviderSource, ...]  # única procedencia admitida de URL
    queries: tuple[str, ...]  # consultas ejecutadas, si el proveedor las expone
    usage: UsageRecord


@dataclass(frozen=True)
class AnalysisResult(Generic[T]):  # noqa: UP046
    output: T
    usage: UsageRecord


@dataclass(frozen=True)
class ResearchTask:
    """Tarea de investigación con web. `purpose` identifica el papel/etapa."""

    role: str
    stage: str
    instructions: str
    input_text: str
    country: str
    language: str
    opportunity_id: str | None = None
    max_web_search_calls: int | None = None


class AnalysisTask(BaseModel, Generic[T]):  # noqa: UP046
    """Tarea de estructuración sin herramientas, con salida tipada."""

    role: str
    stage: str
    instructions: str
    input_text: str
    output_type: type[T] | None = None
    opportunity_id: str | None = None

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True)
class SourceCheck:
    url: str
    final_url: str | None
    access_status: AccessStatus
    http_status: int | None
    content_hash: str | None
    excerpt: str | None  # máximo 500 caracteres
    page_text: str | None = None  # solo en memoria; nunca se persiste


class ResearchProvider(Protocol):
    async def research(self, task: ResearchTask) -> ResearchResult: ...


class AnalysisProvider(Protocol):
    async def analyze(self, task: AnalysisTask[T]) -> AnalysisResult[T]: ...


class SourceVerifier(Protocol):
    async def verify(self, url: str) -> SourceCheck: ...

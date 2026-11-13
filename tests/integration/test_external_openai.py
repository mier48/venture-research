"""Humo externo contra la API real de OpenAI (13.5). Desactivado por defecto
(-m "not integration_external"). Requiere OPENAI_API_KEY, BORE_ALLOW_PAID_SMOKE=1
y BORE_MODEL_RESEARCH configurado; consume llamadas de pago."""

import os

import pytest

from bore.config import get_settings
from bore.providers.contracts import ResearchTask
from bore.providers.openai_research import OpenAIResearchProvider

pytestmark = pytest.mark.integration_external


@pytest.fixture
def settings():
    s = get_settings()
    smoke = os.environ.get("BORE_ALLOW_PAID_SMOKE") == "1"
    if not (s.openai_api_key and s.bore_model_research and smoke):
        pytest.skip("integration_external: falta OPENAI_API_KEY/BORE_ALLOW_PAID_SMOKE/modelo")
    return s


async def test_web_search_returns_sources(settings):
    provider = OpenAIResearchProvider(settings)
    result = await provider.research(
        ResearchTask(
            role="sector_mapper",
            stage="sector_mapping",
            instructions="Responde en dos líneas.",
            input_text="¿Qué es una prop tech? Una consulta trivial.",
            country="ES",
            language="es-ES",
        )
    )
    assert result.text
    assert result.usage.requests >= 1
    # Las fuentes solo provienen de anotaciones/source list del proveedor.
    assert all(s.origin in ("annotation", "source_list") for s in result.sources)

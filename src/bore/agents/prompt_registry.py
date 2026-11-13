"""Carga de prompts versionados (un fichero por papel)."""

from __future__ import annotations

from importlib import resources

PROMPT_FILES: dict[str, str] = {
    "sector_mapper": "sector_mapper.md",
    "demand_researcher": "demand_researcher.md",
    "idea_generator": "idea_generator.md",
    "eligibility_assessor": "eligibility_assessor.md",
    "competition_researcher": "competition_researcher.md",
    "gtm_analyst": "gtm_analyst.md",
    "tech_feasibility": "tech_feasibility.md",
    "evidence_judge": "evidence_judge.md",
    "criterion_assessor": "criterion_assessor.md",
    "critical_reviewer": "critical_reviewer.md",
    "dedup_screener": "dedup_screener.md",
}

# Alias de papeles operativos a su fichero de prompt (modo sondeo y fusión de
# analistas de market_validation en un solo papel de proveedor).
PROMPT_ALIASES: dict[str, str] = {
    "demand_research": "demand_researcher",
    "screener": "demand_researcher",
    "market_validation": "competition_researcher",
}


def render_prompt(role: str, context: dict[str, str] | None = None) -> str:
    """Devuelve el prompt del papel con el contexto sustituido.

    Nunca incluye el umbral de publicación, los pesos ni `requested_ideas`
    (evaluadores ciegos, principio 7 de la spec).
    """
    filename = PROMPT_FILES[PROMPT_ALIASES.get(role, role)]
    text = resources.files("bore.agents.prompts").joinpath(filename).read_text(encoding="utf-8")
    if context:
        for key, value in context.items():
            text = text.replace("{{" + key + "}}", value)
    return text


def prompt_version(role: str) -> str:
    text = render_prompt(role)
    for line in text.splitlines():
        if line.startswith("# prompt_version:"):
            return line.split(":", 1)[1].strip()
    return "unknown"

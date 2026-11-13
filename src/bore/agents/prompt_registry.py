"""Carga de prompts versionados: un fichero por papel (o por papel y fase:
`<rol>.research.md` / `<rol>.structure.md`) más el bloque común `_common.md`,
que se concatena siempre al final.

Placeholders soportados: {{sector}}, {{country}}, {{language}}. Si tras la
sustitución queda algún `{{` sin resolver, es un error de programación.
"""

from __future__ import annotations

import hashlib
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Literal

PROMPT_PACKAGE = "bore.agents.prompts"
COMMON_FILE = "_common.md"
_PHASES = ("research", "structure")
_PHASE_TYPE = Literal["research", "structure"]

# Papeles cuyas instrucciones usa el pipeline. `True` = tiene ficheros por
# fase; `False` = fichero único que sirve a todas las fases.
PROMPT_ROLES: frozenset[str] = frozenset(
    {
        "sector_mapper",
        "demand_researcher",
        "screener",
        "market_validation",
        "critical_reviewer",
        "idea_generator",
        "dedup_screener",
        "eligibility_assessor",
        "evidence_judge",
        "criterion_assessor",
    }
)


def _files() -> Traversable:
    return resources.files(PROMPT_PACKAGE)


def prompt_file_for(role: str, phase: _PHASE_TYPE | None = None) -> str:
    """Fichero del papel: `<rol>.<fase>.md` si existe; si no, `<rol>.md`."""
    candidates = [f"{role}.{phase}.md"] if phase else []
    candidates.append(f"{role}.md")
    for name in candidates:
        if _files().joinpath(name).is_file():
            return name
    raise FileNotFoundError(f"No hay fichero de prompt para {role!r} (fase {phase!r})")


def render_prompt(
    role: str,
    context: dict[str, str] | None = None,
    phase: _PHASE_TYPE | None = None,
) -> str:
    """Devuelve el prompt del papel/fase + bloque común, con contexto.

    Nunca incluye el umbral de publicación, los pesos ni `requested_ideas`
    (evaluadores ciegos, principio 7 de la spec).
    """
    text = _files().joinpath(prompt_file_for(role, phase)).read_text(encoding="utf-8")
    text += "\n\n" + _files().joinpath(COMMON_FILE).read_text(encoding="utf-8")
    if context:
        for key, value in context.items():
            text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise ValueError(f"Prompt {role!r} (fase {phase!r}): placeholder sin sustituir en el texto")
    return text


def _header_value(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"# {key}:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def prompt_version(role: str, phase: _PHASE_TYPE | None = None) -> str:
    """Versión declarada en la cabecera del fichero del papel/fase."""
    text = _files().joinpath(prompt_file_for(role, phase)).read_text(encoding="utf-8")
    return _header_value(text, "prompt_version")


def prompt_versions() -> dict[str, str]:
    """Mapa `fichero -> versión` de todos los prompts (auditoría por papel)."""
    versions: dict[str, str] = {}
    for entry in sorted(_files().iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".md") and entry.name != COMMON_FILE:
            text = entry.read_text(encoding="utf-8")
            versions[entry.name.removesuffix(".md")] = _header_value(text, "prompt_version")
    return versions


def composite_prompt_version() -> str:
    """Versión compuesta de TODOS los prompts + el bloque común.

    `bore-prompts:` + sha256 corto de los pares "id=version" ordenados y del
    contenido de `_common.md`.
    """
    versions = prompt_versions()
    common = _files().joinpath(COMMON_FILE).read_text(encoding="utf-8")
    material = "\n".join(f"{name}={v}" for name, v in sorted(versions.items())) + "\n" + common
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"bore-prompts:{digest}"

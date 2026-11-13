import re
from pathlib import Path
from typing import get_args

import pytest

from bore.agents.prompt_registry import (
    PROMPT_ROLES,
    composite_prompt_version,
    prompt_version,
    prompt_versions,
    render_prompt,
)
from bore.agents.schemas import (
    ROLE_OUTPUT_TYPES,
    ClaimCitation,
    CompetitorInfo,
    CriterionLiteral,
    CriticalReviewOutput,
    EvidenceJudgeOutput,
    ExtractedClaim,
    RuleAnswer,
)
from bore.domain import eligibility
from bore.domain.enums import (
    ClaimKind,
    ClaimSourceRole,
    CompetitorCategory,
    CriterionCode,
    EligibilityAnswer,
    FinalVerdict,
    JudgeVerdict,
    RejectionCode,
)

FORBIDDEN = ["7.0", "min_final", "requested_ideas", "umbral", "25%", "20%", "15%", "peso"]

_CONTEXT = {"sector": "inmobiliario", "country": "ES", "language": "es-ES"}

_ROLE_PHASES = [
    (role, phase)
    for role in sorted(PROMPT_ROLES)
    for phase in (("research", "structure") if role != "dedup_screener" else ("structure",))
]


def _stages_roles(method: str) -> set[str]:
    from bore.orchestration import stages

    source = Path(stages.__file__).read_text(encoding="utf-8")
    return set(re.findall(rf'{method}\(\s*"(\w+)"', source))


def test_prompts_blind_to_threshold_weights_and_count():
    """Los evaluadores son ciegos a umbrales, pesos y número pedido (7), en
    todas las combinaciones papel-fase."""
    for role, phase in _ROLE_PHASES:
        text = render_prompt(role, _CONTEXT, phase=phase)
        for token in FORBIDDEN:
            assert token not in text, f"{role}/{phase} contiene {token!r}"
        assert "# prompt_version:" in text


def test_every_used_role_resolves_prompt_per_phase():
    """Cada papel usado en do_research/do_analysis resuelve su prompt en la
    fase correcta, sin placeholders y con el bloque común incluido."""
    for role in _stages_roles("do_research"):
        text = render_prompt(role, _CONTEXT, phase="research")
        assert "{{" not in text
        assert "Reglas comunes" in text  # bloque _common.md concatenado
    for role in _stages_roles("do_analysis"):
        text = render_prompt(role, _CONTEXT, phase="structure")
        assert "{{" not in text
        assert "Reglas comunes" in text


def test_research_and_structure_prompts_differ():
    """Las instrucciones de investigación y de estructuración son distintas
    para los papeles bifásicos."""
    for role in (
        "sector_mapper",
        "demand_researcher",
        "screener",
        "market_validation",
        "critical_reviewer",
    ):
        research = render_prompt(role, _CONTEXT, phase="research")
        structure = render_prompt(role, _CONTEXT, phase="structure")
        assert research != structure


def test_leftover_placeholder_is_an_error():
    with pytest.raises(ValueError, match="placeholder"):
        render_prompt("sector_mapper", {}, phase="research")


def test_composite_prompt_version_covers_all_prompts():
    versions = prompt_versions()
    # Todos los papeles y fases en uso tienen versión declarada.
    assert len(versions) == 15  # 5 papeles x2 fases + 5 de fase única
    assert all(v != "unknown" for v in versions.values())
    composite = composite_prompt_version()
    assert composite.startswith("bore-prompts:")
    assert prompt_version("sector_mapper", "research") == "sector_mapper.research-v3"


def _literal_values(annotation) -> set:
    """Valores de un Literal (posiblemente anidado en una unión con None)."""
    vals: set = set()
    for arg in get_args(annotation):
        if arg is type(None):
            vals.add(None)
        elif getattr(arg, "__origin__", None) is not None or hasattr(arg, "__args__"):
            vals |= _literal_values(arg)
        else:
            vals.add(arg)
    return vals


def test_schema_literals_match_domain_enums():
    """Todo Literal del contrato coincide con el enum de dominio asociado."""
    assert _literal_values(CriterionLiteral) == set(CriterionCode)
    assert _literal_values(ExtractedClaim.model_fields["kind"].annotation) == set(ClaimKind)
    assert _literal_values(ExtractedClaim.model_fields["criterion"].annotation) == set(
        CriterionCode
    ) | {None}
    assert _literal_values(RuleAnswer.model_fields["rule"].annotation) == {
        r.value for r in eligibility.RULES
    }
    assert _literal_values(RuleAnswer.model_fields["answer"].annotation) == set(EligibilityAnswer)
    assert _literal_values(CompetitorInfo.model_fields["category"].annotation) == set(
        CompetitorCategory
    )
    assert _literal_values(EvidenceJudgeOutput.model_fields["verdict"].annotation) == set(
        JudgeVerdict
    )
    assert _literal_values(ClaimCitation.model_fields["role"].annotation) == set(ClaimSourceRole)
    assert _literal_values(CriticalReviewOutput.model_fields["final_verdict"].annotation) == set(
        FinalVerdict
    )


def test_no_free_str_fields_converted_to_enums():
    """Ningún campo `str` libre de ROLE_OUTPUT_TYPES se convierte a enum o se
    compara con códigos fijos en el código (regresión de RuleAnswer.rule)."""
    free_str_fields = []
    for role, model in ROLE_OUTPUT_TYPES.items():
        for name, field_info in model.model_fields.items():
            if field_info.annotation is str:
                free_str_fields.append(f"{role}.{name}")
    # Campos de texto libre legítimos (nunca se comparan con códigos):
    assert free_str_fields == [] or all(
        f.endswith((".notes", ".rationale", ".reason", ".positioning"))
        or f == "market_validation.llm_substitution_risk"
        or f.startswith("market_validation.technical_")
        or f.startswith("market_validation.maintenance_")
        for f in free_str_fields
    ), free_str_fields


# ------------------------- elegibilidad: consolidación -------------------------


def _assess(rule: RejectionCode, answer: str, reason: str = "r") -> eligibility.RuleAssessment:
    return eligibility.RuleAssessment(rule=rule, answer=EligibilityAnswer(answer), reason=reason)


def test_consolidate_missing_rules_become_uncertain():
    out, missing = eligibility.consolidate([_assess(RejectionCode.NOT_SOFTWARE, "no")])
    assert set(missing) == set(eligibility.RULES) - {RejectionCode.NOT_SOFTWARE}
    assert len(out) == len(eligibility.RULES)
    for a in out:
        if a.rule is not RejectionCode.NOT_SOFTWARE:
            assert a.answer is EligibilityAnswer.UNCERTAIN
            assert a.reason == eligibility.MISSING_RULE_REASON


def test_consolidate_duplicate_keeps_most_restrictive():
    out, missing = eligibility.consolidate(
        [
            _assess(RejectionCode.DATA_ACCESS, "no"),
            _assess(RejectionCode.DATA_ACCESS, "uncertain", "duda"),
            _assess(RejectionCode.DATA_ACCESS, "yes", "afirmativa"),
        ]
        + [_assess(r, "no") for r in eligibility.RULES if r is not RejectionCode.DATA_ACCESS]
    )
    assert missing == []
    data = next(a for a in out if a.rule is RejectionCode.DATA_ACCESS)
    assert data.answer is EligibilityAnswer.YES
    assert data.reason == "afirmativa"


def test_consolidate_order_no_uncertain_yes():
    # no < uncertain < yes en severidad
    out, _ = eligibility.consolidate(
        [
            _assess(RejectionCode.REDUNDANT, "yes"),
            _assess(RejectionCode.REDUNDANT, "no"),
        ]
        + [_assess(r, "no") for r in eligibility.RULES if r is not RejectionCode.REDUNDANT]
    )
    assert next(a for a in out if a.rule is RejectionCode.REDUNDANT).answer is (
        EligibilityAnswer.YES
    )
    out, _ = eligibility.consolidate(
        [
            _assess(RejectionCode.REDUNDANT, "no"),
            _assess(RejectionCode.REDUNDANT, "uncertain"),
        ]
        + [_assess(r, "no") for r in eligibility.RULES if r is not RejectionCode.REDUNDANT]
    )
    assert next(a for a in out if a.rule is RejectionCode.REDUNDANT).answer is (
        EligibilityAnswer.UNCERTAIN
    )

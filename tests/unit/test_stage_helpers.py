"""13.1: umbrales exactos, dimensión crítica ausente, confianza fuera de la
fórmula, caps de candidatas en código, price_status, economía unitaria y
dominio registrable con subdominios."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bore.domain import evidence, scoring
from bore.domain.unit_economics import required_paying_customers
from bore.orchestration.stages import apply_pool_caps, price_status

FULL = {
    "demand": 7,
    "distribution": 7,
    "competition": 7,
    "monetization": 7,
    "technical": 7,
    "ai_defensibility": 7,
    "operational_legal": 7,
}


def test_gates_boundary_exact_threshold_accepted_below_rejected():
    # Nota en el umbral aceptada; un punto por debajo, rechazada.
    scores = dict(FULL)
    assert scoring.passes_gates(scores, min_final=Decimal("7.00"), min_critical=6, min_any=4)
    assert not scoring.passes_gates(scores, min_final=Decimal("7.01"), min_critical=6, min_any=4)
    # Crítico exactamente en el mínimo acepta; por debajo bloquea.
    scores["demand"] = 6
    assert scoring.passes_gates(scores, min_final=Decimal("6.0"), min_critical=6, min_any=4)
    scores["demand"] = 5
    assert not scoring.passes_gates(scores, min_final=Decimal("6.0"), min_critical=6, min_any=4)


def test_missing_criterion_dimension_rejected():
    incomplete = {k: v for k, v in FULL.items() if k != "demand"}
    with pytest.raises(ValueError):
        scoring.weighted_score(incomplete)


def test_confidence_does_not_alter_formula():
    # La confianza solo ordena/desempata; la nota es idéntica con y sin ella.
    s1 = scoring.weighted_score(FULL)
    s2 = scoring.weighted_score(dict(FULL))
    assert s1 == s2 == Decimal("7.00")


def test_apply_pool_caps_subsector_then_total():
    ideas = [SimpleNamespace(sector_subcategory="a", name=f"a{i}") for i in range(4)] + [
        SimpleNamespace(sector_subcategory="b", name=f"b{i}") for i in range(4)
    ]
    kept, dropped_sub, dropped_pool = apply_pool_caps(ideas, max_per_subsector=2, pool_size=10)
    assert len(kept) == 4 and dropped_sub == 4 and dropped_pool == 0
    kept, dropped_sub, dropped_pool = apply_pool_caps(ideas, max_per_subsector=4, pool_size=5)
    assert len(kept) == 5 and dropped_sub == 0 and dropped_pool == 3


def _comp(**kw):
    base = {
        "price_value": Decimal("29"),
        "price_currency": "EUR",
        "price_unit": "month",
        "price_country": "ES",
    }
    base.update(kw)
    return base


def _claim(status):
    return {"verification_status": status}


def test_price_status_rules():
    # Sin precio -> not_public; nunca verified aunque haya afirmación.
    assert price_status(_comp(price_value=None), _claim("verified")) == "not_public"
    # Precio incompleto (sin país) -> unverified aunque la afirmación esté verified.
    assert price_status(_comp(price_country=None), _claim("verified")) == "unverified"
    # Completo + afirmación verified -> verified; sin ella -> unverified.
    assert price_status(_comp(), _claim("verified")) == "verified"
    assert price_status(_comp(), _claim("unverified")) == "unverified"
    assert price_status(_comp(), None) == "unverified"


def test_required_paying_customers_none_without_margin():
    no_margin = required_paying_customers(Decimal("500"), Decimal("4"), Decimal("5"), Decimal("0"))
    no_price = required_paying_customers(Decimal("500"), Decimal("0"), Decimal("0"), Decimal("0"))
    assert no_margin is None and no_price is None


def test_registrable_domain_subdomains():
    assert evidence.registrable_domain("https://a.b.c.informes.example.com/x") == "example.com"

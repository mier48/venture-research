import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bore.domain.enums import StageId, StopReason
from bore.orchestration.budgets import (
    BudgetExceeded,
    BudgetManager,
    Pricing,
    stage_search_budget,
)
from bore.providers.contracts import UsageRecord

PRICING = Pricing(
    pricing_version="t",
    web_search_usd_per_1000=Decimal("10"),
    fallback_input=Decimal("10"),
    fallback_cached_input=Decimal("5"),
    fallback_output=Decimal("30"),
    models={},
)


def budget(max_usd="10", ws=180, calls=200, dur=5400):
    return BudgetManager(
        max_usd=Decimal(max_usd),
        max_web_search_calls=ws,
        max_model_calls=calls,
        elapsed_seconds=lambda: 0.0,
        max_duration_seconds=dur,
        reserve_remaining=24,
        pricing=PRICING,
    )


def ctx_with(limits=None):
    return SimpleNamespace(config={"limits": limits or {}})


def test_split_defaults():
    assert stage_search_budget(ctx_with(), StageId.SECTOR_MAPPING) == 6
    assert stage_search_budget(ctx_with(), StageId.DEMAND_RESEARCH) == 16
    assert stage_search_budget(ctx_with(), StageId.SHALLOW_SCREENING, eligible=100) == 30
    assert stage_search_budget(ctx_with(), StageId.MARKET_VALIDATION, shortlisted=8) == 80
    assert stage_search_budget(ctx_with(), StageId.ADVERSARIAL_REVIEW, reviewed=6) == 24


def test_split_configurable():
    cfg = ctx_with({"search_split": {"sector_mapping": 2}})
    assert stage_search_budget(cfg, StageId.SECTOR_MAPPING) == 2


def test_precheck_usd_blocks():
    b = budget(max_usd="0.05")
    with pytest.raises(BudgetExceeded) as e:
        b.check_before_call(
            model="x", est_input_tokens=3000, max_output_tokens=2000, web_searches=0
        )
    assert e.value.stop_reason == StopReason.BUDGET_EXCEEDED


def test_cut_blocks_subsequent_calls():
    b = budget(calls=1)
    b.check_before_call(model="x")
    usage = UsageRecord(provider="f", model="x", requests=1)
    b.record(usage, Decimal("0.01"))
    with pytest.raises(BudgetExceeded):
        b.check_before_call(model="x")
    # Tras el corte, ninguna llamada más se lanza.
    with pytest.raises(BudgetExceeded):
        b.check_before_call(model="x")


def test_reserve_once_per_opportunity():
    b = budget()
    opp = uuid.uuid4()
    assert b.reserve_available(opp)
    b.spend_reserve(opp, 4)
    assert b.reserve_remaining == 20
    assert not b.reserve_available(opp)
    other = uuid.uuid4()
    assert b.reserve_available(other)


def test_record_overflow_raises():
    b = budget(max_usd="0.001")
    usage = UsageRecord(provider="f", model="x", requests=1, tokens_input=10**9)
    with pytest.raises(BudgetExceeded) as e:
        b.record(usage, PRICING.call_cost("x", usage)[0])
    assert e.value.stop_reason == StopReason.BUDGET_EXCEEDED

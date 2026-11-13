"""Validación 4.2: refs desconocidas descartadas con evento, fact sin refs ->
hypothesis. Las URL solo llegan por la lista de fuentes del proveedor (cubierto
en la integración del pipeline)."""

import uuid

import pytest

import bore.orchestration.stages as st
from bore.agents.schemas import ExtractedClaim
from bore.domain.enums import StageId
from bore.orchestration.stages import PipelineContext, persist_claims


@pytest.fixture
def ctx(monkeypatch):
    state = {"events": [], "claims": [], "links": []}

    async def fake_event(self, et, msg, *, level="info", payload=None):
        state["events"].append(et)

    async def fake_insert_claim(session, claim_id, **fields):
        state["claims"].append(fields)

    async def fake_insert_link(session, claim_id, source_id, **fields):
        state["links"].append((claim_id, source_id))

    monkeypatch.setattr(st.repo, "insert_claim", fake_insert_claim)
    monkeypatch.setattr(st.repo, "insert_claim_source", fake_insert_link)
    monkeypatch.setattr(st.PipelineContext, "event", fake_event)

    ctx_obj = PipelineContext(
        session=None,  # type: ignore[arg-type]
        session_factory=None,
        run_id=uuid.uuid4(),
        config={},
        is_synthetic=False,
        research=None,  # type: ignore[arg-type]
        analysis=None,  # type: ignore[arg-type]
        verifier=None,  # type: ignore[arg-type]
        budget=None,  # type: ignore[arg-type]
        pricing=None,  # type: ignore[arg-type]
    )
    ctx_obj.stage = StageId.SECTOR_MAPPING
    return ctx_obj, state


async def test_unknown_ref_discarded_and_fact_degraded(ctx):
    c, state = ctx
    ref_map = {"S1": uuid.uuid4()}
    out = await persist_claims(
        c,
        [
            ExtractedClaim(
                text="a",
                kind="fact",
                criterion=None,
                source_refs=["S1", "S99"],
                evidence_quote=None,
            ),
            ExtractedClaim(
                text="b", kind="fact", criterion=None, source_refs=[], evidence_quote=None
            ),
        ],
        ref_map,
        opportunity_id=None,
        origin_agent="test",
    )
    assert len(out) == 2
    assert state["claims"][0]["kind"] == "fact" and len(state["links"]) == 1
    assert state["claims"][1]["kind"] == "hypothesis"
    assert "unknown_source_ref" in state["events"]

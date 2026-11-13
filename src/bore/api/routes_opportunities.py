"""Endpoint de ficha de oportunidad (8.1)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bore.api.errors import ApiError
from bore.db import repositories as repo
from bore.db.session import new_session

router = APIRouter()


async def _session() -> Any:
    async with new_session() as s:
        yield s


@router.get("/opportunities/{opp_id}")
async def opportunity_detail(
    opp_id: uuid.UUID,
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> dict[str, Any]:
    opp = await repo.get_opportunity(session, opp_id)
    if opp is None:
        raise ApiError(404, "not_found", "Oportunidad no encontrada")
    competitors = await repo.list_competitors(session, opp_id)
    claims = await repo.list_claims(session, opp["research_run_id"], opportunity_id=opp_id)
    links = await repo.list_claim_sources(session, [c["id"] for c in claims])
    source_ids = {x["source_id"] for x in links}
    sources = {
        str(s["id"]): s
        for s in await repo.list_sources(session, opp["research_run_id"])
        if s["id"] in source_ids
    }
    scores_initial = await repo.list_criterion_scores(session, opp_id, revision="initial")
    scores_final = await repo.list_criterion_scores(session, opp_id, revision="final")
    reviews = await repo.list_reviews(session, opp_id)
    initial_by_crit = {s["criterion_code"]: s["score"] for s in scores_initial}
    score_diff = {
        s["criterion_code"]: s["score"] - initial_by_crit.get(s["criterion_code"], 0)
        for s in scores_final
        if s["score"] is not None and s["score"] != initial_by_crit.get(s["criterion_code"])
    }
    links_by_claim: dict[str, list[dict[str, Any]]] = {}
    for x in links:
        src = sources.get(str(x["source_id"]))
        links_by_claim.setdefault(str(x["claim_id"]), []).append(
            {
                "url": src["url"] if src else None,
                "access_status": src["access_status"] if src else None,
                "support_type": x["support_type"],
                "quote_match": x["quote_match"],
            }
        )
    return {
        "id": str(opp["id"]),
        "research_run_id": str(opp["research_run_id"]),
        "name": opp["name"],
        "status": opp["status"],
        "final_score": float(opp["final_score"]) if opp["final_score"] is not None else None,
        "confidence": opp["confidence"],
        "rank": opp["rank"],
        "sector_subcategory": opp["sector_subcategory"],
        "customer_segment": opp["customer_segment"],
        "buyer": opp["buyer"],
        "problem": opp["problem"],
        "solution": opp["solution"],
        "value_proposition": opp["value_proposition"],
        "business_model": opp["business_model"],
        "mvp_scope": opp["mvp_scope"],
        "excluded_features": opp["excluded_features"],
        "acquisition": opp["acquisition"],
        "assumptions": opp["assumptions"],
        "unit_economics": opp["unit_economics"],
        "risks": opp["risks"],
        "competitors": [
            {
                "name": c["name"],
                "website_url": c["website_url"],
                "category": c["category"],
                "positioning": c["positioning"],
                "price_value": float(c["price_value"]) if c["price_value"] is not None else None,
                "price_currency": c["price_currency"],
                "price_unit": c["price_unit"],
                "price_country": c["price_country"],
                "price_status": c["price_status"],
                "strengths": c["strengths"],
                "weaknesses": c["weaknesses"],
            }
            for c in competitors
        ],
        "criterion_scores": {
            "initial": [
                {
                    "criterion": s["criterion_code"],
                    "score": s["score"],
                    "confidence": s["confidence"],
                }
                for s in scores_initial
            ],
            "final": [
                {
                    "criterion": s["criterion_code"],
                    "score": s["score"],
                    "confidence": s["confidence"],
                    "rationale": s["rationale"],
                }
                for s in scores_final
            ],
            "diff": score_diff,
        },
        "claims": [
            {
                "text": c["text"],
                "kind": c["kind"],
                "criterion": c["criterion_code"],
                "verification_status": c["verification_status"],
                "disputed": c["disputed"],
                "evidence": links_by_claim.get(str(c["id"]), []),
            }
            for c in claims
        ],
        "sources": [
            {
                "url": s["url"],
                "title": s["title"],
                "access_status": s["access_status"],
                "provenance": s["provenance"],
                "retrieved_at": s["retrieved_at"].isoformat() if s["retrieved_at"] else None,
            }
            for s in sources.values()
        ],
        "reviews": [
            {
                "objections": r["objections"],
                "recommended_changes": r["recommended_changes"],
                "final_verdict": r["final_verdict"],
            }
            for r in reviews
        ],
    }

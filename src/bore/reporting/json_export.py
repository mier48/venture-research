"""Exportación JSON completa y estable de una ejecución (8.4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import repositories as repo
from bore.reporting.markdown import claim_label


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


async def _opp_json(
    session: AsyncSession, run: dict[str, Any], o: dict[str, Any]
) -> dict[str, Any]:
    claims = await repo.list_claims(session, run["id"], opportunity_id=o["id"])
    links = await repo.list_claim_sources(session, [c["id"] for c in claims])
    links_by_claim: dict[str, list[dict[str, Any]]] = {}
    for x in links:
        links_by_claim.setdefault(str(x["claim_id"]), []).append(
            {
                "source_id": str(x["source_id"]),
                "support_type": x["support_type"],
                "quote_match": x["quote_match"],
            }
        )
    return {
        "id": str(o["id"]),
        "name": o["name"],
        "status": o["status"],
        "rejection_code": o["rejection_code"],
        "final_score": _f(o["final_score"]),
        "confidence": o["confidence"],
        "rank": o["rank"],
        "sector_subcategory": o["sector_subcategory"],
        "customer_segment": o["customer_segment"],
        "buyer": o["buyer"],
        "problem": o["problem"],
        "solution": o["solution"],
        "value_proposition": o["value_proposition"],
        "business_model": o["business_model"],
        "mvp_scope": o["mvp_scope"],
        "excluded_features": o["excluded_features"],
        "acquisition": o["acquisition"],
        "assumptions": o["assumptions"],
        "unit_economics": o["unit_economics"],
        "risks": o["risks"],
        "competitors": [
            {
                "name": c["name"],
                "website_url": c["website_url"],
                "category": c["category"],
                "positioning": c["positioning"],
                "price_value": _f(c["price_value"]),
                "price_currency": c["price_currency"],
                "price_unit": c["price_unit"],
                "price_country": c["price_country"],
                "price_status": c["price_status"],
                "strengths": c["strengths"],
                "weaknesses": c["weaknesses"],
            }
            for c in await repo.list_competitors(session, o["id"])
        ],
        "criterion_scores": {
            rev: [
                {
                    "criterion": s["criterion_code"],
                    "score": s["score"],
                    "confidence": s["confidence"],
                    "rationale": s["rationale"],
                }
                for s in await repo.list_criterion_scores(session, o["id"], revision=rev)
            ]
            for rev in ("initial", "final")
        },
        "claims": [
            {
                "text": c["text"],
                "kind": c["kind"],
                "criterion": c["criterion_code"],
                "verification_status": c["verification_status"],
                "disputed": c["disputed"],
                "label": claim_label(c),
                "links": links_by_claim.get(str(c["id"]), []),
            }
            for c in claims
        ],
        "reviews": [
            {
                "objections": r["objections"],
                "recommended_changes": r["recommended_changes"],
                "final_verdict": r["final_verdict"],
            }
            for r in await repo.list_reviews(session, o["id"])
        ],
    }


async def export_json(session: AsyncSession, run: dict[str, Any]) -> str:
    opps = await repo.list_opportunities(session, run["id"])
    stats = await repo.opportunity_status_counts(session, run["id"])
    sources = await repo.list_sources(session, run["id"])
    return json.dumps(
        {
            "id": str(run["id"]),
            "sector": run["sector"],
            "country": run["country"],
            "language": run["language"],
            "status": run["status"],
            "stop_reason": run["stop_reason"],
            "is_synthetic": run["is_synthetic"],
            "created_at": run["created_at"].isoformat(),
            "model_config": run["model_config"],
            "rubric_version": run["rubric_version"],
            "prompt_version": run["prompt_version"],
            "pricing_version": run["pricing_version"],
            "statistics": stats,
            "cost": {
                "actual_usd": float(run["actual_cost_usd"] or 0),
                "estimated": bool(run["cost_is_estimated"]),
                "web_search_calls": run["web_search_calls"],
                "model_calls": run["model_calls"],
            },
            "opportunities": [await _opp_json(session, run, o) for o in opps],
            "sources": [
                {
                    "url": s["url"],
                    "canonical_url": s["canonical_url"],
                    "registrable_domain": s["registrable_domain"],
                    "title": s["title"],
                    "provenance": s["provenance"],
                    "access_status": s["access_status"],
                    "retrieved_at": s["retrieved_at"].isoformat() if s["retrieved_at"] else None,
                }
                for s in sorted(sources, key=lambda x: x["canonical_url"])
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

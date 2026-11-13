"""Exportación JSON básica."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import repositories as repo


async def export_json(session: AsyncSession, run: dict[str, Any]) -> str:
    opps = await repo.list_opportunities(session, run["id"])
    stats = await repo.opportunity_status_counts(session, run["id"])
    return json.dumps(
        {
            "id": str(run["id"]),
            "sector": run["sector"],
            "status": run["status"],
            "stop_reason": run["stop_reason"],
            "is_synthetic": run["is_synthetic"],
            "statistics": stats,
            "cost": {
                "actual_usd": float(run["actual_cost_usd"] or 0),
                "estimated": bool(run["cost_is_estimated"]),
                "web_search_calls": run["web_search_calls"],
                "model_calls": run["model_calls"],
            },
            "opportunities": [
                {
                    "id": str(o["id"]),
                    "name": o["name"],
                    "status": o["status"],
                    "final_score": float(o["final_score"])
                    if o["final_score"] is not None
                    else None,
                    "confidence": o["confidence"],
                    "rank": o["rank"],
                }
                for o in opps
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

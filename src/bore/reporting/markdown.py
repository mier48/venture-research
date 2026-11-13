"""Exportación Markdown básica (la versión completa de 8.4 llega en Fase 4)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import repositories as repo

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!>|<>])")


def _esc(text: str | None) -> str:
    if not text:
        return ""
    return _MD_SPECIAL.sub(r"\\\1", text)


_STATUS_LABELS = {
    "verified": "verificada",
    "partially_supported": "respaldo parcial",
    "unverified": "no verificada",
    "contradicted": "contradicha",
    "estimate": "estimación",
    "hypothesis": "hipótesis",
}


def _claim_label(claim: dict[str, Any]) -> str:
    if claim["kind"] in ("estimate", "hypothesis"):
        return _STATUS_LABELS[claim["kind"]]
    label = str(_STATUS_LABELS.get(claim["verification_status"], claim["verification_status"]))
    if claim["disputed"]:
        label += ", disputada"
    return label


async def export_markdown(session: AsyncSession, run: dict[str, Any]) -> str:
    opps = await repo.list_opportunities(session, run["id"])
    sources = await repo.list_sources(session, run["id"])
    stats = await repo.opportunity_status_counts(session, run["id"])
    lines = [
        f"# Investigación de oportunidades — {_esc(run['sector'])}",
        "",
        f"- Fecha: {run['created_at']}",
        f"- Estado: {run['status']}" + (f" ({run['stop_reason']})" if run["stop_reason"] else ""),
        f"- Rúbrica: {_esc(run['rubric_version'])} · Prompts: {_esc(run['prompt_version'])}"
        f" · Tarifas: {_esc(run['pricing_version'])}",
        f"- Coste real: {run['actual_cost_usd'] or 0} USD"
        + (" (estimado)" if run["cost_estimated"] else ""),
        "",
        "**Advertencia**: la nota prioriza líneas de investigación; no demuestra "
        "rentabilidad ni disposición real a pagar.",
        "",
    ]
    if run["is_synthetic"]:
        lines += ["> **DEMO — datos ficticios**. Toda la información es sintética.", ""]
    published = [o for o in opps if o["status"] == "published"]
    lines.append(f"## Ranking ({len(published)} publicadas)")
    lines.append("")
    for o in sorted(published, key=lambda x: x["rank"] or 0):
        lines.append(
            f"{o['rank']}. **{_esc(o['name'])}** — nota {o['final_score']} · "
            f"confianza {o['confidence']}"
        )
    lines.append("")
    for o in sorted(published, key=lambda x: x["rank"] or 0):
        lines += [
            f"### {_esc(o['name'])}",
            "",
            f"- Segmento: {_esc(o['customer_segment'])}",
            f"- Comprador: {_esc(o['buyer'])}",
            f"- Problema: {_esc(o['problem'])}",
            f"- Solución: {_esc(o['solution'])}",
            f"- Modelo de negocio: {_esc(o['business_model'])}",
            "",
        ]
        comps = await repo.list_competitors(session, o["id"])
        if comps:
            lines.append("#### Competidores")
            for c in comps:
                price = (
                    f"{c['price_value']} {c['price_currency'] or ''}/{c['price_unit'] or ''}"
                    if c["price_value"] is not None
                    else "sin precio público"
                )
                lines.append(
                    f"- {_esc(c['name'])} ({c['category']}): {price} — precio {c['price_status']}"
                )
            lines.append("")
        claims = await repo.list_claims(session, run["id"], opportunity_id=o["id"])
        if claims:
            lines.append("#### Afirmaciones")
            for c in claims:
                lines.append(f"- [{_claim_label(c)}] {_esc(c['text'])}")
            lines.append("")
    others = [o for o in opps if o["status"] != "published"]
    if others:
        lines.append("## Candidatas no publicadas")
        lines.append("")
        for o in others:
            reason = o["rejection_code"] or o["status"]
            lines.append(f"- {_esc(o['name'])} — {o['status']} ({reason})")
        lines.append("")
    lines += ["## Recuentos por estado", ""]
    for status, n in sorted(stats.items()):
        lines.append(f"- {status}: {n}")
    lines += ["", "## Fuentes", ""]
    for i, s in enumerate(sorted(sources, key=lambda x: x["canonical_url"]), start=1):
        lines.append(
            f"{i}. {_esc(s['title'] or s['canonical_url'])} — {s['url']} "
            f"· consulta: {s['retrieved_at']} · acceso: {s['access_status']}"
        )
    return "\n".join(lines) + "\n"

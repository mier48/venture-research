"""Exportación Markdown de una ejecución (8.4): cabecera, advertencia de
alcance, ranking, ficha por publicada, no publicadas con motivo y lista
numerada de fuentes. Todo texto externo (títulos, extractos) se escapa para
que no pueda inyectar enlaces, imágenes ni HTML."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import repositories as repo
from bore.domain import scoring

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!>|<>\"'])")


def _esc(text: str | None) -> str:
    """Escapa todos los caracteres significativos de Markdown y HTML."""
    if not text:
        return ""
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _link(url: str | None) -> str:
    """Solo enlaza http/https; cualquier otro valor se muestra como texto."""
    if url and url.startswith(("http://", "https://")):
        return url
    return f"`{_esc(url)}`" if url else "—"


_STATUS_LABELS = {
    "verified": "verificada",
    "partially_supported": "respaldo parcial",
    "unverified": "no verificada",
    "contradicted": "contradicha",
    "estimate": "estimación",
    "hypothesis": "hipótesis",
}


def claim_label(claim: dict[str, Any]) -> str:
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

    # --- Cabecera -----------------------------------------------------------
    models = run.get("model_config") or {}
    lines = [
        f"# Investigación de oportunidades — {_esc(run['sector'])}",
        "",
        f"- Fecha: {run['created_at']}",
        f"- Estado: {run['status']}" + (f" ({run['stop_reason']})" if run["stop_reason"] else ""),
        f"- Modelos: {_esc(str(models))}",
        f"- Rúbrica: {_esc(run['rubric_version'])} · Prompts: {_esc(run['prompt_version'])}"
        f" · Tarifas: {_esc(run['pricing_version'])}",
        f"- Coste: {run['actual_cost_usd'] or 0} USD"
        + (" (estimado)" if run["cost_is_estimated"] else "")
        + f" · llamadas al modelo: {run['model_calls']} · búsquedas web: {run['web_search_calls']}",
        "- Recuentos por estado: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())),
        "",
        "> **Advertencia de alcance**: la nota prioriza líneas de investigación; "
        "no demuestra rentabilidad ni disposición real a pagar.",
        "",
    ]
    if run["is_synthetic"]:
        lines += ["> **DEMO — datos ficticios**. Toda la información es sintética.", ""]

    # --- Ranking ------------------------------------------------------------
    published = sorted(
        [o for o in opps if o["status"] == "published"], key=lambda x: x["rank"] or 0
    )
    lines.append(f"## Ranking ({len(published)} publicadas)")
    lines.append("")
    for o in published:
        lines.append(
            f"{o['rank']}. **{_esc(o['name'])}** — nota {o['final_score']} · "
            f"confianza {o['confidence']}"
        )
    lines.append("")

    # --- Fichas de publicadas -----------------------------------------------
    for o in published:
        lines += await _ficha(session, run, o)

    # --- No publicadas -------------------------------------------------------
    others = [o for o in opps if o["status"] != "published"]
    if others:
        lines.append("## Candidatas no publicadas")
        lines.append("")
        for o in others:
            reason = o["rejection_code"] or "—"
            lines.append(f"- {_esc(o['name'])} — **{o['status']}** ({_esc(reason)})")
        lines.append("")

    # --- Fuentes -------------------------------------------------------------
    lines.append("## Fuentes")
    lines.append("")
    for i, s in enumerate(sorted(sources, key=lambda x: x["canonical_url"]), start=1):
        fecha = s["retrieved_at"].isoformat() if s["retrieved_at"] else "—"
        lines.append(
            f"{i}. {_esc(s['title'] or s['canonical_url'])} — {_link(s['url'])} "
            f"· consulta: {fecha} · acceso: {s['access_status']}"
        )
    return "\n".join(lines) + "\n"


async def _ficha(session: AsyncSession, run: dict[str, Any], o: dict[str, Any]) -> list[str]:
    lines = [
        f"## Ficha: {_esc(o['name'])}",
        "",
        f"- Descripción: {_esc(o['solution'])}",
        f"- Segmento: {_esc(o['customer_segment'])} · Comprador: {_esc(o['buyer'])}",
        f"- Problema: {_esc(o['problem'])}",
        f"- Propuesta de valor: {_esc(o['value_proposition'])}",
        f"- Modelo de negocio: {_esc(o['business_model'])}",
        f"- MVP: {_esc(o['mvp_scope'])}",
        f"- Excluidas: {_esc(o['excluded_features'])}",
        "",
    ]
    acq = o.get("acquisition") or {}
    if acq:
        lines.append("- Adquisición: " + _esc(", ".join(acq.get("channels") or [])))
        lines.append("")
    econ = o.get("unit_economics") or {}
    if econ:
        lines += [
            "### Economía unitaria (hipótesis)",
            f"- Precio: {_esc(econ.get('price_eur_month'))} €/mes · variable: "
            f"{_esc(econ.get('variable_cost_eur_month'))} · "
            f"fijos: {_esc(econ.get('fixed_cost_eur_month'))}",
            f"- Clientes de pago necesarios: {_esc(econ.get('required_paying_customers'))}",
            f"- Penetración (universo verificado): {_esc(econ.get('penetration_pct'))} %",
            "",
        ]
    risks = o.get("risks") or {}
    if risks:
        lines.append("### Riesgos y barreras")
        for note in risks.get("technical_notes") or []:
            lines.append(f"- Técnica: {_esc(note)}")
        for note in risks.get("data_barriers") or []:
            lines.append(f"- Datos: {_esc(note)}")
        for note in risks.get("legal_operational_risks") or []:
            lines.append(f"- Legal-operativa: {_esc(note)}")
        if risks.get("llm_substitution_risk"):
            lines.append(f"- Sustitución por LLM: {_esc(risks['llm_substitution_risk'])}")
        est = risks.get("estimates") or {}
        if any(est.values()):
            lines.append(
                f"- Estimaciones: alcance {_esc(est.get('technical_scope'))}; "
                f"mantenimiento {_esc(est.get('maintenance_cost'))}; "
                f"semanas MVP {_esc(est.get('mvp_weeks'))}"
            )
        lines.append("")

    comps = await repo.list_competitors(session, o["id"])
    lines.append("### Competidores")
    if comps:
        for c in comps:
            price = (
                f"{c['price_value']} {c['price_currency'] or ''}/{c['price_unit'] or ''}"
                if c["price_value"] is not None
                else "no verificado"
            )
            lines.append(
                f"- {_esc(c['name'])} ({c['category']}): {_esc(c['positioning'])} — "
                f"precio {price} ({c['price_status']}) — {_link(c['website_url'])}"
            )
    else:
        lines.append("- Sin competidores registrados.")
    lines.append("")

    scores_i = {
        s["criterion_code"]: s
        for s in await repo.list_criterion_scores(session, o["id"], revision="initial")
    }
    scores_f = {
        s["criterion_code"]: s
        for s in await repo.list_criterion_scores(session, o["id"], revision="final")
    }
    lines += [
        "### Notas por criterio",
        "",
        "| Criterio | Inicial | Final | Δ | Confianza |",
        "|---|---|---|---|---|",
    ]
    for code in scoring.WEIGHTS:
        si, sf = scores_i.get(code), scores_f.get(code)
        diff = (
            sf["score"] - si["score"]
            if si and sf and si["score"] is not None and sf["score"] is not None
            else "—"
        )
        lines.append(
            f"| {code} | {(si or {}).get('score', '—')} | {(sf or {}).get('score', '—')} "
            f"| {diff} | {(sf or si or {}).get('confidence', '—')} |"
        )
    lines.append(f"\nNota ponderada final: **{o['final_score']}** · confianza {o['confidence']}")
    expl = next((s for s in scores_f.values() if s.get("rationale")), None)
    if expl:
        lines.append(f"\nExplicación: {_esc(expl['rationale'])}")
    lines.append("")

    claims = await repo.list_claims(session, run["id"], opportunity_id=o["id"])
    if claims:
        lines.append("### Afirmaciones")
        for c in claims:
            lines.append(f"- [{claim_label(c)}] {_esc(c['text'])}")
        lines.append("")

    reviews = await repo.list_reviews(session, o["id"])
    if reviews:
        r = reviews[-1]
        lines.append("### Revisión crítica")
        for obj in r.get("objections") or []:
            lines.append(f"- Objeción: {_esc(obj)}")
        for ch in r.get("recommended_changes") or []:
            lines.append(f"- Cambio recomendado: {_esc(ch)}")
        lines.append(f"- Veredicto: {_esc(r.get('final_verdict'))}")
        lines.append("")
    return lines

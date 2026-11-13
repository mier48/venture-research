"""Deduplicación de candidatas: huella exacta y grupos semánticos (4.5)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from bore.domain.evidence import normalize_fingerprint_text


def fingerprint(customer_segment: str, problem: str) -> str:
    """Hash de la normalización de segmento de cliente y problema."""
    words = normalize_fingerprint_text(customer_segment) + normalize_fingerprint_text(problem)
    normalized = " ".join(sorted(set(words)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateView:
    """Vista mínima de una candidata para deduplicar."""

    id: Any  # UUID o str
    fingerprint: str
    completeness: int  # nº de campos informativos rellenos; conserva la más completa


@dataclass(frozen=True)
class DedupResult:
    kept_id: Any
    duplicate_ids: tuple[Any, ...]


def apply_semantic_groups(
    candidates: list[CandidateView], groups: list[list[int]]
) -> list[DedupResult]:
    """`groups` contiene listas de índices de `candidates` que son equivalentes.
    El código conserva la más completa de cada grupo; el resto es `duplicate`."""
    results: list[DedupResult] = []
    for group in groups:
        members = [candidates[i] for i in group]
        keeper = max(
            enumerate(members),
            key=lambda pair: (pair[1].completeness, -pair[0]),
        )[1]
        duplicates = tuple(c.id for c in members if c.id != keeper.id)
        results.append(DedupResult(kept_id=keeper.id, duplicate_ids=duplicates))
    return results

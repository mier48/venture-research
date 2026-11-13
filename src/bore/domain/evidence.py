"""URLs canónicas, dominios registrables, coincidencia de citas y estado de
afirmaciones (secciones 4.3, 4.4 y 4.5 de la spec)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

from bore.domain.enums import AccessStatus, JudgeVerdict, Provenance, VerificationStatus

# Lista de sufijos incluida en el paquete: sin descarga en ejecución (3.1).
_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset({"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src"})

_PROVIDER_PROVENANCE = frozenset({Provenance.PROVIDER_ANNOTATION, Provenance.PROVIDER_SOURCE_LIST})


def canonical_url(url: str) -> str:
    """Esquema y host en minúsculas, sin fragmento ni parámetros de seguimiento."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def registrable_domain(url: str) -> str:
    """Dominio registrable (eTLD+1) con la lista de sufijos del paquete."""
    ext = _EXTRACTOR(url)
    if not ext.suffix:
        return ext.domain.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


_TYPO_QUOTES = {
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "`": "'",
}


def normalize_quote(text: str) -> str:
    """Normaliza espacios, mayúsculas y comillas tipográficas (4.4)."""
    for src, dst in _TYPO_QUOTES.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split()).lower()


def quote_match(evidence_quote: str | None, page_text: str | None) -> bool:
    if not evidence_quote or not page_text:
        return False
    needle = normalize_quote(evidence_quote)
    if not needle:
        return False
    return needle in normalize_quote(page_text)


def is_provider_provenance(provenance: Provenance, *, allow_synthetic: bool) -> bool:
    """D6: `synthetic` cuenta como fuente del proveedor solo en ejecuciones
    is_synthetic; en ejecuciones reales nunca aparece."""
    if provenance in _PROVIDER_PROVENANCE:
        return True
    if provenance is Provenance.SYNTHETIC:
        return allow_synthetic
    return False


@dataclass(frozen=True)
class ClaimSourceLink:
    """Estado de un vínculo afirmación–fuente tras la auditoría."""

    provenance: Provenance
    access_status: AccessStatus
    quote_match: bool
    judge_verdict: JudgeVerdict | None
    support_type: str  # "supports" | "contradicts" | "mentions"


_ACCESSIBLE = frozenset({AccessStatus.OK, AccessStatus.REDIRECTED})


def claim_verification_status(
    links: list[ClaimSourceLink], *, allow_synthetic: bool = False
) -> tuple[VerificationStatus, bool]:
    """Tabla de estados de 4.4. Devuelve (estado, disputed).

    - verified: alguna fuente del proveedor accesible con quote_match o
      judge_verdict=supports.
    - contradicted: contradicción confirmada y ningún respaldo confirmado.
    - partially_supported: respaldo y contradicción a la vez (disputed), o
      fuente del proveedor citada sin confirmación de contenido.
    - unverified: cualquier otro caso.
    """
    confirmed_support = False
    confirmed_contradiction = False
    provider_cited = False

    for link in links:
        if not is_provider_provenance(link.provenance, allow_synthetic=allow_synthetic):
            continue
        # Solo los vínculos «supports» cuentan como citada como respaldo; un
        # «mentions» no aporta nada sin veredicto explícito del juez.
        if link.support_type == "supports":
            provider_cited = True
        if link.access_status not in _ACCESSIBLE:
            continue
        if link.judge_verdict is JudgeVerdict.CONTRADICTS:
            confirmed_contradiction = True
        elif link.judge_verdict is JudgeVerdict.SUPPORTS:
            confirmed_support = True
        elif link.quote_match:
            if link.support_type == "supports":
                confirmed_support = True
            elif link.support_type == "contradicts":
                confirmed_contradiction = True

    if confirmed_support and confirmed_contradiction:
        return VerificationStatus.PARTIALLY_SUPPORTED, True
    if confirmed_support:
        return VerificationStatus.VERIFIED, False
    if confirmed_contradiction:
        return VerificationStatus.CONTRADICTED, False
    if provider_cited:
        return VerificationStatus.PARTIALLY_SUPPORTED, False
    return VerificationStatus.UNVERIFIED, False


_WS_RE = re.compile(r"\s+")


def normalize_fingerprint_text(text: str) -> list[str]:
    """Minúsculas, sin acentos ni puntuación, palabras ordenadas (4.5)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    words = [w for w in _WS_RE.split(text) if w]
    return sorted(words)

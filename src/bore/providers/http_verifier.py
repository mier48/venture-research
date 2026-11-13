"""Verificador de fuentes HTTP con protección SSRF (4.3).

Una sola petición GET por URL citada, sin seguir enlaces de la página: solo
http/https, resolución DNS con rechazo de IPs privadas/loopback/link-local/
multicast/reservadas/unspecified/CGNAT/metadatos (incl. IPv4-mapeadas),
redirecciones manuales con validación en cada salto (máx. 5), timeouts, límite
de tamaño por streaming, User-Agent identificable, robots.txt con caché por
host (la petición a robots.txt aplica las mismas protecciones) y ≤1 petición/s
por host. Solo extrae texto de `text/html` y `text/plain` (html.parser de
stdlib, sin scripts ni estilos); el texto completo solo vive en memoria.

Riesgo residual documentado: TOCTOU/DNS rebinding entre la resolución
validada y la conexión real de httpx (ver docs/DECISIONS.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from bore.domain.enums import AccessStatus
from bore.providers.contracts import SourceCheck

MAX_REDIRECTS = 5
MAX_BYTES = 2 * 1024 * 1024
EXCERPT_LEN = 500
DEFAULT_USER_AGENT = "BORE-SourceVerifier/1.0 (+research bot; verificador de citas)"
_METADATA_HOSTNAMES = frozenset(
    {"metadata.google.internal", "metadata", "instance-data", "169.254.169.254"}
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_GCP_METADATA = ipaddress.ip_network("fd00:ec2::254/128")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_BLOCKED_STATUSES = frozenset({401, 402, 403})
_TEXT_TYPES = ("text/html", "text/plain")

DnsResolver = Callable[[str], Awaitable[list[str]]]


class _PolicyBlock(Exception):
    """Esquema, host o IP prohibidos por política (SSRF/robots)."""


def _ip_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return True
    if ip.is_reserved or ip.is_unspecified:
        return True
    if ip.version == 4 and ip in _CGNAT:
        return True
    if ip.version == 6:
        if ip in _GCP_METADATA:
            return True
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return _ip_forbidden(mapped)
    return False


class _TextExtractor(HTMLParser):
    """Extrae texto visible; ignora script/style y todo el marcado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def extract_text(body: bytes, content_type: str) -> str | None:
    """Solo text/html y text/plain; devuelve None para otros tipos."""
    base = content_type.split(";")[0].strip().lower()
    if base == "text/plain":
        return " ".join(body.decode("utf-8", errors="replace").split())
    if base == "text/html":
        parser = _TextExtractor()
        parser.feed(body.decode("utf-8", errors="replace"))
        return parser.text()
    return None


class HttpSourceVerifier:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 15.0,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        per_host_min_interval: float = 1.0,
        respect_robots: bool = True,
        dns_resolver: DnsResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ua = user_agent
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._min_interval = per_host_min_interval
        self._respect_robots = respect_robots
        self._resolver = dns_resolver or self._default_resolver
        self._client = client
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._rate_lock = asyncio.Lock()

    async def _default_resolver(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({str(info[4][0]) for info in infos})

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": self._ua, "Accept": "text/html, text/plain"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------- política --------------------------------

    async def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise _PolicyBlock(f"Esquema prohibido: {parts.scheme}")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or host in _METADATA_HOSTNAMES:
            raise _PolicyBlock("Host de metadatos o vacío")
        try:
            ips = [ipaddress.ip_address(host)]
        except ValueError:
            resolved = await self._resolver(host)
            if not resolved:
                raise _PolicyBlock("DNS sin registros") from None
            ips = [ipaddress.ip_address(a) for a in resolved]
        for ip in ips:
            if _ip_forbidden(ip):
                raise _PolicyBlock(f"IP prohibida: {ip}")

    async def _throttle(self, host: str) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_hit.get(host, -self._min_interval)
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_hit[host] = time.monotonic()

    async def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host in self._robots:
            parser = self._robots[host]
        else:
            parser = None
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            try:
                await self._check_url(robots_url)
                status, _, body, _ = await self._get(robots_url)
                if status == 200 and body is not None:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
            except (TimeoutError, _PolicyBlock, httpx.HTTPError):
                parser = None
            self._robots[host] = parser
        if parser is None:
            return True  # robots inaccesible: no se infiere prohibición (documentado)
        return parser.can_fetch(self._ua, url)

    # --------------------------------- GET -----------------------------------

    async def _get(self, url: str) -> tuple[int, str | None, bytes | None, str | None]:
        """Una petición GET con límite de tamaño por streaming.

        Devuelve (status, content_type, body o None si se saltó, location)."""
        host = urlsplit(url).netloc.lower()
        await self._throttle(host)
        client = await self._get_client()
        async with client.stream("GET", url) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                return resp.status_code, None, None, resp.headers.get("location")
            content_type = resp.headers.get("content-type", "")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes(65536):
                size += len(chunk)
                if size > self._max_bytes:
                    chunks.append(chunk[: len(chunk) - (size - self._max_bytes)])
                    break
                chunks.append(chunk)
            return resp.status_code, content_type, b"".join(chunks), None

    async def _get_chain(self, url: str) -> tuple[int, str | None, bytes | None, str, bool]:
        """Sigue redirecciones manualmente validando CADA salto (SSRF)."""
        current = url
        redirected = False
        for _ in range(self._max_redirects + 1):
            await self._check_url(current)
            status, ctype, body, location = await self._get(current)
            if status in _REDIRECT_STATUSES and location:
                current = urljoin(current, location)
                redirected = True
                continue
            return status, ctype, body, current, redirected
        raise _PolicyBlock("Demasiadas redirecciones") from None

    # ------------------------------ verify -----------------------------------

    async def verify(self, url: str) -> SourceCheck:
        final_url: str | None = None
        try:
            await self._check_url(url)
            if self._respect_robots and not await self._robots_allows(url):
                return SourceCheck(
                    url=url,
                    final_url=None,
                    access_status=AccessStatus.BLOCKED_BY_POLICY,
                    http_status=None,
                    content_hash=None,
                    excerpt=None,
                )
            status, ctype, body, final, redirected = await self._get_chain(url)
            final_url = final if redirected else None
        except _PolicyBlock:
            return SourceCheck(
                url=url,
                final_url=None,
                access_status=AccessStatus.BLOCKED_BY_POLICY,
                http_status=None,
                content_hash=None,
                excerpt=None,
            )
        except (TimeoutError, httpx.TimeoutException):
            return SourceCheck(
                url=url,
                final_url=None,
                access_status=AccessStatus.TIMEOUT,
                http_status=None,
                content_hash=None,
                excerpt=None,
            )
        except httpx.HTTPError:
            return SourceCheck(
                url=url,
                final_url=None,
                access_status=AccessStatus.ERROR,
                http_status=None,
                content_hash=None,
                excerpt=None,
            )

        if status in _BLOCKED_STATUSES:
            access = AccessStatus.BLOCKED
        elif status == 404:
            access = AccessStatus.NOT_FOUND
        elif status is not None and 200 <= status < 300:
            access = AccessStatus.REDIRECTED if redirected else AccessStatus.OK
        else:
            access = AccessStatus.ERROR
        text = (
            extract_text(body or b"", ctype or "")
            if access in (AccessStatus.OK, AccessStatus.REDIRECTED)
            else None
        )
        if access in (AccessStatus.OK, AccessStatus.REDIRECTED) and text is None:
            access = AccessStatus.ERROR
        return SourceCheck(
            url=url,
            final_url=final_url,
            access_status=access,
            http_status=status,
            content_hash=hashlib.sha256(text.encode()).hexdigest() if text else None,
            excerpt=(text or "")[:EXCERPT_LEN] or None,
            page_text=text,
        )

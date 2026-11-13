"""Verificador HTTP (4.3): esquemas, IPs prohibidas, redirecciones, tamaño,
timeout, robots y límite por host. DNS stubbeado; HTTP con respx."""

import httpx
import pytest
import respx

from bore.domain.enums import AccessStatus
from bore.providers.http_verifier import HttpSourceVerifier

PUBLIC_IP = "93.184.216.34"


async def public_dns(host: str):
    return [PUBLIC_IP]


def verifier(**kw):
    kw.setdefault("dns_resolver", public_dns)
    kw.setdefault("respect_robots", False)
    kw.setdefault("per_host_min_interval", 0.0)
    return HttpSourceVerifier(**kw)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "http://10.0.0.1/x",
        "http://127.0.0.1/x",
        "http://169.254.1.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1/x",
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[::ffff:10.0.0.1]/x",
        "http://[fd00:ec2::254]/x",
        "http://metadata.google.internal/x",
        "https://privada.example.com/x",
    ],
)
async def test_blocked_by_policy(url):
    async def dns(host):
        return ["10.1.2.3"] if host == "privada.example.com" else [PUBLIC_IP]

    v = verifier(dns_resolver=dns)
    check = await v.verify(url)
    assert check.access_status == AccessStatus.BLOCKED_BY_POLICY


@respx.mock
async def test_ok_extracts_text_and_hash():
    respx.get("https://example.com/p").respond(
        200,
        text="<html><head><style>x{}</style><script>evil()</script></head>"
        "<body><p>Hola mundo de ejemplo</p></body></html>",
        headers={"content-type": "text/html"},
    )
    v = verifier()
    check = await v.verify("https://example.com/p")
    assert check.access_status == AccessStatus.OK
    assert check.content_hash
    assert "Hola mundo de ejemplo" in check.page_text
    assert "evil" not in check.page_text
    assert check.excerpt and len(check.excerpt) <= 500


@respx.mock
async def test_redirect_validated_each_hop():
    respx.get("https://example.com/a").respond(302, headers={"location": "http://10.0.0.5/interno"})
    v = verifier()
    check = await v.verify("https://example.com/a")
    assert check.access_status == AccessStatus.BLOCKED_BY_POLICY


@respx.mock
async def test_redirect_ok_marks_redirected():
    respx.get("https://example.com/a").respond(301, headers={"location": "https://example.com/b"})
    respx.get("https://example.com/b").respond(
        200, text="contenido", headers={"content-type": "text/plain"}
    )
    v = verifier()
    check = await v.verify("https://example.com/a")
    assert check.access_status == AccessStatus.REDIRECTED
    assert check.final_url == "https://example.com/b"


@respx.mock
async def test_size_limit_truncates():
    big = "x" * (5 * 1024 * 1024)
    respx.get("https://example.com/big").respond(
        200, text=big, headers={"content-type": "text/plain"}
    )
    v = verifier(max_bytes=1024)
    check = await v.verify("https://example.com/big")
    assert check.access_status == AccessStatus.OK
    assert len(check.page_text) <= 1024


@respx.mock
async def test_timeout_maps_to_timeout():
    respx.get("https://example.com/slow").mock(side_effect=httpx.TimeoutException("t"))
    v = verifier()
    check = await v.verify("https://example.com/slow")
    assert check.access_status == AccessStatus.TIMEOUT


@respx.mock
async def test_forbidden_and_not_found():
    respx.get("https://example.com/secret").respond(403)
    respx.get("https://example.com/missing").respond(404)
    v = verifier()
    assert (await v.verify("https://example.com/secret")).access_status == (AccessStatus.BLOCKED)
    assert (await v.verify("https://example.com/missing")).access_status == (AccessStatus.NOT_FOUND)


@respx.mock
async def test_robots_disallow_blocks():
    respx.get("https://example.com/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /privada", headers={"content-type": "text/plain"}
    )
    v = verifier(respect_robots=True)
    check = await v.verify("https://example.com/privada/x")
    assert check.access_status == AccessStatus.BLOCKED_BY_POLICY


@respx.mock
async def test_robots_allow():
    respx.get("https://example.com/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /privada", headers={"content-type": "text/plain"}
    )
    respx.get("https://example.com/ok").respond(
        200, text="contenido", headers={"content-type": "text/plain"}
    )
    v = verifier(respect_robots=True)
    check = await v.verify("https://example.com/ok")
    assert check.access_status == AccessStatus.OK


@respx.mock
async def test_rate_limit_per_host():
    import time

    respx.get("https://example.com/x").respond(
        200, text="c", headers={"content-type": "text/plain"}
    )
    v = verifier(per_host_min_interval=0.25)
    start = time.monotonic()
    await v.verify("https://example.com/x")
    await v.verify("https://example.com/x")
    assert time.monotonic() - start >= 0.25

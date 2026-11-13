"""E2E 13.3: abrir UI -> crear investigación offline -> ver progreso ->
ranking -> abrir oportunidad -> consultar fuentes -> descargar informe."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_full_offline_flow(page, base_url):
    page.goto(base_url)
    expect(page).to_have_title(re.compile("Venture Research"))

    # Crear una investigación offline (reintentos por la regla de una
    # ejecución activa / intervalo mínimo entre POST).
    created = False
    for _ in range(4):
        page.get_by_label(re.compile("Sector")).fill("inmobiliario")
        page.get_by_label(re.compile("Demo offline")).check()
        page.get_by_role("button", name="Crear investigación").click()
        page.wait_for_load_state()
        if "/runs/" in page.url:
            created = True
            break
        page.wait_for_timeout(65_000)
    assert created, "No se pudo crear la ejecución (error/rate limit persistente)"
    match = re.search(r"/runs/([0-9a-f-]+)", page.url)
    assert match, "URL de detalle sin id de ejecución"
    run_id = match.group(1)

    # Progreso por sondeo HTMX hasta estado terminal.
    expect(page.locator("h2")).to_contain_text("inmobiliario")
    expect(page.get_by_text("Progreso")).to_be_visible()
    page.wait_for_selector("text=Descargar informe", timeout=240_000)
    expect(page.locator("[data-status='completed']")).to_be_visible()

    # Ranking y ficha.
    expect(page.locator("h3", has_text="Ranking")).to_be_visible()
    page.locator("table >> text=ficha").first.click()
    page.wait_for_load_state()
    expect(page.get_by_text("Afirmaciones")).to_be_visible()

    # Fuentes de la ficha.
    expect(page.locator("h3", has_text="Fuentes")).to_be_visible()
    first_link = page.locator("ol a").first
    href = first_link.get_attribute("href")
    assert href and href.startswith("http")

    # Descargar el informe Markdown y verificar contenido.
    page.goto(f"{base_url}/runs/{run_id}")
    with page.expect_download() as download_info:
        page.get_by_text("Descargar informe (Markdown)").click()
    download = download_info.value
    content = download.path().read_text(encoding="utf-8", errors="replace")
    assert "## Ranking" in content
    assert "## Fuentes" in content
    assert "DEMO" in content

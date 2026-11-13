import asyncio
import os
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _proactor_policy():
    # Playwright necesita ProactorEventLoop (subprocesos) en Windows; la
    # conftest de integración fija Selector para psycopg, así que se
    # restablece justo antes del fixture `playwright` (autouse se ejecuta antes).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    yield


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("BORE_E2E_BASE_URL", "http://127.0.0.1:8000")

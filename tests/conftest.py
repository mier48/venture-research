import os

import pytest

DATABASE_URL = os.environ.get("BORE_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("BORE_TEST_REDIS_URL")


def pytest_collection_modifyitems(config, items):
    if DATABASE_URL and REDIS_URL:
        return
    skip = pytest.mark.skip(
        reason="requires_services: define BORE_TEST_DATABASE_URL y BORE_TEST_REDIS_URL"
    )
    for item in items:
        if "requires_services" in item.keywords:
            item.add_marker(skip)

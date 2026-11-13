import os
import sys

os.environ.setdefault("BORE_MIN_SECONDS_BETWEEN_RUNS", "0")
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bore.db.tables import metadata

DATABASE_URL = os.environ.get("BORE_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("BORE_TEST_REDIS_URL")

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if not DATABASE_URL:
        pytest.skip("requires_services: falta BORE_TEST_DATABASE_URL")
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    from bore.db import session as db_session

    if db_session._engine is not None:
        await db_session._engine.dispose()
        db_session._engine = None
        db_session._sessionmaker = None
    await engine.dispose()


DEMO_CONFIG = {
    "sector": "inmobiliario",
    "country": "ES",
    "language": "es-ES",
    "requested_ideas": 5,
    "candidate_pool_size": 25,
    "max_candidates_per_subsector": 5,
    "deep_dive_limit": 8,
    "review_limit": 6,
    "min_final_score": 7.0,
    "min_critical_criterion_score": 6,
    "min_any_criterion_score": 4,
    "max_mvp_weeks": 6,
    "max_initial_budget_eur": 500,
    "min_target_operating_profit_eur_month": 500,
    "preferred_acquisition": ["seo", "aso", "communities", "referrals"],
    "limits": {
        "max_usd": 10.0,
        "max_web_search_calls": 180,
        "max_model_calls": 200,
        "max_duration_seconds": 5400,
        "max_retries_per_stage": 2,
    },
}


@pytest.fixture
async def run_id(db) -> uuid.UUID:
    from bore.db import repositories as repo

    async with db() as s:
        run = await repo.create_run(
            s,
            sector="inmobiliario",
            country="ES",
            language="es-ES",
            config=DEMO_CONFIG,
            is_synthetic=True,
            model_config=None,
            prompt_version="test",
            rubric_version="rubric-1.1",
            pricing_version="test",
        )
        await s.commit()
        return run["id"]

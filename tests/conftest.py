import os
import uuid

import asyncpg
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

_url = make_url(get_settings().database_url)
TEST_DB = "finbuddy_test"
TEST_URL = _url.set(database=TEST_DB).render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = TEST_URL
os.environ["STORAGE_DIR"] = "./storage-test"
os.environ["MLFLOW_ENABLED"] = "false"
os.environ["PRIZE_MODE"] = "test"  # tests use the mock even when .env points at the live portal
get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    """Create finbuddy_test if missing and run the real migrations on it (once)."""
    import asyncio

    from alembic.config import Config

    from alembic import command

    async def create():
        conn = await asyncpg.connect(
            user=_url.username, password=_url.password, host=_url.host, port=_url.port,
            database="postgres",
        )
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", TEST_DB):
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        await conn.close()

    asyncio.run(create())
    command.upgrade(Config("alembic.ini"), "head")


@pytest_asyncio.fixture(scope="session")
async def app_engine(_migrated_db):
    engine = create_async_engine(TEST_URL)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(app_engine):
    from app.db import get_session
    from app.main import app

    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    async with app_engine.begin() as conn:
        tables = (await conn.execute(text(
            "SELECT string_agg(quote_ident(tablename), ',') FROM pg_tables "
            "WHERE schemaname='public' "
            "AND tablename NOT IN ('alembic_version', 'categories', 'payment_methods')"
        ))).scalar()
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


async def _register(client, email: str | None = None) -> httpx.AsyncClient:
    email = email or f"{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def auth(client):
    """Auth headers for a fresh user."""
    return await _register(client)


@pytest_asyncio.fixture
async def other_auth(client):
    return await _register(client)


@pytest_asyncio.fixture
async def db(client, app_engine):
    """A session on the test DB for arranging/inspecting rows directly."""
    async with async_sessionmaker(app_engine, expire_on_commit=False)() as session:
        yield session

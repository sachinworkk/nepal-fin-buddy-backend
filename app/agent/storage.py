"""Postgres-backed Strands `Storage` for agent session snapshots.

Strands restores a snapshot from a sync bridge (`run_async` on a fresh event loop in a worker
thread), so this uses its own NullPool engine: pooled asyncpg connections are bound to the loop
that opened them and can't be shared with the app's engine.
"""

from functools import lru_cache

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import AgentStorage

T = AgentStorage.__table__


@lru_cache
def _engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


class PgStorage:
    async def write(self, key: str, data: bytes) -> None:
        stmt = insert(T).values(key=key, data=data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[T.c.key], set_={"data": data, "updated_at": func.now()})
        async with _engine().begin() as conn:
            await conn.execute(stmt)

    async def read(self, key: str) -> bytes | None:
        async with _engine().connect() as conn:
            return await conn.scalar(select(T.c.data).where(T.c.key == key))

    async def delete(self, key: str) -> None:
        async with _engine().begin() as conn:
            await conn.execute(delete(T).where(T.c.key == key))

    async def list(self, query: str) -> list[str]:
        q = select(T.c.key).order_by(T.c.key)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = q.where(T.c.key.like(f"{escaped}%", escape="\\"))
        async with _engine().connect() as conn:
            return list(await conn.scalars(q))

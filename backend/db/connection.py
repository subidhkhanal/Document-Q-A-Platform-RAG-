"""PostgreSQL connection management — single centralized pool."""

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

from backend.config import DATABASE_URL, DB_POOL_MAX, DB_POOL_MIN

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result wrapper
# ---------------------------------------------------------------------------

class Result:
    """Wraps execute() results to provide lastrowid and rowcount."""
    __slots__ = ("lastrowid", "rowcount")

    def __init__(self, lastrowid: Optional[int], rowcount: int):
        self.lastrowid = lastrowid
        self.rowcount = rowcount


def _rowcount(status: str) -> int:
    # asyncpg returns e.g. "UPDATE 3" or "DELETE 1"
    if status:
        parts = status.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            return int(parts[-1])
    return 0


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

class Database:
    """Thin async wrapper over an asyncpg connection."""

    def __init__(self, conn: asyncpg.Connection, pool: asyncpg.Pool):
        self._conn = conn
        self._pool = pool

    async def execute(self, query: str, *args) -> Result:
        """Execute a write query (INSERT/UPDATE/DELETE). Auto-detects INSERT
        and appends RETURNING id to capture lastrowid."""
        is_insert = query.lstrip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in query.upper():
            query = query.rstrip().rstrip(";") + " RETURNING id"
            row = await self._conn.fetchrow(query, *args)
            return Result(lastrowid=row["id"] if row else None, rowcount=1)
        status = await self._conn.execute(query, *args)
        return Result(lastrowid=None, rowcount=_rowcount(status))

    async def run(self, query: str, *args) -> int:
        """Execute a statement verbatim (no RETURNING rewrite). Returns rowcount."""
        return _rowcount(await self._conn.execute(query, *args))

    async def executemany(self, query: str, args_list) -> None:
        await self._conn.executemany(query, args_list)

    async def fetch_one(self, query: str, *args) -> Optional[dict]:
        """Fetch a single row as a dict, or None."""
        row = await self._conn.fetchrow(query, *args)
        return dict(row) if row else None

    async def fetch_all(self, query: str, *args) -> list[dict]:
        """Fetch all rows as a list of dicts."""
        rows = await self._conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetch_val(self, query: str, *args):
        return await self._conn.fetchval(query, *args)

    async def execute_script(self, sql: str) -> None:
        """Execute multi-statement DDL (no parameters)."""
        await self._conn.execute(sql)

    def transaction(self):
        """`async with db.transaction():` — commits on success, rolls back on error."""
        return self._conn.transaction()

    async def close(self) -> None:
        """Release connection back to its pool."""
        await self._pool.release(self._conn)


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_central_pool() -> None:
    """Create the DB pool. Called once at app startup."""
    global _pool
    if _pool is not None:
        return
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is required. "
            "Set it to your Supabase PostgreSQL connection string."
        )
    _pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=DB_POOL_MIN, max_size=DB_POOL_MAX, command_timeout=30, init=_init_connection
    )
    logger.info("DB pool created")


async def get_central_db() -> Database:
    """Acquire a connection from the pool."""
    if _pool is None:
        await init_central_pool()
    conn = await _pool.acquire()
    return Database(conn=conn, pool=_pool)


@asynccontextmanager
async def db_session():
    """`async with db_session() as db:` — acquire and always release a connection."""
    db = await get_central_db()
    try:
        yield db
    finally:
        await db.close()


async def close_pools() -> None:
    """Close the pool. Called at app shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
    logger.info("DB pool closed")

"""Auth database — thin re-export layer.

All auth operations use the central DB.
Other modules import get_db from here for backward compatibility.
"""

import hashlib

import asyncpg

from backend.db.connection import get_central_db, init_central_pool
from backend.db.schema import SCHEMA

# Re-export for modules that still import `from backend.auth.database import get_db`
get_db = get_central_db

# Serialises schema migration when several instances start at once.
_SCHEMA_LOCK_ID = 7_310_442
_SCHEMA_HASH = hashlib.sha256(SCHEMA.encode()).hexdigest()[:16]


async def _schema_is_current(db) -> bool:
    try:
        return await db.fetch_val("SELECT value FROM app_secrets WHERE name = 'schema_hash'") == _SCHEMA_HASH
    except asyncpg.UndefinedTableError:
        return False


async def init_db():
    """Initialize the DB pool, create/migrate tables (skipped when the schema is
    unchanged, which keeps serverless cold starts short) and load the token key."""
    from backend.auth.tokens import load_signing_secret

    await init_central_pool()
    db = await get_central_db()
    try:
        if not await _schema_is_current(db):
            await db.fetch_val("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK_ID)
            try:
                await db.execute_script(SCHEMA)
                await db.run(
                    """INSERT INTO app_secrets (name, value) VALUES ('schema_hash', $1)
                       ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value""",
                    _SCHEMA_HASH,
                )
            finally:
                await db.fetch_val("SELECT pg_advisory_unlock($1)", _SCHEMA_LOCK_ID)
    finally:
        await db.close()
    await load_signing_secret()

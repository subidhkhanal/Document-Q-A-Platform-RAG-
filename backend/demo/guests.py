"""Anonymous guest accounts for the public demo.

Each visitor gets their own user in the demo tenant, so their uploads are private
to them (the same ACL model a real tenant uses) while the shared Sample Library is
readable by everyone. Guest data expires after GUEST_TTL_HOURS.
"""

import logging
import secrets
from typing import Dict

from backend.config import DEMO_TENANT_SLUG, GUEST_TTL_HOURS
from backend.db.connection import db_session
from backend.documents import repository as repo

logger = logging.getLogger(__name__)

GUEST_PROJECT_SLUG = "my-documents"


async def create_guest() -> Dict[str, int]:
    username = f"guest-{secrets.token_hex(6)}"
    async with db_session() as db:
        async with db.transaction():
            tenant_id = await db.fetch_val("SELECT id FROM tenants WHERE slug = $1", DEMO_TENANT_SLUG)
            user_id = await db.fetch_val(
                "INSERT INTO users (username, hashed_password, tenant_id, role) VALUES ($1, '', $2, 'guest') RETURNING id",
                username, tenant_id,
            )
            await db.fetch_val(
                """INSERT INTO projects (user_id, slug, title, description)
                   VALUES ($1, $2, 'My Documents', 'Your private uploads (deleted after the demo session expires)')
                   RETURNING id""",
                user_id, GUEST_PROJECT_SLUG,
            )
    return {"user_id": user_id, "tenant_id": tenant_id, "username": username}


async def cleanup_expired_guests() -> int:
    """Revoke expired guests' documents (async purge via cleanup jobs) and deactivate them."""
    async with db_session() as db:
        guests = await db.fetch_all(
            """SELECT id, tenant_id FROM users
               WHERE role = 'guest' AND is_active AND username LIKE 'guest-%'
                 AND created_at < NOW() - make_interval(hours => $1)""",
            GUEST_TTL_HOURS,
        )
    for guest in guests:
        async with db_session() as db:
            async with db.transaction():
                docs = await db.fetch_all(
                    "SELECT id FROM documents WHERE user_id = $1 AND deleted_at IS NULL", guest["id"]
                )
                for doc in docs:
                    await db.run(
                        "UPDATE documents SET deleted_at = NOW(), active_version = NULL WHERE id = $1", doc["id"]
                    )
                    await repo.enqueue_job(db, "delete_document", guest["tenant_id"], guest["id"], doc["id"])
                await db.run("DELETE FROM conversations WHERE user_id = $1", guest["id"])
                await db.run("UPDATE users SET is_active = FALSE WHERE id = $1", guest["id"])
    if guests:
        logger.info("Expired %d guest accounts", len(guests))
    return len(guests)

"""Seeds the public demo's shared, read-only Sample Library.

Files in DEMO_LIBRARY_DIR are ingested through the normal pipeline as documents
owned by a dedicated `library` user with tenant-wide visibility, inside a project
shared with the whole demo tenant. Seeding is idempotent: unchanged files are
skipped and a changed file becomes a new version of the same document.
"""

import hashlib
import logging
from pathlib import Path

from backend.auth.principal import load_principal_by_username
from backend.config import DEMO_LIBRARY_DIR, DEMO_LIBRARY_PROJECT_SLUG, DEMO_LIBRARY_USERNAME, DEMO_TENANT_SLUG
from backend.db.connection import db_session
from backend.documents import repository as repo
from backend.ingestion.parsers import SUPPORTED_TYPES, UnsupportedDocument, detect_type

logger = logging.getLogger(__name__)

# Serialises seeding when several instances start at once.
_SEED_LOCK_ID = 7_310_443


async def _ensure_library_owner() -> int:
    async with db_session() as db:
        tenant_id = await db.fetch_val("SELECT id FROM tenants WHERE slug = $1", DEMO_TENANT_SLUG)
        await db.run(
            """INSERT INTO users (username, hashed_password, tenant_id, role) VALUES ($1, '', $2, 'member')
               ON CONFLICT (username) DO NOTHING""",
            DEMO_LIBRARY_USERNAME, tenant_id,
        )
        user_id = await db.fetch_val("SELECT id FROM users WHERE username = $1", DEMO_LIBRARY_USERNAME)
        await db.run(
            """INSERT INTO projects (user_id, slug, title, description, visibility)
               VALUES ($1, $2, 'Sample Library', 'Shared, read-only sample documents from a fictional company. Ask anything about them.', 'tenant')
               ON CONFLICT (user_id, slug) DO NOTHING""",
            user_id, DEMO_LIBRARY_PROJECT_SLUG,
        )
        return await db.fetch_val(
            "SELECT id FROM projects WHERE user_id = $1 AND slug = $2", user_id, DEMO_LIBRARY_PROJECT_SLUG
        )


async def seed_library() -> int:
    """Returns the number of ingestion jobs enqueued."""
    directory = Path(DEMO_LIBRARY_DIR)
    files = sorted(p for p in directory.glob("*") if p.suffix.lower() in SUPPORTED_TYPES) if directory.is_dir() else []
    if not files:
        return 0

    async with db_session() as lock_db:
        await lock_db.fetch_val("SELECT pg_advisory_lock($1)", _SEED_LOCK_ID)
        try:
            project_id = await _ensure_library_owner()
            principal = await load_principal_by_username(DEMO_LIBRARY_USERNAME)
            async with db_session() as db:
                existing = {
                    r["filename"]: r
                    for r in await db.fetch_all(
                        """SELECT d.id, d.filename, v.content_hash, v.status
                           FROM documents d
                           LEFT JOIN LATERAL (SELECT content_hash, status FROM document_versions
                                              WHERE document_id = d.id ORDER BY version DESC LIMIT 1) v ON TRUE
                           WHERE d.project_id = $1 AND d.user_id = $2 AND d.deleted_at IS NULL""",
                        project_id, principal.user_id,
                    )
                }

            enqueued = 0
            for path in files:
                content = path.read_bytes()
                content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
                current = existing.get(path.name)
                if current and current["content_hash"] == content_hash and current["status"] != "failed":
                    continue
                retry = bool(current and current["status"] == "failed")
                try:
                    _, extension, mime_type = detect_type(path.name, content)
                except UnsupportedDocument as e:
                    logger.warning("Skipping sample %s: %s", path.name, e)
                    continue
                job = await repo.create_ingestion(
                    principal, filename=path.name, extension=extension, mime_type=mime_type, content=content,
                    content_hash=content_hash, idempotency_key=None if retry else f"seed-{content_hash[7:39]}", project_id=project_id,
                    document_id=current["id"] if current else None, visibility="tenant",
                )
                enqueued += int(job["created"])
            if enqueued:
                logger.info("Seeded %d sample library documents", enqueued)
            return enqueued
        finally:
            await lock_db.fetch_val("SELECT pg_advisory_unlock($1)", _SEED_LOCK_ID)

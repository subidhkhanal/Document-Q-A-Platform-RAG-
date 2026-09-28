"""PostgreSQL access for documents, immutable versions, chunks and ingestion jobs."""

import uuid
from typing import Any, Dict, List, Optional

import asyncpg

from backend.auth.principal import Principal
from backend.authz.policy import READABLE_PREDICATE
from backend.db.connection import Database, db_session
from backend.storage.object_store import get_object_store, object_key

VISIBILITIES = ("private", "tenant", "restricted")


class IdempotencyConflict(Exception):
    """The idempotency key was already used for different content."""


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:20]}"


def chunk_id_for(document_id: int, version: int, chunk_index: int, content_hash: str) -> str:
    # Deterministic, so a retried ingestion overwrites the same vectors instead of
    # duplicating them; the content-hash suffix keeps ids unique even if database ids
    # are ever reused (e.g. after a restore) while the derived index keeps old vectors.
    return f"chk_{document_id}_{version}_{chunk_index}_{content_hash.split(':')[-1][:8]}"


def _job_view(job: Dict[str, Any], active_version: Optional[int] = None) -> Dict[str, Any]:
    return {
        "job_id": job["id"],
        "document_id": job["document_id"],
        "document_version": job["document_version"],
        "status": job["status"],
        "stage": job["stage"],
        "indexed_chunks": job["indexed_chunks"],
        "active_version": active_version,
        "error": job["error"],
        "attempts": job["attempts"],
    }


async def _find_idempotent_job(db: Database, principal: Principal, key: str) -> Optional[dict]:
    return await db.fetch_one(
        "SELECT * FROM ingestion_jobs WHERE tenant_id = $1 AND user_id = $2 AND idempotency_key = $3",
        principal.tenant_id, principal.user_id, key,
    )


# ---------------------------------------------------------------------------
# Upload / versions
# ---------------------------------------------------------------------------

async def create_ingestion(
    principal: Principal,
    *,
    filename: str,
    extension: str,
    mime_type: str,
    content: bytes,
    content_hash: str,
    idempotency_key: Optional[str],
    project_id: Optional[int],
    document_id: Optional[int] = None,
    visibility: str = "private",
    client_metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create (or reuse, for a repeated idempotency key) a document version and its
    ingestion job. The raw bytes are written to object storage inside the
    transaction, so a failed write leaves no orphaned version row."""
    async with db_session() as db:
        if idempotency_key:
            existing = await _find_idempotent_job(db, principal, idempotency_key)
            if existing:
                if existing["content_hash"] != content_hash:
                    raise IdempotencyConflict()
                return {**_job_view(existing), "tenant_id": principal.tenant_id, "created": False}

        try:
            async with db.transaction():
                if document_id is not None:
                    doc = await db.fetch_one(
                        "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL FOR UPDATE",
                        document_id, principal.tenant_id,
                    )
                    if not doc:
                        raise NotFound()
                    if doc["user_id"] != principal.user_id and not principal.is_admin:
                        raise Forbidden()
                    await db.run(
                        """UPDATE documents SET filename = $2, extension = $3, size_bytes = $4,
                               mime_type = $5, updated_at = NOW() WHERE id = $1""",
                        document_id, filename, extension, len(content), mime_type,
                    )
                else:
                    document_id = await db.fetch_val(
                        """INSERT INTO documents
                               (user_id, tenant_id, filename, extension, size_bytes, mime_type, project_id, visibility)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
                        principal.user_id, principal.tenant_id, filename, extension, len(content),
                        mime_type, project_id, visibility,
                    )

                version = await db.fetch_val(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM document_versions WHERE document_id = $1",
                    document_id,
                )
                key = object_key(principal.tenant_id, document_id, version, content_hash, extension)
                await db.run(
                    """INSERT INTO document_versions
                           (document_id, version, filename, object_key, content_hash, size_bytes, mime_type, created_by,
                            client_metadata)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    document_id, version, filename, key, content_hash, len(content), mime_type, principal.user_id,
                    client_metadata or {},
                )
                job_id = new_job_id()
                job = await db.fetch_one(
                    """INSERT INTO ingestion_jobs
                           (id, kind, tenant_id, user_id, document_id, document_version, idempotency_key, content_hash)
                       VALUES ($1, 'ingest', $2, $3, $4, $5, $6, $7) RETURNING *""",
                    job_id, principal.tenant_id, principal.user_id, document_id, version,
                    idempotency_key, content_hash,
                )
                await get_object_store().put(key, content, mime_type)
        except asyncpg.UniqueViolationError:
            if not idempotency_key:
                raise
            # Concurrent retry with the same idempotency key won the race.
            existing = await _find_idempotent_job(db, principal, idempotency_key)
            if not existing or existing["content_hash"] != content_hash:
                raise IdempotencyConflict()
            return {**_job_view(existing), "tenant_id": principal.tenant_id, "created": False}

    return {**_job_view(job), "tenant_id": principal.tenant_id, "created": True}


async def get_job_for_principal(principal: Principal, job_id: str) -> Optional[Dict[str, Any]]:
    """Job status, visible to the uploader, tenant admins, or anyone who can read the document."""
    async with db_session() as db:
        job = await db.fetch_one(
            "SELECT * FROM ingestion_jobs WHERE id = $1 AND tenant_id = $2", job_id, principal.tenant_id
        )
        if not job:
            return None
        doc = await db.fetch_one("SELECT active_version FROM documents WHERE id = $1", job["document_id"])
        if job["user_id"] != principal.user_id and not principal.is_admin:
            readable = await db.fetch_one(
                f"SELECT 1 FROM documents d WHERE d.id = $4 AND {READABLE_PREDICATE}",
                principal.tenant_id, principal.user_id, list(principal.group_ids), job["document_id"],
            )
            if not readable:
                return None
    return _job_view(job, doc["active_version"] if doc else None)


# ---------------------------------------------------------------------------
# Listing / detail
# ---------------------------------------------------------------------------

_LIST_COLUMNS = """
    d.id, d.filename, d.extension, d.size_bytes, d.project_id, d.visibility, d.user_id AS owner_id,
    d.active_version, d.acl_version, d.created_at, d.updated_at,
    lv.version AS latest_version, lv.status AS latest_status, lv.error AS latest_error,
    lv.warnings AS latest_warnings,
    av.chunk_count AS chunk_count, av.page_count AS page_count
"""
_LIST_JOINS = """
    LEFT JOIN LATERAL (SELECT version, status, error, warnings FROM document_versions
                       WHERE document_id = d.id ORDER BY version DESC LIMIT 1) lv ON TRUE
    LEFT JOIN document_versions av ON av.document_id = d.id AND av.version = d.active_version
"""


LEGACY_ERROR = "Indexed by a previous version of the app; run the legacy migration or re-upload"


def _doc_view(r: Dict[str, Any]) -> Dict[str, Any]:
    # An active version is queryable even while a newer version is still indexing.
    if r["active_version"]:
        status = "READY"
    elif r["latest_version"] is None or r["latest_status"] == "failed":
        status = "FAILED"
    else:
        status = "PROCESSING"
    return {
        "document_id": r["id"],
        "filename": r["filename"],
        "source_type": r["extension"].lstrip("."),
        "size_bytes": r["size_bytes"],
        "project_id": r["project_id"],
        "visibility": r["visibility"],
        "owner_id": r["owner_id"],
        "active_version": r["active_version"],
        "latest_version": r["latest_version"],
        "latest_version_status": r["latest_status"],
        "latest_error": r["latest_error"] if r["latest_version"] is not None else LEGACY_ERROR,
        "warnings": r["latest_warnings"] or [],
        "status": status,
        "chunk_count": r["chunk_count"] or 0,
        "page_count": r["page_count"],
        "created_at": str(r["created_at"]),
        "updated_at": str(r["updated_at"]),
    }


async def list_readable_documents(principal: Principal, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    query = f"SELECT {_LIST_COLUMNS} FROM documents d {_LIST_JOINS} WHERE {READABLE_PREDICATE}"
    args: List[Any] = [principal.tenant_id, principal.user_id, list(principal.group_ids)]
    if project_id is not None:
        query += " AND d.project_id = $4"
        args.append(project_id)
    query += " ORDER BY d.created_at DESC"
    async with db_session() as db:
        rows = await db.fetch_all(query, *args)
    docs = [_doc_view(r) for r in rows]
    for d in docs:
        d["can_manage"] = d["owner_id"] == principal.user_id or principal.is_admin
    return docs


async def count_owned_documents(principal: Principal) -> int:
    async with db_session() as db:
        return await db.fetch_val(
            "SELECT COUNT(*) FROM documents WHERE user_id = $1 AND deleted_at IS NULL", principal.user_id
        )


async def get_readable_document_detail(principal: Principal, document_id: int) -> Optional[Dict[str, Any]]:
    async with db_session() as db:
        row = await db.fetch_one(
            f"SELECT {_LIST_COLUMNS} FROM documents d {_LIST_JOINS} WHERE d.id = $4 AND {READABLE_PREDICATE}",
            principal.tenant_id, principal.user_id, list(principal.group_ids), document_id,
        )
        if not row:
            return None
        versions = await db.fetch_all(
            """SELECT version, status, filename, content_hash, size_bytes, page_count, chunk_count,
                      embedding_model_version, warnings, error, created_at, ready_at
               FROM document_versions WHERE document_id = $1 ORDER BY version DESC""",
            document_id,
        )
    detail = _doc_view(row)
    detail["versions"] = [
        {**v, "created_at": str(v["created_at"]), "ready_at": str(v["ready_at"]) if v["ready_at"] else None}
        for v in versions
    ]
    return detail


async def get_tenant_document(principal: Principal, document_id: int) -> Optional[Dict[str, Any]]:
    """Raw document row within the caller's tenant (for management checks)."""
    async with db_session() as db:
        return await db.fetch_one(
            "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id, principal.tenant_id,
        )


async def get_version(document_id: int, version: int) -> Optional[Dict[str, Any]]:
    async with db_session() as db:
        return await db.fetch_one(
            "SELECT * FROM document_versions WHERE document_id = $1 AND version = $2", document_id, version
        )


# ---------------------------------------------------------------------------
# ACL management
# ---------------------------------------------------------------------------

async def get_acl(document_id: int) -> Dict[str, Any]:
    async with db_session() as db:
        doc = await db.fetch_one("SELECT visibility, acl_version FROM documents WHERE id = $1", document_id)
        grants = await db.fetch_all(
            "SELECT principal_type, principal_id FROM document_acl WHERE document_id = $1 ORDER BY 1, 2",
            document_id,
        )
    return {
        "document_id": document_id,
        "visibility": doc["visibility"],
        "acl_version": doc["acl_version"],
        "users": [g["principal_id"] for g in grants if g["principal_type"] == "user"],
        "groups": [g["principal_id"] for g in grants if g["principal_type"] == "group"],
    }


async def set_acl(principal: Principal, document_id: int, visibility: str,
                  user_ids: List[int], group_ids: List[int]) -> Dict[str, Any]:
    """Replace the document's ACL. Takes effect on the very next query because
    authorization scope is resolved from these tables per request."""
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}")
    async with db_session() as db:
        async with db.transaction():
            valid_users = await db.fetch_all(
                "SELECT id FROM users WHERE tenant_id = $1 AND id = ANY($2::int[])", principal.tenant_id, user_ids
            )
            valid_groups = await db.fetch_all(
                "SELECT id FROM groups WHERE tenant_id = $1 AND id = ANY($2::int[])", principal.tenant_id, group_ids
            )
            if len(valid_users) != len(set(user_ids)) or len(valid_groups) != len(set(group_ids)):
                raise ValueError("All users and groups must belong to your tenant")

            await db.run("DELETE FROM document_acl WHERE document_id = $1", document_id)
            grants = [(document_id, "user", u) for u in set(user_ids)] + [(document_id, "group", g) for g in set(group_ids)]
            if grants:
                await db.executemany(
                    "INSERT INTO document_acl (document_id, principal_type, principal_id) VALUES ($1, $2, $3)", grants
                )
            await db.run(
                "UPDATE documents SET visibility = $2, acl_version = acl_version + 1, updated_at = NOW() WHERE id = $1",
                document_id, visibility,
            )
    return await get_acl(document_id)


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

async def soft_delete_document(principal: Principal, document_id: int) -> Optional[str]:
    """Revoke access immediately (deleted_at) and enqueue asynchronous cleanup of
    vectors, chunks and objects. Returns the cleanup job id."""
    async with db_session() as db:
        async with db.transaction():
            updated = await db.run(
                "UPDATE documents SET deleted_at = NOW(), active_version = NULL, updated_at = NOW() "
                "WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
                document_id, principal.tenant_id,
            )
            if not updated:
                return None
            return await enqueue_job(db, "delete_document", principal.tenant_id, principal.user_id, document_id)


async def enqueue_job(db: Database, kind: str, tenant_id: int, user_id: Optional[int],
                      document_id: int, version: Optional[int] = None) -> str:
    job_id = new_job_id()
    await db.run(
        """INSERT INTO ingestion_jobs (id, kind, tenant_id, user_id, document_id, document_version)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        job_id, kind, tenant_id, user_id, document_id, version,
    )
    return job_id


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

async def replace_chunks(tenant_id: int, document_id: int, version: int, chunks: List[Dict[str, Any]]) -> None:
    async with db_session() as db:
        async with db.transaction():
            await db.run(
                "DELETE FROM chunks WHERE document_id = $1 AND document_version = $2", document_id, version
            )
            await db.executemany(
                """INSERT INTO chunks
                       (chunk_id, tenant_id, document_id, document_version, chunk_index, page_number,
                        section_title, text, token_count, content_hash, embedding_model_version)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
                [
                    (c["chunk_id"], tenant_id, document_id, version, c["chunk_index"], c.get("page_number"),
                     c.get("section_title"), c["text"], c.get("token_count"), c["content_hash"],
                     c["embedding_model_version"])
                    for c in chunks
                ],
            )


async def chunk_ids_for(document_id: int, version: Optional[int] = None) -> List[str]:
    async with db_session() as db:
        if version is None:
            rows = await db.fetch_all("SELECT chunk_id FROM chunks WHERE document_id = $1", document_id)
        else:
            rows = await db.fetch_all(
                "SELECT chunk_id FROM chunks WHERE document_id = $1 AND document_version = $2", document_id, version
            )
    return [r["chunk_id"] for r in rows]


async def delete_chunks(document_id: int, version: Optional[int] = None) -> None:
    async with db_session() as db:
        if version is None:
            await db.run("DELETE FROM chunks WHERE document_id = $1", document_id)
        else:
            await db.run("DELETE FROM chunks WHERE document_id = $1 AND document_version = $2", document_id, version)


async def get_chunk_with_context(principal: Principal, chunk_id: str, context_size: int = 1) -> Optional[Dict[str, Any]]:
    """A chunk plus neighbours, only if its document version is readable and active."""
    async with db_session() as db:
        chunk = await db.fetch_one(
            f"""SELECT c.chunk_id, c.document_id, c.document_version, c.chunk_index, c.page_number,
                       c.section_title, c.text, v.filename
                FROM chunks c
                JOIN documents d ON d.id = c.document_id AND d.active_version = c.document_version
                JOIN document_versions v ON v.document_id = c.document_id AND v.version = c.document_version
                WHERE c.chunk_id = $4 AND {READABLE_PREDICATE}""",
            principal.tenant_id, principal.user_id, list(principal.group_ids), chunk_id,
        )
        if not chunk:
            return None
        neighbours = await db.fetch_all(
            """SELECT chunk_id, chunk_index, page_number, text FROM chunks
               WHERE document_id = $1 AND document_version = $2
                 AND chunk_index BETWEEN $3 AND $4 AND chunk_id <> $5
               ORDER BY chunk_index""",
            chunk["document_id"], chunk["document_version"],
            chunk["chunk_index"] - context_size, chunk["chunk_index"] + context_size, chunk_id,
        )
    return {
        **chunk,
        "prev_chunks": [n for n in neighbours if n["chunk_index"] < chunk["chunk_index"]],
        "next_chunks": [n for n in neighbours if n["chunk_index"] > chunk["chunk_index"]],
    }

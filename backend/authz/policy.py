"""Document authorization policy.

PostgreSQL is the only authorization authority. Every query resolves the caller's
effective scope — the set of (document_id, active_version) pairs they may read —
directly from the policy tables. Retrieval filters on that scope, and results are
revalidated against it before prompt construction. Nothing here is cached, so a
revocation takes effect on the next request.
"""

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from backend.auth.principal import Principal
from backend.db.connection import db_session

# Parameters: $1 tenant_id, $2 user_id, $3 group_ids (int[])
READABLE_PREDICATE = """
d.tenant_id = $1 AND d.deleted_at IS NULL AND (
    d.user_id = $2
    OR d.visibility = 'tenant'
    OR (d.visibility = 'restricted' AND EXISTS (
        SELECT 1 FROM document_acl a
        WHERE a.document_id = d.id AND (
            (a.principal_type = 'user' AND a.principal_id = $2)
            OR (a.principal_type = 'group' AND a.principal_id = ANY($3::int[]))
        )
    ))
)
"""


@dataclass(frozen=True)
class AuthorizedDoc:
    document_id: int
    version: int
    filename: str
    acl_version: int
    project_id: Optional[int]


def version_key(document_id: int, version: int) -> str:
    return f"{document_id}:{version}"


@dataclass(frozen=True)
class AuthorizationScope:
    tenant_id: int
    user_id: int
    docs: Dict[int, AuthorizedDoc]

    @property
    def is_empty(self) -> bool:
        return not self.docs

    def version_keys(self) -> List[str]:
        return sorted(version_key(d.document_id, d.version) for d in self.docs.values())

    def allows(self, tenant_id: int, document_id: int, version: int) -> bool:
        doc = self.docs.get(document_id)
        return tenant_id == self.tenant_id and doc is not None and doc.version == version

    @property
    def fingerprint(self) -> str:
        """Identity of the readable set (documents + active versions). Part of every
        retrieval cache key, so any permission or version change misses the cache."""
        raw = f"{self.tenant_id}|" + ",".join(self.version_keys())
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @property
    def policy_version(self) -> str:
        """Hash of the ACL versions in scope, recorded in audit events."""
        raw = ",".join(f"{d.document_id}:{d.acl_version}" for d in sorted(self.docs.values(), key=lambda d: d.document_id))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def restricted_to(self, document_ids: Iterable[int]) -> "AuthorizationScope":
        wanted = set(document_ids)
        return AuthorizationScope(
            self.tenant_id, self.user_id, {k: v for k, v in self.docs.items() if k in wanted}
        )


async def resolve_scope(principal: Principal, project_id: Optional[int] = None) -> AuthorizationScope:
    """Readable documents with an active (fully indexed) version. Raises on DB failure
    so callers fail closed."""
    query = f"""
        SELECT d.id, d.active_version, d.filename, d.acl_version, d.project_id
        FROM documents d
        WHERE {READABLE_PREDICATE} AND d.active_version IS NOT NULL
    """
    args = [principal.tenant_id, principal.user_id, list(principal.group_ids)]
    if project_id is not None:
        query += " AND d.project_id = $4"
        args.append(project_id)

    async with db_session() as db:
        rows = await db.fetch_all(query, *args)

    docs = {
        r["id"]: AuthorizedDoc(r["id"], r["active_version"], r["filename"], r["acl_version"], r["project_id"])
        for r in rows
    }
    return AuthorizationScope(principal.tenant_id, principal.user_id, docs)


async def get_readable_document(principal: Principal, document_id: int) -> Optional[dict]:
    """The document row if the principal may read it, else None (no existence leak)."""
    async with db_session() as db:
        return await db.fetch_one(
            f"SELECT d.* FROM documents d WHERE d.id = $4 AND {READABLE_PREDICATE}",
            principal.tenant_id, principal.user_id, list(principal.group_ids), document_id,
        )


def can_manage(principal: Principal, document: dict) -> bool:
    """Owners and tenant admins may upload versions, change ACLs and delete."""
    return document["tenant_id"] == principal.tenant_id and (
        document["user_id"] == principal.user_id or principal.is_admin
    )

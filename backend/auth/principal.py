"""The authenticated principal, resolved from the policy store on every request."""

from dataclasses import dataclass, field
from typing import Optional, Tuple

from backend.db.connection import db_session

_PRINCIPAL_QUERY = """
SELECT u.id AS user_id, u.username, u.role, u.is_active,
       t.id AS tenant_id, t.slug AS tenant_slug, t.region, t.allowed_llm_providers,
       ARRAY(SELECT gm.group_id FROM group_members gm
                      JOIN groups g ON g.id = gm.group_id
                      WHERE gm.user_id = u.id AND g.tenant_id = t.id) AS group_ids
FROM users u
JOIN tenants t ON t.id = u.tenant_id
WHERE {predicate}
"""


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    tenant_id: int
    tenant_slug: str
    role: str
    group_ids: Tuple[int, ...] = ()
    region: str = ""
    allowed_llm_providers: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_guest(self) -> bool:
        return self.role == "guest"


def _from_row(row: dict) -> Principal:
    return Principal(
        user_id=row["user_id"],
        username=row["username"],
        tenant_id=row["tenant_id"],
        tenant_slug=row["tenant_slug"],
        role=row["role"],
        group_ids=tuple(sorted(row["group_ids"] or [])),
        region=row["region"],
        allowed_llm_providers=tuple(row["allowed_llm_providers"] or []),
    )


async def load_principal(user_id: int, tenant_id: int) -> Optional[Principal]:
    """Load an active user in the given tenant, or None."""
    async with db_session() as db:
        row = await db.fetch_one(
            _PRINCIPAL_QUERY.format(predicate="u.id = $1 AND u.tenant_id = $2 AND u.is_active"),
            user_id, tenant_id,
        )
    return _from_row(row) if row else None


async def load_principal_by_username(username: str) -> Optional[Principal]:
    async with db_session() as db:
        row = await db.fetch_one(
            _PRINCIPAL_QUERY.format(predicate="u.username = $1 AND u.is_active"), username
        )
    return _from_row(row) if row else None

"""PostgreSQL CRUD operations for the projects table."""

from typing import Optional, List, Dict, Any
from backend.db.connection import get_central_db


async def insert_project(
    slug: str,
    title: str,
    description: str,
    user_id: int,
) -> int:
    """Insert a new project. Returns the project ID."""
    db = await get_central_db()
    try:
        result = await db.execute(
            """INSERT INTO projects (user_id, slug, title, description)
               VALUES ($1, $2, $3, $4)""",
            user_id, slug, title, description,
        )
        return result.lastrowid
    finally:
        await db.close()


# A caller sees their own projects plus projects shared with their tenant (read-only).
_VISIBLE = """
    (p.user_id = $1 OR (p.visibility = 'tenant' AND owner.tenant_id = (SELECT tenant_id FROM users WHERE id = $1)))
"""


def _project_view(r: Dict[str, Any]) -> Dict[str, Any]:
    view = {
        "id": r["id"],
        "slug": r["slug"],
        "title": r["title"],
        "description": r["description"],
        "created_at": str(r["created_at"]),
        "updated_at": str(r["updated_at"]),
        "visibility": r["visibility"],
        "read_only": r["read_only"],
    }
    if "document_count" in r:
        view["document_count"] = r["document_count"]
    return view


async def get_all_projects(user_id: int) -> List[Dict[str, Any]]:
    """Own and tenant-shared projects with document counts (shared first)."""
    db = await get_central_db()
    try:
        rows = await db.fetch_all(
            f"""SELECT p.id, p.slug, p.title, p.description, p.created_at, p.updated_at, p.visibility,
                       (p.user_id <> $1) AS read_only,
                       (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id AND d.deleted_at IS NULL) as document_count
                FROM projects p JOIN users owner ON owner.id = p.user_id
                WHERE {_VISIBLE}
                ORDER BY read_only DESC, p.updated_at DESC""",
            user_id,
        )
        return [_project_view(r) for r in rows]
    finally:
        await db.close()


async def get_project_by_slug(slug: str, user_id: int) -> Optional[Dict[str, Any]]:
    """A project by slug: the caller's own first, else one shared with their tenant."""
    db = await get_central_db()
    try:
        r = await db.fetch_one(
            f"""SELECT p.id, p.slug, p.title, p.description, p.created_at, p.updated_at, p.visibility,
                       (p.user_id <> $1) AS read_only
                FROM projects p JOIN users owner ON owner.id = p.user_id
                WHERE p.slug = $2 AND {_VISIBLE}
                ORDER BY (p.user_id = $1) DESC
                LIMIT 1""",
            user_id, slug,
        )
        return _project_view(r) if r else None
    finally:
        await db.close()


async def get_project_id_by_slug(slug: str, user_id: int) -> Optional[int]:
    """Project id for a slug the caller can see (own or tenant-shared)."""
    project = await get_project_by_slug(slug, user_id)
    return project["id"] if project else None


async def update_project(
    slug: str, title: str, description: str, user_id: int
) -> bool:
    """Update a project's title and description. Returns True if updated."""
    db = await get_central_db()
    try:
        result = await db.execute(
            """UPDATE projects
               SET title = $1, description = $2, updated_at = NOW()
               WHERE slug = $3 AND user_id = $4""",
            title, description, slug, user_id,
        )
        return result.rowcount > 0
    finally:
        await db.close()


async def delete_project(slug: str, user_id: int) -> Optional[int]:
    """Delete a project by slug. Returns the project ID if deleted, None otherwise."""
    db = await get_central_db()
    try:
        row = await db.fetch_one(
            "SELECT id FROM projects WHERE slug = $1 AND user_id = $2", slug, user_id
        )
        if not row:
            return None
        project_id = row["id"]

        # Unlink documents. Rows stay as tombstones so async cleanup can still find
        # their chunks and vectors; documents owned by others simply leave the project.
        await db.execute("UPDATE documents SET project_id = NULL WHERE project_id = $1", project_id)
        # Delete the project
        await db.execute("DELETE FROM projects WHERE id = $1 AND user_id = $2", project_id, user_id)
        return project_id
    finally:
        await db.close()


async def slug_exists(slug: str, user_id: int) -> bool:
    """Check if a project slug already exists for this user."""
    db = await get_central_db()
    try:
        row = await db.fetch_one(
            "SELECT 1 FROM projects WHERE slug = $1 AND user_id = $2", slug, user_id
        )
        return row is not None
    finally:
        await db.close()

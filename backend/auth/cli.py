"""Tenant / user / group administration and token issuance.

    python -m backend.auth.cli create-tenant --slug acme --name "Acme Corp" [--region us-east-1]
    python -m backend.auth.cli create-user --tenant acme --username alice [--role admin]
    python -m backend.auth.cli create-group --tenant acme --name hr
    python -m backend.auth.cli add-member --tenant acme --group hr --username alice
    python -m backend.auth.cli issue-token --username alice [--minutes 60]
"""

import argparse
import asyncio
import sys

from backend.auth.database import init_db
from backend.auth.tokens import issue_token
from backend.db.connection import close_pools, db_session


async def _tenant_id(db, slug: str) -> int:
    tenant_id = await db.fetch_val("SELECT id FROM tenants WHERE slug = $1", slug)
    if tenant_id is None:
        sys.exit(f"Unknown tenant '{slug}'")
    return tenant_id


async def run(args: argparse.Namespace) -> None:
    await init_db()
    async with db_session() as db:
        if args.command == "create-tenant":
            tenant_id = await db.fetch_val(
                "INSERT INTO tenants (slug, name, region) VALUES ($1, $2, $3) RETURNING id",
                args.slug, args.name, args.region,
            )
            print(f"tenant_id={tenant_id}")

        elif args.command == "create-user":
            tenant_id = await _tenant_id(db, args.tenant)
            user_id = await db.fetch_val(
                "INSERT INTO users (username, hashed_password, tenant_id, role) VALUES ($1, '', $2, $3) RETURNING id",
                args.username, tenant_id, args.role,
            )
            print(f"user_id={user_id}")

        elif args.command == "create-group":
            tenant_id = await _tenant_id(db, args.tenant)
            group_id = await db.fetch_val(
                "INSERT INTO groups (tenant_id, name) VALUES ($1, $2) RETURNING id", tenant_id, args.name
            )
            print(f"group_id={group_id}")

        elif args.command == "add-member":
            tenant_id = await _tenant_id(db, args.tenant)
            group_id = await db.fetch_val("SELECT id FROM groups WHERE tenant_id = $1 AND name = $2", tenant_id, args.group)
            user_id = await db.fetch_val(
                "SELECT id FROM users WHERE tenant_id = $1 AND username = $2", tenant_id, args.username
            )
            if group_id is None or user_id is None:
                sys.exit("Group and user must both exist in the tenant")
            await db.run(
                "INSERT INTO group_members (group_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                group_id, user_id,
            )
            print(f"added user {user_id} to group {group_id}")

        elif args.command == "issue-token":
            row = await db.fetch_one("SELECT id, tenant_id FROM users WHERE username = $1 AND is_active", args.username)
            if not row:
                sys.exit(f"Unknown or inactive user '{args.username}'")
            print(issue_token(row["id"], row["tenant_id"], args.minutes))
    await close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tenant and access administration")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-tenant")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--region", default="us-east-1")

    p = sub.add_parser("create-user")
    p.add_argument("--tenant", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--role", choices=["member", "admin"], default="member")

    p = sub.add_parser("create-group")
    p.add_argument("--tenant", required=True)
    p.add_argument("--name", required=True)

    p = sub.add_parser("add-member")
    p.add_argument("--tenant", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--username", required=True)

    p = sub.add_parser("issue-token")
    p.add_argument("--username", required=True)
    p.add_argument("--minutes", type=int, default=1440)

    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

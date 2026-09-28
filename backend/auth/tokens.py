"""HS256 access tokens. Tokens identify the caller only; tenant membership, role
and group membership are always re-read from PostgreSQL.

The signing key is JWT_SECRET when set; otherwise a random key is generated once
and stored in PostgreSQL (app_secrets) so every instance signs and verifies with
the same key without extra configuration.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt

from backend.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES
from backend.config import JWT_SECRET as _ENV_SECRET

JWT_SECRET = _ENV_SECRET  # replaced by load_signing_secret() when not configured


class TokenError(Exception):
    pass


async def load_signing_secret() -> None:
    """Called at startup (after the schema exists)."""
    global JWT_SECRET
    if JWT_SECRET:
        return
    from backend.db.connection import db_session

    async with db_session() as db:
        await db.run(
            "INSERT INTO app_secrets (name, value) VALUES ('jwt_signing_key', $1) ON CONFLICT (name) DO NOTHING",
            secrets.token_hex(32),
        )
        JWT_SECRET = await db.fetch_val("SELECT value FROM app_secrets WHERE name = 'jwt_signing_key'")


def issue_token(user_id: int, tenant_id: int, expires_minutes: int = JWT_EXPIRE_MINUTES) -> str:
    if not JWT_SECRET:
        raise TokenError("Token signing key is not loaded")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> tuple[int, int]:
    """Return (user_id, tenant_id) from a valid token."""
    if not JWT_SECRET:
        raise TokenError("Token signing key is not loaded")
    try:
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"require": ["sub", "tid", "exp"]}
        )
        return int(payload["sub"]), int(payload["tid"])
    except (jwt.PyJWTError, ValueError) as e:
        raise TokenError(str(e)) from e

"""FastAPI dependency that resolves the authenticated principal.

Fails closed: if the policy store cannot be read, the request is rejected.
"""

import logging

from fastapi import HTTPException, Request

from backend.auth.principal import Principal, load_principal, load_principal_by_username
from backend.auth.tokens import TokenError, decode_token
from backend.config import AUTH_MODE, DEMO_USERNAME, DEPLOYMENT_REGION

logger = logging.getLogger(__name__)


async def get_current_principal(request: Request) -> Principal:
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""

    try:
        if token:
            try:
                user_id, tenant_id = decode_token(token)
            except TokenError:
                raise HTTPException(status_code=401, detail="Invalid or expired access token")
            principal = await load_principal(user_id, tenant_id)
        elif AUTH_MODE == "demo":
            principal = await load_principal_by_username(DEMO_USERNAME)
        else:
            raise HTTPException(status_code=401, detail="Missing bearer token")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Policy store unavailable while resolving principal")
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    if principal is None:
        raise HTTPException(status_code=401, detail="Unknown or inactive user")

    if principal.region != DEPLOYMENT_REGION:
        raise HTTPException(
            status_code=403,
            detail=f"Tenant data is homed in region '{principal.region}' and cannot be served from '{DEPLOYMENT_REGION}'",
        )

    request.state.principal = principal
    return principal

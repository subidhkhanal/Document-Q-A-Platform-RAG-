"""Session endpoints: anonymous guest sessions for the public demo, and `me`."""

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth import Principal, get_current_principal
from backend.auth.tokens import issue_token
from backend.common import rate_limit
from backend.config import (
    AUTH_MODE, GUEST_MAX_DOCUMENTS, GUEST_MAX_UPLOAD_MB, GUEST_TTL_HOURS, MAX_UPLOAD_SIZE_MB,
    RATE_LIMIT_GUESTS_PER_IP_PER_HOUR,
)
from backend.demo.guests import create_guest

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/guest")
async def create_guest_session(request: Request):
    """Demo mode only: a fresh private guest account and a token valid for its lifetime."""
    if AUTH_MODE != "demo":
        raise HTTPException(status_code=404, detail="Not found")
    rate_limit.check("guest", rate_limit.client_ip(request), RATE_LIMIT_GUESTS_PER_IP_PER_HOUR, 3600)
    guest = await create_guest()
    return {
        "access_token": issue_token(guest["user_id"], guest["tenant_id"], expires_minutes=GUEST_TTL_HOURS * 60),
        "token_type": "bearer",
        "expires_in": GUEST_TTL_HOURS * 3600,
        "username": guest["username"],
    }


@router.get("/me")
async def me(principal: Principal = Depends(get_current_principal)):
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "tenant": principal.tenant_slug,
        "is_guest": principal.is_guest,
        "limits": {
            "max_upload_mb": GUEST_MAX_UPLOAD_MB if principal.is_guest else MAX_UPLOAD_SIZE_MB,
            "max_documents": GUEST_MAX_DOCUMENTS if principal.is_guest else None,
            "data_expires_hours": GUEST_TTL_HOURS if principal.is_guest else None,
        },
    }

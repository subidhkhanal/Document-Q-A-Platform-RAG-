"""Security-relevant audit events: who retrieved or changed what, under which
policy and model versions. Identifiers only — never source text."""

import logging
from typing import Any, Dict, Optional

from backend.common.request_context import current_request_id
from backend.db.connection import db_session

logger = logging.getLogger(__name__)


async def record_event(
    action: str,
    *,
    tenant_id: Optional[int],
    user_id: Optional[int],
    resource_type: Optional[str] = None,
    resource_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> None:
    """Best-effort write; audit failures are logged, never raised to the caller."""
    try:
        async with db_session() as db:
            await db.run(
                """INSERT INTO audit_events
                   (tenant_id, user_id, request_id, action, resource_type, resource_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                tenant_id, user_id, request_id or current_request_id(), action,
                resource_type, str(resource_id) if resource_id is not None else None, details or {},
            )
    except Exception:
        logger.exception("Failed to write audit event %s", action)

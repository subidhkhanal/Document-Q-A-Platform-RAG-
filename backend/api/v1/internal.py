"""Scheduled housekeeping, called by the platform cron (Vercel sends
`Authorization: Bearer $CRON_SECRET`). Also useful right after a deploy to ingest the
Sample Library without waiting for the next cron run."""

import hmac

from fastapi import APIRouter, HTTPException, Request

from backend.config import AUTH_MODE, CRON_SECRET, SEED_DEMO_LIBRARY
from backend.demo.guests import cleanup_expired_guests
from backend.demo.library import seed_library
from backend.ingestion.worker import drive_queue

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


@router.get("/housekeeping")
async def housekeeping(request: Request):
    supplied = request.headers.get("authorization", "")
    if not CRON_SECRET or not hmac.compare_digest(supplied, f"Bearer {CRON_SECRET}"):
        raise HTTPException(status_code=404, detail="Not found")

    report = {}
    if AUTH_MODE == "demo":
        report["expired_guests"] = await cleanup_expired_guests()
        if SEED_DEMO_LIBRARY:
            report["library_jobs_enqueued"] = await seed_library()
    # Drain pending ingestion/cleanup jobs within the function's time limit.
    report["jobs_processed"] = await drive_queue(max_jobs=50, time_budget=240.0)
    return report

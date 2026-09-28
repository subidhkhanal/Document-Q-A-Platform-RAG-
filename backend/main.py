import asyncio
import logging
import os
import time

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.legacy import router as legacy_router
from backend.api.v1 import router as v1_router
from backend.auth import Principal, get_current_principal
from backend.auth.database import get_db, init_db
from backend.common.request_context import RequestIdMiddleware
from backend.config import (
    AUTH_MODE, DEMO_TENANT_SLUG, DEMO_USERNAME, LANGSMITH_API_KEY, LANGSMITH_PROJECT, LANGSMITH_TRACING,
    OBJECT_STORE, RUN_INGESTION_WORKER, SEED_DEMO_LIBRARY,
)
from backend.conversations import ConversationService
from backend.db.connection import close_pools
from backend.demo.guests import cleanup_expired_guests
from backend.demo.library import seed_library
from backend.ingestion.parsers import SUPPORTED_TYPES
from backend.ingestion.worker import worker
from backend.projects.routes import router as projects_router
from backend.projects.database import insert_project as _create_default_project

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Document Q&A API",
    description="Multi-tenant RAG: ACL-enforced hybrid retrieval with cited, grounded answers",
    version="2.0.0",
)

# CORS for frontend
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
_cors_origins = list(dict.fromkeys([FRONTEND_URL, "http://localhost:3000"]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.(vercel\.app|up\.railway\.app|awsapprunner\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)

app.include_router(v1_router)
app.include_router(projects_router)
app.include_router(legacy_router)


async def _seed_demo_user() -> None:
    """Demo mode fallback identity for requests without a token (older frontends).
    It is a guest: it gets guest limits and no admin rights."""
    db = await get_db()
    try:
        tenant_id = await db.fetch_val("SELECT id FROM tenants WHERE slug = $1", DEMO_TENANT_SLUG)
        existing = await db.fetch_one("SELECT id FROM users WHERE username = $1", DEMO_USERNAME)
        if existing:
            await db.run("UPDATE users SET role = 'guest' WHERE id = $1 AND role <> 'guest'", existing["id"])
            return
        user_id = await db.fetch_val(
            """INSERT INTO users (username, hashed_password, tenant_id, role)
               VALUES ($1, '', $2, 'guest') ON CONFLICT (username) DO NOTHING RETURNING id""",
            DEMO_USERNAME, tenant_id,
        )
    finally:
        await db.close()
    if user_id:
        await _create_default_project(
            slug="uncategorized", title="Uncategorized",
            description="Default project for unsorted documents", user_id=user_id,
        )


@app.on_event("startup")
async def startup():
    if LANGSMITH_TRACING and LANGSMITH_API_KEY:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = LANGSMITH_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT
        os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

    await init_db()
    logger.info("Object store: %s", OBJECT_STORE)
    if AUTH_MODE == "demo":
        logger.warning("AUTH_MODE=demo: public demo with guest sessions. Use AUTH_MODE=jwt for private deployments.")
        await _seed_demo_user()
        if SEED_DEMO_LIBRARY:
            try:
                await seed_library()
            except Exception:
                logger.exception("Sample library seeding failed")
    if RUN_INGESTION_WORKER:
        worker.start()
        if AUTH_MODE == "demo":
            worker.add_periodic(cleanup_expired_guests, interval_seconds=3600)
    app.state.warmup_task = asyncio.create_task(_warm_up_clients())  # keep a reference


async def _warm_up_clients() -> None:
    """Create external clients in the background so the first query doesn't pay
    connection setup inside its retrieval deadline."""
    from backend.components import get_llm, get_reranker, get_vector_store

    for factory in (get_vector_store, get_reranker, get_llm):
        try:
            await asyncio.to_thread(factory)
        except Exception as e:
            logger.warning("Warm-up of %s failed: %s", factory.__name__, e)


@app.on_event("shutdown")
async def shutdown():
    await worker.stop()
    await close_pools()


# Health check endpoint (responds immediately, no heavy loading, no auth)
@app.get("/health")
async def health_check():
    return JSONResponse(
        content={"status": "healthy", "timestamp": time.time()},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/")
async def root():
    return {
        "message": "Document Q&A API",
        "version": app.version,
        "supported_formats": list(SUPPORTED_TYPES),
        "endpoints": {
            "ingest": "POST /api/v1/documents (multipart, Idempotency-Key header) -> 202 + job_id",
            "job_status": "GET /api/v1/ingestion/jobs/{job_id}",
            "documents": "GET /api/v1/documents",
            "acl": "GET|PUT /api/v1/documents/{id}/acl",
            "download": "GET /api/v1/documents/{id}/file",
            "query": "POST /api/v1/qa/query (SSE)",
            "metrics": "GET /api/v1/metrics (admin)",
        },
    }


@app.post("/api/conversations")
async def create_conversation(principal: Principal = Depends(get_current_principal)):
    conv_id = await ConversationService.create_conversation(principal.user_id)
    return {"conversation_id": conv_id}


@app.get("/api/conversations")
async def list_conversations(principal: Principal = Depends(get_current_principal)):
    return await ConversationService.get_conversations(principal.user_id)


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: int, principal: Principal = Depends(get_current_principal)):
    if not await ConversationService.verify_ownership(conv_id, principal.user_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await ConversationService.get_messages(conv_id, user_id=principal.user_id)


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int, principal: Principal = Depends(get_current_principal)):
    deleted = await ConversationService.delete_conversation(conv_id, principal.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"message": "Conversation deleted"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

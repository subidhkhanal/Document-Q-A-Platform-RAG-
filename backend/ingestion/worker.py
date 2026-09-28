"""Asynchronous ingestion worker over a durable PostgreSQL job queue.

Jobs are claimed with FOR UPDATE SKIP LOCKED under a lease, so any number of
worker processes can run and a crashed worker's job is picked up again after the
lease expires. Transient failures (embedding rate limits, vector DB errors) retry
with exponential backoff + jitter up to INGESTION_MAX_ATTEMPTS; permanent failures
(malformed or malicious uploads) quarantine the version as FAILED. The document's
active version pointer only moves after every derived index is written.

Run standalone:  python -m backend.ingestion.worker
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from backend.audit.log import record_event
from backend.common.metrics import metrics
from backend.common.retry import backoff_delay, retry_sync
from backend.components import get_chunker, get_vector_store
from backend.config import (
    EMBEDDING_MODEL_VERSION, INGESTION_LEASE_SECONDS, INGESTION_MAX_ATTEMPTS,
    INGESTION_POLL_SECONDS, INGESTION_WORKER_CONCURRENCY, RUN_INGESTION_WORKER,
)
from backend.db.connection import db_session
from backend.documents import repository as repo
from backend.ingestion.parsers import UnsupportedDocument, detect_type
from backend.ingestion.pipeline import build_chunks, sha256_hex
from backend.ingestion.sandbox import ParserError, parse_sandboxed
from backend.storage.object_store import get_object_store
from backend.storage.vector_store import build_chunk_metadata

logger = logging.getLogger(__name__)

PERMANENT_ERRORS = (ParserError, UnsupportedDocument)


class PermanentJobError(Exception):
    pass


# ---------------------------------------------------------------------------
# Queue primitives
# ---------------------------------------------------------------------------

async def claim_job() -> Optional[Dict[str, Any]]:
    async with db_session() as db:
        return await db.fetch_one(
            """UPDATE ingestion_jobs
               SET locked_at = NOW(), attempts = attempts + 1, updated_at = NOW()
               WHERE id = (
                   SELECT id FROM ingestion_jobs
                   WHERE status = 'PROCESSING' AND available_at <= NOW()
                     AND (locked_at IS NULL OR locked_at < NOW() - make_interval(secs => $1))
                   ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1)
               RETURNING *""",
            INGESTION_LEASE_SECONDS,
        )


async def _set_stage(job_id: str, stage: str) -> None:
    """Record progress and renew the lease."""
    async with db_session() as db:
        await db.run(
            "UPDATE ingestion_jobs SET stage = $2, locked_at = NOW(), updated_at = NOW() WHERE id = $1",
            job_id, stage,
        )


async def _complete(job_id: str, indexed_chunks: int = 0) -> None:
    async with db_session() as db:
        await db.run(
            """UPDATE ingestion_jobs SET status = 'READY', stage = 'done', locked_at = NULL,
                   indexed_chunks = $2, error = NULL, updated_at = NOW() WHERE id = $1""",
            job_id, indexed_chunks,
        )


async def _fail(job: Dict[str, Any], error: str, permanent: bool) -> None:
    async with db_session() as db:
        async with db.transaction():
            if not permanent and job["attempts"] < INGESTION_MAX_ATTEMPTS:
                delay = max(5.0, backoff_delay(job["attempts"], base=10, cap=300))
                await db.run(
                    """UPDATE ingestion_jobs SET stage = 'retrying', locked_at = NULL, error = $2,
                           available_at = NOW() + make_interval(secs => $3), updated_at = NOW() WHERE id = $1""",
                    job["id"], error[:2000], delay,
                )
                return
            await db.run(
                """UPDATE ingestion_jobs SET status = 'FAILED', stage = 'failed', locked_at = NULL,
                       error = $2, updated_at = NOW() WHERE id = $1""",
                job["id"], error[:2000],
            )
            if job["kind"] == "ingest":
                # The prior active version (if any) is untouched and keeps serving queries.
                await db.run(
                    "UPDATE document_versions SET status = 'failed', error = $3 WHERE document_id = $1 AND version = $2",
                    job["document_id"], job["document_version"], error[:2000],
                )
    metrics.incr(f"ingestion.{job['kind']}.failed")


# ---------------------------------------------------------------------------
# Job handlers
# ---------------------------------------------------------------------------

async def _ingest(job: Dict[str, Any]) -> int:
    doc_id, version, tenant_id = job["document_id"], job["document_version"], job["tenant_id"]
    ver = await repo.get_version(doc_id, version)
    if ver is None:
        raise PermanentJobError("Document version not found")
    if ver["status"] in ("ready", "superseded"):
        return ver["chunk_count"]  # already done (duplicate delivery)
    if ver["status"] == "deleted" or not ver["object_key"]:
        raise PermanentJobError("Document was deleted")

    await _set_stage(job["id"], "parsing")
    data = await get_object_store().get(ver["object_key"])
    if "sha256:" + sha256_hex(data) != ver["content_hash"]:
        raise PermanentJobError("Stored object failed integrity check")
    kind, _, _ = detect_type(ver["filename"], data)
    parsed = await asyncio.to_thread(parse_sandboxed, kind, data, ver["filename"])
    if not parsed["sections"]:
        raise PermanentJobError("No extractable text found (empty, image-only, or password-protected file)")

    await _set_stage(job["id"], "chunking")
    chunker = await asyncio.to_thread(get_chunker)  # tokenizer load/token counting is CPU-bound
    chunks = await asyncio.to_thread(
        build_chunks, parsed["sections"], chunker,
        tenant_id=tenant_id, document_id=doc_id, version=version,
        embedding_model_version=EMBEDDING_MODEL_VERSION,
    )
    if not chunks:
        raise PermanentJobError("Document text is too short to index")

    await _set_stage(job["id"], "embedding")
    vs = await asyncio.to_thread(get_vector_store)
    vectors = await retry_sync(
        lambda: vs.embed_documents([c["text"] for c in chunks]),
        attempts=4, base_delay=2, max_delay=30, label="embed_documents",
    )

    await _set_stage(job["id"], "indexing")
    await repo.replace_chunks(tenant_id, doc_id, version, chunks)
    payload = [
        {"id": c["chunk_id"], "values": v, "metadata": build_chunk_metadata(c)}
        for c, v in zip(chunks, vectors)
    ]
    await retry_sync(lambda: vs.upsert(tenant_id, payload), attempts=4, base_delay=1, label="pinecone_upsert")

    await _set_stage(job["id"], "activating")
    await _activate(job, len(chunks), parsed)
    return len(chunks)


async def _activate(job: Dict[str, Any], chunk_count: int, parsed: Dict[str, Any]) -> None:
    """Flip the active-version pointer only now that every derived index is written.
    Older versions are superseded and their derived data cleaned up asynchronously."""
    doc_id, version = job["document_id"], job["document_version"]
    deleted = False
    async with db_session() as db:
        async with db.transaction():
            doc = await db.fetch_one("SELECT * FROM documents WHERE id = $1 FOR UPDATE", doc_id)
            if doc is None or doc["deleted_at"] is not None:
                # The delete_document job purges every version's chunks and vectors.
                await repo.enqueue_job(db, "cleanup_version", job["tenant_id"], job["user_id"], doc_id, version)
                deleted = True
            else:
                await _mark_ready_and_switch(db, job, doc, chunk_count, parsed)
    if deleted:
        raise PermanentJobError("Document was deleted during ingestion")

    await record_event(
        "document.version_activated", tenant_id=job["tenant_id"], user_id=job["user_id"],
        resource_type="document", resource_id=doc_id,
        details={"version": version, "chunks": chunk_count, "embedding_model_version": EMBEDDING_MODEL_VERSION},
        request_id=job["id"],
    )


async def _mark_ready_and_switch(db, job: Dict[str, Any], doc: Dict[str, Any], chunk_count: int,
                                 parsed: Dict[str, Any]) -> None:
    doc_id, version = job["document_id"], job["document_version"]
    await db.run(
        """UPDATE document_versions
           SET status = 'ready', page_count = $3, chunk_count = $4, embedding_model_version = $5,
               warnings = $6, error = NULL, ready_at = NOW()
           WHERE document_id = $1 AND version = $2""",
        doc_id, version, parsed.get("page_count"), chunk_count, EMBEDDING_MODEL_VERSION,
        parsed.get("warnings") or [],
    )
    previous = doc["active_version"]
    if previous is None or version > previous:
        await db.run("UPDATE documents SET active_version = $2, updated_at = NOW() WHERE id = $1", doc_id, version)
        stale = previous
    else:
        stale = version  # a newer version was activated first; this one is already stale
    if stale is not None:
        await db.run(
            "UPDATE document_versions SET status = 'superseded' WHERE document_id = $1 AND version = $2",
            doc_id, stale,
        )
        await repo.enqueue_job(db, "cleanup_version", job["tenant_id"], job["user_id"], doc_id, stale)


async def _cleanup_version(job: Dict[str, Any]) -> int:
    """Remove derived data (vectors + chunks) of a superseded version. The raw object
    is kept: source versions are immutable records."""
    ids = await repo.chunk_ids_for(job["document_id"], job["document_version"])
    if ids:
        vs = await asyncio.to_thread(get_vector_store)
        await retry_sync(lambda: vs.delete_ids(job["tenant_id"], ids), attempts=4, label="pinecone_delete")
    await repo.delete_chunks(job["document_id"], job["document_version"])
    return len(ids)


async def _delete_document(job: Dict[str, Any]) -> int:
    """Access was revoked synchronously (deleted_at); purge derived data and objects."""
    doc_id = job["document_id"]
    ids = await repo.chunk_ids_for(doc_id)
    if ids:
        vs = await asyncio.to_thread(get_vector_store)
        await retry_sync(lambda: vs.delete_ids(job["tenant_id"], ids), attempts=4, label="pinecone_delete")
    await repo.delete_chunks(doc_id)

    async with db_session() as db:
        versions = await db.fetch_all(
            "SELECT version, object_key FROM document_versions WHERE document_id = $1 AND object_key IS NOT NULL",
            doc_id,
        )
    store = get_object_store()
    for v in versions:
        await store.delete(v["object_key"])
    async with db_session() as db:
        await db.run("UPDATE document_versions SET object_key = NULL, status = 'deleted' WHERE document_id = $1", doc_id)
    return len(ids)


HANDLERS = {"ingest": _ingest, "cleanup_version": _cleanup_version, "delete_document": _delete_document}


async def process_job(job: Dict[str, Any]) -> None:
    handler = HANDLERS.get(job["kind"])
    try:
        if handler is None:
            raise PermanentJobError(f"Unknown job kind {job['kind']}")
        count = await handler(job)
        await _complete(job["id"], count)
        metrics.incr(f"ingestion.{job['kind']}.completed")
    except (PermanentJobError, *PERMANENT_ERRORS) as e:
        logger.warning("Job %s (%s) failed permanently: %s", job["id"], job["kind"], e)
        await _fail(job, str(e), permanent=True)
    except Exception as e:  # transient: provider outage, rate limit, network
        logger.exception("Job %s (%s) failed (attempt %d)", job["id"], job["kind"], job["attempts"])
        await _fail(job, f"{type(e).__name__}: {e}", permanent=False)


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

class IngestionWorker:
    def __init__(self, concurrency: int = INGESTION_WORKER_CONCURRENCY):
        self.concurrency = max(1, concurrency)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    def notify(self) -> None:
        """Wake idle loops immediately after a local enqueue."""
        self._wake.set()

    async def _loop(self, n: int) -> None:
        while self._running:
            try:
                job = await claim_job()
            except Exception:
                logger.exception("Worker %d could not claim a job", n)
                job = None
            if job is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=INGESTION_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            try:
                await process_job(job)
            except Exception:
                # e.g. DB outage while recording the outcome; the lease expires and
                # the job is re-claimed later. Keep the loop alive.
                logger.exception("Worker %d failed while finalizing job %s", n, job["id"])
                await asyncio.sleep(INGESTION_POLL_SECONDS)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [asyncio.create_task(self._loop(i)) for i in range(self.concurrency)]
        logger.info("Ingestion worker started with %d loops", self.concurrency)

    def add_periodic(self, fn, interval_seconds: float) -> None:
        """Run `fn` (async, no args) every interval while the worker runs, e.g. housekeeping.
        Safe with several instances: the work it enqueues goes through the job queue."""

        async def runner():
            while self._running:
                try:
                    await fn()
                except Exception:
                    logger.exception("Periodic task %s failed", getattr(fn, "__name__", fn))
                await asyncio.sleep(interval_seconds)

        self._tasks.append(asyncio.create_task(runner()))

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []


worker = IngestionWorker()


async def drive_queue(max_jobs: int = 3, time_budget: float = 45.0, stop_when_done: Optional[str] = None) -> int:
    """Process queued jobs inside the current request (serverless mode, where no
    background loop exists). Leases make this safe to run from many concurrent
    requests. Returns the number of jobs processed."""
    deadline = time.monotonic() + time_budget
    processed = 0
    while processed < max_jobs and time.monotonic() < deadline:
        job = await claim_job()
        if job is None:
            break
        await process_job(job)
        processed += 1
        if stop_when_done and job["id"] == stop_when_done:
            break
    return processed


def kick(background_tasks=None) -> None:
    """Make sure newly enqueued work gets processed: wake the in-process worker, or
    (serverless) process it in a background task after the response is sent."""
    if RUN_INGESTION_WORKER:
        worker.notify()
    elif background_tasks is not None:
        background_tasks.add_task(drive_queue)


async def _main() -> None:
    from backend.auth.database import init_db

    logging.basicConfig(level=logging.INFO)
    await init_db()
    worker.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())

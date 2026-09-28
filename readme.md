# Document Q&A Platform (RAG)

[![CI](https://github.com/subidhkhanal/knowledge-base/actions/workflows/ci.yml/badge.svg)](https://github.com/subidhkhanal/knowledge-base/actions/workflows/ci.yml)

A multi-tenant document Q&A system: upload PDFs, Word documents, text/Markdown or EPUBs and ask questions answered **only** from documents you are authorized to read, with auditable citations (document, version, page, chunk).

**[Live Demo](https://personal-assistant-indol-omega.vercel.app)** | **[API](https://d3kmysbupw.us-east-2.awsapprunner.com/health)**

**Try it in 10 seconds:** open the demo, no signup needed. Each visitor gets a private guest session, a shared read-only *Sample Library* (a fictional company's policies in PDF, DOCX, Markdown and text), and suggested questions. Uploads are private to the visitor and deleted after 24 hours.

## Results

Measured with `backend/evaluation/eval_runner.py` on the golden set in `backend/evaluation/golden_set.json`: **35 questions over an 11-document corpus**, made up of the 6 sample-library documents plus 5 adversarial documents that contain prompt injections. Full per-question output is in [`backend/evaluation/results.json`](backend/evaluation/results.json).

| Metric | Result | What it measures |
|---|---|---|
| Retrieval Recall@5 | 29/29 (100%) | Gold passage among the 5 reranked passages given to the model |
| Hybrid recall stage (before rerank, k=50) | 29/29 (100%) | Gold passage anywhere in the dense + keyword candidates |
| MRR / nDCG@5 | 1.00 / 1.00 | Gold passage ranked first |
| Answer accuracy | 29/29 (100%) | Answer contains every required fact (including table cells and part numbers like `NW-4471`) |
| Citation accuracy | 29/29 (100%) | A cited passage contains the gold evidence |
| Citation validity | 35/35 (100%) | No citation marker pointing outside the authorized evidence set |
| Unanswerable questions answered "I don't know" | 6/6 (100%) | Abstains instead of answering from model knowledge |
| False abstentions | 0/29 | Answerable questions wrongly refused |
| Prompt-injection cases passed | 5/5 (100%) on the final prompt; 4/5 on the first run | Answer contains none of the canaries planted by instructions hidden in documents |
| LLM-judged faithfulness | 1.00 | Share of answer claims supported by the cited evidence (judge: `gpt-oss-120b`, same family as the generator) |

**Read these numbers with their limits.** The corpus is small and synthetic, and the questions were written by the same person who built the system, so perfect retrieval here does not mean perfect retrieval at scale. The injection result is *mitigation* measured on five hand-written attacks. The system prompt was hardened once after the first run leaked a canary (the model quoted an injected instruction while "reporting a conflict"), so the 5/5 is measured on the same set that informed the fix. No RAG system fully prevents prompt injection. The judge shares a model family with the generator. CI re-runs this set and fails if Recall@5 or citation accuracy drops, or if injection resistance or abstention regresses.

### Latency breakdown

Per-stage timings come from the `timings` field of every answer. The numbers below were measured from a laptop in India against services in `us-east-1`, so network distance dominates. The deployed backend runs in `us-east-2` next to them, and its numbers will be lower.

| Stage (p50 / p95 ms) | Local run | Notes |
|---|---|---|
| Authorization scope (Postgres) | 2 / 4 | Readable document versions, resolved per request |
| Query embedding (Cohere) | 570 / 1,463 | Started in parallel with authorization |
| Dense ANN query (Pinecone) | 1,511 / 1,927 | Runs concurrently with keyword search |
| Keyword search (Postgres FTS) | 1 / 3 | |
| Rerank (Cohere cross-encoder, 20 candidates) | 618 / 1,520 | |
| LLM time to first token (Groq `gpt-oss-120b`, low reasoning effort) | 801 / 1,929 | Default reasoning effort measured **~20 s** TTFT, because hidden reasoning tokens precede the first visible token |
| **Time to first token, end to end** | **3,796 / 7,443** | |

What changed along the way: the retired Llama model was replaced; reasoning effort was set to low (20 s → 0.7 s TTFT); a pre-retrieval LLM routing call was removed from the hot path (−1 s); the query embedding now overlaps authorization; the reranker gets at most 20 candidates.

---

## Architecture

Two independent paths: an **offline ingestion pipeline** and an **online query path**. PostgreSQL is the source of truth for tenancy, permissions, document versions and chunk text; the vector index is rebuildable derived state.

```
                 OFFLINE INGESTION                                   ONLINE QUERY
POST /api/v1/documents (Idempotency-Key)            POST /api/v1/qa/query (SSE)
  │ validate type (magic bytes) + size                │ authenticate → principal (tenant, role, groups)
  │ store raw bytes (S3 / Postgres), sha256           │ resolve authorization scope from PostgreSQL  ── fail closed
  │ document + immutable version + job (1 tx)         │   = readable documents × active versions
  ▼ 202 {job_id}                                      ▼
Postgres job queue (SKIP LOCKED, leases, retries)   ┌─ Dense ANN: Pinecone, tenant namespace,
  │ sandboxed parser (subprocess, timeout, mem cap) │   filter {tenant, doc:version ∈ scope, embed model}
  │   PDF (tables → Markdown, per page) · DOCX      ├─ Keyword: Postgres full-text, same scope
  │   TXT/MD (heading sections) · EPUB              └─ RRF merge → hydrate + REVALIDATE vs scope
  │ chunk 512 tok / 75 overlap, per section/page       → Cohere cross-encoder rerank → top 5
  │ embed (Cohere, batched, backoff+jitter)            → abstain if evidence insufficient
  │ write chunks (Postgres) + vectors (Pinecone)       → prompt: delimited, untrusted evidence
  ▼ flip active_version ONLY after all indexes ready   → stream tokens (Groq), bounded retries
  superseded version → async cleanup job               → validate every [S#] citation vs authorized set
                                                       → audit event (ids only, no text)
```

| Layer | Technology |
|-------|-----------|
| API | FastAPI, SSE streaming, request-id middleware |
| Auth | HS256 bearer tokens (PyJWT); tenant/role/groups re-read from Postgres per request |
| Metadata & policy store | PostgreSQL (tenants, users, groups, documents, versions, ACLs, chunks, jobs, audit) |
| Raw documents | S3 (SSE-AES256) when `S3_BUCKET` is set, otherwise a Postgres `bytea` table (durable, zero setup) |
| Dense index | Pinecone serverless, one namespace per tenant |
| Keyword index | PostgreSQL `tsvector` + GIN (persistent, ACL-filtered in SQL) |
| Embeddings / rerank | Cohere `embed-english-v3.0` / `rerank-english-v3.0` |
| LLM | Groq `openai/gpt-oss-120b`, low reasoning effort |
| Frontend | Next.js 16, React 19, Tailwind v4 |

---

## Requirements traceability

| Requirement | Implementation |
|---|---|
| Upload PDF, Word, text | `backend/ingestion/parsers.py` — pdfplumber (tables → Markdown, page provenance), python-docx (headings + tables), TXT/Markdown (heading sections), EPUB |
| Async ingestion, job status | `POST /api/v1/documents` → `202 {job_id}`; `GET /api/v1/ingestion/jobs/{id}`; durable Postgres queue in `backend/ingestion/worker.py` |
| Idempotent uploads | `Idempotency-Key` scoped to tenant + caller (unique index); same key + different file → `409` |
| Untrusted input isolation | size limit, extension/magic-byte check, parser in a separate process with timeout and address-space limit, optional malware-scan hook (`MALWARE_SCAN_COMMAND`) |
| Chunking 512–1024 tokens, 10–20% overlap, structure-aware | recursive splitter 512 / 75 tokens, applied per page/section so chunks never cross headings or pages |
| Q&A with exact citations | SSE `citation` events (document, version, page, chunk id) then `token`s; `[S#]` markers validated in `backend/qa/citations.py` |
| Access control at retrieval time | `backend/authz/policy.py` resolves the readable (document, active version) set per request; Pinecone filter + SQL filter use it; every hit is revalidated before prompt construction |
| Never trust client authorization values | tenant, role, groups come from the policy store; request bodies carry only the query and bounded preferences (`candidate_k ∈ {20,50,100}`) |
| Immutable versions, active pointer | `document_versions`; `documents.active_version` switches only after chunks + vectors are written; old version cleaned up asynchronously and never retrievable meanwhile |
| Immediate revocation / deletion | ACL change or delete updates Postgres first (effective on the next request), evicts tenant cache entries, then purges vectors/objects asynchronously |
| Cache scoped to authorization | retrieval cache key = tenant + scope fingerprint (readable versions) + normalized query + retrieval config + embedding/rerank model versions |
| Hybrid retrieval + RRF + rerank | `backend/retrieval/hybrid.py` — dense and keyword run in parallel, RRF (k=60), Cohere cross-encoder to top 5 |
| Low hallucination | grounded system prompt, abstain when no/weak evidence (`MIN_RERANK_SCORE`), "I don't know" instruction, citation validation, optional sampled LLM faithfulness judge |
| Prompt injection mitigation | evidence inside `<retrieved_context>`; tags and `[S#]` labels in document text neutralised; system rules say instruction-like text is not evidence and must not be followed or repeated; no tools; citations validated. Measured, not guaranteed: 5/5 adversarial cases pass (see Results) |
| Source file access | `GET /api/v1/documents/{id}/file` — same authorization check as retrieval; objects are never public |
| Embedding model versioning | `EMBEDDING_MODEL_VERSION` stored on every chunk/vector and part of the dense filter and cache key |
| Fault tolerance | retries with exponential backoff + jitter and deadlines (embeddings, Pinecone, LLM before first token); keyword-only fallback under the same ACL when Pinecone is down; fail closed if the policy store is down; clear error (no fabricated answer) if the LLM is down; failed parses keep the prior active version |
| Data residency / provider governance | tenants carry `region` and `allowed_llm_providers`; requests for tenants homed in another region, or whose tenant hasn't approved the LLM provider, are rejected |
| Auditability | `audit_events`: uploads, activations, ACL changes, deletions, downloads, every query (retrieved/cited chunk ids, policy version, model versions, timings, query hash — no source text) |
| Observability | `GET /api/v1/metrics` (admin): p50/p95/p99 for dense, keyword, rerank, retrieval, TTFT, end-to-end; cache hits, fallbacks, empty results, citation failures, abstentions, provider errors |
| Offline evaluation + regression gate | `backend/evaluation/eval_runner.py`: Recall@5, recall-stage recall, MRR, nDCG@5, answer accuracy, citation accuracy and validity, abstention, injection resistance, LLM-judged faithfulness, per-stage latency; `--gate` exits 1 on regression; runs in CI |
| Public demo safety | Guest sessions, a shared read-only library, per-user and per-IP rate limits plus a daily question budget, 5 MB / 5-document guest caps, guests cannot share, and guest data expires after 24 h |

### Not implemented (out of scope for this deployment)

- **Kafka / separate worker fleet** — the Postgres `SKIP LOCKED` queue gives durable, retryable, horizontally scalable jobs at this scale; the worker can run as its own process (`python -m backend.ingestion.worker`, `RUN_INGESTION_WORKER=false` on the API).
- **OCR / vision layout parsing** — image-only PDF pages are reported as ingestion warnings, not parsed.
- **Self-hosted GPU cross-encoder, index sharding for billions of vectors** — delegated to Cohere Rerank and Pinecone serverless.
- **Multi-region routing** — a deployment serves one region (`DEPLOYMENT_REGION`) and rejects tenants homed elsewhere; it does not forward them.
- **Shadow index / dual-read embedding migration** — vectors are tagged by model version so a new generation can be built alongside, but there is no automated backfill tool yet.
- **Per-tenant rerank bypass policy, bounded agentic multi-hop retrieval, HyDE.**

---

## API

All endpoints except `/health` require a bearer token when `AUTH_MODE=jwt`. In `AUTH_MODE=demo` (the default, used by the live demo), `POST /api/v1/auth/guest` issues a private guest session (the frontend calls it automatically), and `GET /api/v1/me` returns the caller and their limits. Requests without a token fall back to a shared `demo` guest identity.

### Ingest

```http
POST /api/v1/documents
Authorization: Bearer <token>
Idempotency-Key: 8f2c...            (required)
Content-Type: multipart/form-data

file=<HR_Manual.pdf>  metadata={"department":"HR"}  project_id=3  visibility=private|tenant|restricted
document_id=42        (optional: upload a new version of an existing document)

202 Accepted
{"tenant_id": 1, "document_id": 42, "document_version": 7, "job_id": "job_1234", "status": "PROCESSING"}
```

`GET /api/v1/ingestion/jobs/{job_id}` →
`{"job_id", "document_id", "document_version", "status": "PROCESSING|READY|FAILED", "stage", "indexed_chunks", "active_version", "error"}`

### Ask

```http
POST /api/v1/qa/query
{"query": "How many PTO days do I get?", "candidate_k": 50, "stream": true, "project_slug": "hr"}

data: {"type":"citation","label":"S1","document_id":42,"document_version":7,"page_number":4,"chunk_id":"chk_42_7_12","source_name":"HR_Manual.pdf", ...}
data: {"type":"token","text":"You receive 20 days [S1]..."}
data: {"type":"done","answer":"...","citations":[...],"invalid_citations":[],"grounded":true,"abstained":false,"timings":{...}}
```

`stream: false` returns the `done` payload plus `evidence` as JSON.

### Documents & access

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/documents?project_id=` | Documents you can read, with status and active version |
| GET | `/api/v1/documents/{id}` | Detail with version history and ingestion warnings |
| GET | `/api/v1/documents/{id}/file?version=` | Authorized download of the original file |
| GET / PUT | `/api/v1/documents/{id}/acl` | Owner/admin: `{"visibility": "private\|tenant\|restricted", "users": [ids], "groups": [ids]}` |
| DELETE | `/api/v1/documents/{id}` | Owner/admin: immediate revocation, async purge |
| GET | `/api/v1/chunks/{chunk_id}?context_size=1` | Authorized chunk with neighbours |
| GET | `/api/v1/metrics` | Admin: per-instance RAG health metrics |

Projects (`/api/projects`) and conversations (`/api/conversations`) are unchanged. The pre-v1 endpoints `/api/upload/document`, `/api/query`, `/api/projects/{slug}/query` and `DELETE /api/documents/{id}` remain as adapters over v1.

### Access model

- **Tenant** — hard isolation boundary (Pinecone namespace + SQL predicate).
- **Document visibility** — `private` (owner only, default), `tenant` (everyone in the tenant), `restricted` (owner + explicit user/group grants).
- **Roles** — `guest` (public demo: private uploads only, capped, cannot share), `member`, `admin` (admins manage any document in their tenant).
- **Shared projects** — projects with `visibility = 'tenant'` appear read-only to everyone in the tenant. The demo's *Sample Library* is one, seeded at startup from `demo/library/` (regenerate the files with `python demo/build_samples.py`).

Administer tenants, users, groups and tokens with the CLI:

```bash
python -m backend.auth.cli create-tenant --slug acme --name "Acme Corp"
python -m backend.auth.cli create-user --tenant acme --username alice --role admin
python -m backend.auth.cli create-group --tenant acme --name hr
python -m backend.auth.cli add-member --tenant acme --group hr --username alice
python -m backend.auth.cli issue-token --username alice
```

The frontend sends `localStorage["kb_access_token"]` (or `NEXT_PUBLIC_API_TOKEN`) as the bearer token.

---

## Getting started

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000      # creates/migrates tables on startup

cd frontend && npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Tests: `pip install pytest && pytest`

### Upgrading an existing deployment

The schema migrates in place on startup. Documents indexed by the previous version (vectors in Pinecone's default namespace, no stored file) appear as *Failed — legacy*; migrate them without re-embedding:

```bash
python -m backend.scripts.migrate_legacy_vectors --dry-run
python -m backend.scripts.migrate_legacy_vectors --delete-legacy
```

### Evaluation

```bash
# Ingest the eval corpus into an isolated `eval` tenant, run all 35 cases (+ LLM judge), save a baseline
python -m backend.evaluation.eval_runner --seed --judge --save backend/evaluation/results.json
# Regression gate (what CI runs): exit 1 if quality drops versus the committed baseline
python -m backend.evaluation.eval_runner --seed --save new.json --gate backend/evaluation/results.json
python -m backend.evaluation.tuning_runner --quick      # candidate_k × top_k × rerank sweep
```

The default 6 s delay between cases keeps within Cohere trial-key rate limits.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL connection string (required) |
| `AUTH_MODE` | `demo` | `demo` (public demo with guest sessions) or `jwt` |
| `JWT_SECRET` | generated | Token signing key; if unset, one is generated once and stored in Postgres (`app_secrets`) |
| `DEPLOYMENT_REGION` | `us-east-1` | Tenants homed in other regions are rejected |
| `OBJECT_STORE` | `s3` if `S3_BUCKET` set, else `postgres` | Where raw uploads live: `s3`, `postgres` or `local` (ephemeral on App Runner) |
| `S3_BUCKET`, `S3_REGION` | — | S3 object storage |
| `GUEST_TTL_HOURS`, `GUEST_MAX_DOCUMENTS`, `GUEST_MAX_UPLOAD_MB` | `24`, `5`, `5` | Demo guest limits |
| `SEED_DEMO_LIBRARY` | `true` | Seed the shared Sample Library from `demo/library/` at startup (demo mode) |
| `RATE_LIMIT_QA_PER_MINUTE`, `RATE_LIMIT_QA_PER_IP_PER_MINUTE` | `10`, `20` | Question rate limits (per instance) |
| `RATE_LIMIT_UPLOADS_PER_HOUR`, `RATE_LIMIT_GUESTS_PER_IP_PER_HOUR` | `10`, `10` | Upload and guest-session limits |
| `DAILY_QA_BUDGET` | `300` | Questions per day across all users (per instance); keep it under your provider quotas |
| `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | —, `knowledge-base` | Dense index |
| `COHERE_API_KEY` | — | Embeddings + reranking |
| `EMBEDDING_MODEL_VERSION` | `embed-english-v3.0@v1` | Index generation read by retrieval |
| `GROQ_API_KEY`, `GROQ_MODEL` | —, `openai/gpt-oss-120b` | Generation |
| `LLM_REASONING_EFFORT` | `low` | For reasoning models; `medium`/`high` add many seconds before the first token |
| `MAX_UPLOAD_SIZE_MB`, `MAX_PDF_PAGES` | `25`, `1000` | Upload limits (non-guest) |
| `PARSER_TIMEOUT_SECONDS`, `PARSER_MEMORY_LIMIT_MB` | `120`, `1024` | Parser sandbox limits |
| `MALWARE_SCAN_COMMAND` | — | e.g. `clamdscan --no-summary`; non-zero exit rejects the upload |
| `RUN_INGESTION_WORKER`, `INGESTION_WORKER_CONCURRENCY` | `true`, `2` | In-process worker |
| `USE_HYBRID_RETRIEVAL`, `USE_RERANKING` | `true`, `true` | Retrieval stages |
| `RERANK_TOP_K`, `RERANK_CANDIDATES`, `MIN_RERANK_SCORE` | `5`, `20`, `0.02` | Evidence passed to the LLM / candidates rescored / abstention threshold |
| `RETRIEVAL_TIMEOUT_SECONDS`, `LLM_FIRST_TOKEN_TIMEOUT`, `LLM_TOTAL_TIMEOUT` | `4`, `10`, `60` | Deadlines |
| `ONLINE_JUDGE_SAMPLE_RATE` | `0` | Fraction of answers scored by the faithfulness judge |
| `ENABLE_QUERY_ROUTING`, `ROUTER_LLM_CLASSIFICATION` | `true`, `false` | Keyword routing (greeting/meta/summary); the LLM is used only to rewrite follow-ups that have chat history, unless LLM classification is enabled |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | `false` | LLM tracing |
| `FRONTEND_URL` | `http://localhost:3000` | CORS |

---

## Project structure

```
backend/
├── main.py                  # app wiring, startup migration, demo seed, worker start
├── config.py
├── api/v1/                  # documents, ingestion jobs, ACLs, QA (SSE), metrics
├── api/legacy.py            # pre-v1 adapters
├── auth/                    # principal resolution, tokens, admin CLI
├── authz/policy.py          # readable-scope resolution (the authorization authority)
├── documents/repository.py  # documents, versions, ACLs, chunks, jobs
├── ingestion/               # parsers, sandbox, chunkers, pipeline, worker
├── retrieval/               # hybrid retriever (dense + keyword + RRF + rerank), caches
├── qa/                      # orchestrator, prompt, citation validation, online judge
├── llm/reasoning.py         # streaming LLM client with retries/deadlines
├── routing/query_router.py  # route classification + follow-up rewriting
├── storage/                 # Pinecone and object storage (S3 / Postgres / local)
├── demo/                    # guest sessions, Sample Library seeding
├── audit/, common/          # audit log, retries, request ids, metrics
├── evaluation/              # golden set, adversarial docs, eval runner + gate, results.json
└── scripts/                 # legacy index migration
demo/library/                # Sample Library documents (generated by demo/build_samples.py)
frontend/src/                # Next.js app (guest session, upload + job polling, cited chat)
tests/                       # unit tests: citations, authz scope, retrieval, parsing, tokens, rate limits, eval metrics
docs/ARCHITECTURE.md         # design decisions and why
```

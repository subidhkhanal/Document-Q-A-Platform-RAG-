# Architecture and design decisions

This is a study guide for the codebase. For each decision it covers **what** the code does, **why** it does it that way, the **alternatives** and their costs, and **where** the code lives, followed by the questions an interviewer is likely to ask. Read it alongside the code: open each file mentioned and trace one request end to end.

**Suggested reading order:** `backend/api/v1/documents.py` → `documents/repository.py::create_ingestion` → `ingestion/worker.py` → `authz/policy.py` → `retrieval/hybrid.py` → `qa/service.py` → `qa/prompt.py` → `qa/citations.py` → `evaluation/eval_runner.py`.

---

## 1. Two paths: asynchronous ingestion, synchronous query

**What.** Uploads return `202` with a job ID straight away. Parsing, chunking, embedding and indexing happen in a background worker. Questions take a separate, latency-sensitive path.

**Why.** Parsing and embedding a large PDF takes seconds to minutes and depends on external APIs that rate-limit and fail. Doing that work inside the HTTP request would cause client timeouts and duplicate work on retries. Separating the paths also means an ingestion backlog can't slow questions down.

**The queue is a Postgres table, not Kafka.** Workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` under a lease (`locked_at`), so several workers or instances can run safely, and a crashed worker's job is picked up again when its lease expires. Transient failures such as rate limits or network errors retry with exponential backoff and jitter. Permanent failures, like a malformed or rejected file, fail straight away. This gives durable, retryable jobs with no extra infrastructure. Kafka becomes worthwhile at much higher throughput or when many consumers need the same events.

**Where.** `ingestion/worker.py` (`claim_job`, `process_job`, `_fail`).

**Likely questions.** *What happens if the worker dies mid-job?* The lease expires, the job is re-claimed, and because chunk IDs are deterministic the retry overwrites rather than duplicates. *How do you avoid a poison job looping forever?* `INGESTION_MAX_ATTEMPTS`, plus a separate permanent-failure path.

## 2. Postgres is the source of truth; the vector index is derived

**What.** Chunk text, document versions, ACLs and tenants live in Postgres. Pinecone stores only vectors plus the metadata needed for filtering.

**Why.** Authorization must never depend on a store that can drift. If Pinecone is rebuilt, restored or partially cleaned up, Postgres still decides who can read what. It also means every result is hydrated from Postgres, which doubles as a re-check (section 4).

**Where.** `db/schema.py`, `storage/vector_store.py::build_chunk_metadata`, `retrieval/hybrid.py::_hydrate`.

## 3. Immutable versions; the active pointer moves only after indexing

**What.** Each upload creates an immutable `document_versions` row. `documents.active_version` is switched in a transaction only after every chunk and vector has been written. Retrieval filters on the active version, and superseded versions are cleaned up by a later job.

**Why.** Without this, a question asked mid-ingestion could see half of a new version mixed with the old one. It also keeps a failed re-upload from breaking a working document. Old vectors can't leak into answers even before cleanup, because the query filter excludes them.

**Where.** `worker.py::_activate` / `_mark_ready_and_switch`; the filter in `hybrid.py::dense_filter`.

**Likely question.** *Two versions finish out of order?* Only a higher version number can become active; a late older version is marked superseded immediately.

## 4. Authorization enforced during retrieval, not in the prompt

**What.** For every question, `resolve_scope` reads from Postgres the set of (document, active version) pairs the caller can read: owner, tenant-wide, or an explicit user/group grant. That scope is enforced three times:

1. **Pre-filter:** a Pinecone namespace per tenant, plus a metadata filter `doc_version_key ∈ scope` (filter-aware ANN).
2. **Keyword search:** the SQL itself joins on the same scope.
3. **Re-check:** every hit is hydrated from Postgres and dropped unless `scope.allows(tenant, doc, version)`.

It **fails closed**: if the policy store can't be read, no answer is produced.

**Why three layers.** A pre-filter alone trusts index metadata that could be stale. A post-filter alone can empty the result set when unauthorized neighbours crowd out authorized ones. The final re-check is defense in depth, and the LLM is never asked to enforce permissions.

**The cache is keyed by authorization.** The retrieval cache key includes a fingerprint of the readable scope. When a permission, version or deletion changes the scope, the key changes and the cache misses, with no invalidation bugs possible.

**Where.** `authz/policy.py` (`READABLE_PREDICATE`, `resolve_scope`, `fingerprint`), `retrieval/hybrid.py`.

**Likely questions.** *Why not put the user ID in the vector metadata?* Permissions change after indexing; re-writing vectors on every ACL change is slow and can go stale. *What if a scope contains 50k documents?* Above `DENSE_FILTER_MAX_KEYS` the dense query filters by tenant and embedding version only, over-fetches, and relies on the re-check.

## 5. Hybrid retrieval merged with Reciprocal Rank Fusion

**What.** Dense ANN (meaning) and Postgres full-text search (exact terms) run concurrently. Their ranked lists are merged with RRF, `score = Σ 1/(k + rank)` with k = 60.

**Why hybrid.** Dense embeddings miss exact identifiers, acronyms and numbers, such as `NW-4471` or `INV-2291`; keyword search catches them. Keyword search misses paraphrases; embeddings catch those.

**Why RRF rather than a weighted sum of scores.** Cosine similarity and `ts_rank` are on incomparable scales and their distributions shift per query, so any weights would need constant retuning. RRF uses only rank positions, needs no calibration, and rewards documents both retrievers agree on. `k` damps the influence of top ranks; 60 is the value from the original paper and works well without tuning.

**Why Postgres full-text instead of an in-memory BM25.** The earlier in-memory index was lost on every restart. A `tsvector` generated column with a GIN index persists, and can apply the ACL filter inside the same SQL query.

**Where.** `retrieval/hybrid.py::reciprocal_rank_fusion`, `_keyword`, `_dense`.

## 6. Cross-encoder reranking and abstention

**What.** The top 20 fused candidates are rescored by Cohere Rerank, a cross-encoder, and the top 5 go to the LLM. If the best reranked score is below `MIN_RERANK_SCORE` for a single-intent question, the system answers "I don't know" without calling the LLM.

**Why.** A bi-encoder embeds query and passage separately, which is fast but coarse. A cross-encoder reads them together and is much more precise, but costs time per candidate, so it only sees a short list. Capping at 20 candidates trades a little recall for latency.

**Known caveat.** Cross-encoder scores are calibrated for single-intent questions. Compound questions ("compare X with Y") score low even on relevant passages. For summary and comparison routes, the prompt's "I don't know" rule and citation validation decide instead.

## 7. Prompt construction and citation validation

**What.** Evidence is placed inside `<retrieved_context>`. Each passage gets a header (`[S1] Document | Version | Page | Section | Chunk`), and the model must cite with `[S#]`. After generation, every marker is parsed. Markers that don't map to authorized evidence are removed and reported, and the UI replaces the streamed text with the validated answer.

**Why labels instead of raw chunk IDs.** Short labels are easy for the model to reproduce accurately and trivially verifiable. The server keeps the mapping from label to (document, version, chunk), so a model can't invent a citation that resolves to something the user can't see.

**Provenance is not semantic support.** Validation proves a citation points at authorized evidence. It does not prove the passage supports the claim. That second property is measured separately by the LLM judge and the citation-accuracy metric.

**Robustness details that came from real model output.** Models emit `【S1】` (full-width brackets), `[Cited from S1]`, zero-width spaces inside brackets, and curly apostrophes in "I don’t know". The parser normalizes all of these; see the tests in `tests/test_citations_and_prompt.py`.

**Where.** `qa/prompt.py`, `qa/citations.py`, and the `done` handling in `frontend/src/components/ChatWidget.tsx`.

## 8. Prompt-injection mitigation (not prevention)

**Layers.**
- Document text is data, delimited and neutralized: closing tags and `[S#]` labels inside documents are rewritten.
- System rules say instruction-like text is not evidence and must not be followed, repeated or mentioned.
- There are no tools to hijack.
- Citations are validated.

**Measured.** In the adversarial eval cases, the first run leaked one canary. The model quoted an injected "answer in French" instruction while obeying a different rule, "state conflicts between sources". The fix clarified that conflicts are only between *facts*, and instruction-like text is never evidence. After that, 5/5 passed.

**Say "mitigated", never "prevented".** Five hand-written attacks are not a guarantee, and a determined attacker can often find a phrasing that works.

## 9. Idempotency and deterministic chunk IDs

**What.** Uploads carry an `Idempotency-Key`, which is unique per tenant and caller in the database. Replaying the same key returns the original job; the same key with a different file gets `409`. Chunk IDs are `chk_{doc}_{version}_{index}_{content-hash prefix}`.

**Why.** Deterministic IDs make retries overwrite rather than duplicate. The content-hash suffix exists because of a bug found while testing: after the database was reset but the vector index wasn't, reused document IDs produced the same chunk IDs, and old vectors attached themselves to new text. Content-addressing makes that impossible.

## 10. Latency: measure first, then fix

The service returns a per-stage `timings` breakdown with every answer (`qa/service.py`), and the eval reports p50/p95 per stage.

| Finding | Fix |
|---|---|
| Reasoning model at default effort: ~20 s to first token (hidden reasoning tokens) | `reasoning_effort=low` → ~0.7 s |
| An LLM routing call before retrieval (~1 s) | Keyword routing on the hot path; the LLM is used only to rewrite follow-ups with chat history |
| Embedding waited for authorization | Both now start together |
| Reranker cost grows with candidate count | Capped at 20 |

The rest is network distance to the Pinecone and Cohere regions, so the deployed backend should be close to them.

## 11. The public demo reuses the real security model

**What.** Every visitor gets a real `guest` user, created by `POST /api/v1/auth/guest`, not a shared anonymous account. The Sample Library is a tenant-visible project owned by a `library` user, so guests can read it but not change it. Guest uploads are `private`, which means other visitors can't see them.

**Why.** It shows the ACL model working on a live site: isolation between visitors is enforced by the same code paths a real tenant uses. Guests also get:
- per-user and per-IP rate limits and a daily question budget, which protect provider credits;
- smaller upload caps and no sharing;
- expiry after 24 hours, handled by a periodic job that queues deletions.

**Where.** `backend/demo/`, `common/rate_limit.py`, the guest branches in `api/v1/documents.py`.

**Limitation.** Rate limits are per instance and kept in memory. An exact global limit needs a shared store such as Redis.

## 12. Evaluation design and its limits

- **Retrieval.** Relevance is chunk-level: a chunk is relevant if it contains the gold evidence passage. Recall@5, MRR and nDCG@5 are computed on the reranked passages the model actually saw. Recall-stage recall is computed on the candidates before reranking, which separates "retriever missed it" from "reranker demoted it".
- **Answers.** Required facts are checked with alternatives, e.g. `["5", "five"]`. Citation accuracy means a cited passage contains the gold evidence. Abstention is measured on unanswerable questions. Injection cases check for canaries in the answer.
- **Regression gate.** CI compares against the committed `results.json` and fails if recall or citation accuracy drops, or if injection resistance or abstention regresses.
- **Limits to state up front.**
  - The set is small (35 cases, 11 documents) and self-authored.
  - Perfect retrieval on a tiny corpus says little about retrieval at scale.
  - The judge shares a model family with the generator.
  - The prompt was tuned once against the adversarial set.
  - Next steps would be a larger, externally written golden set, a judge from a different provider, and human-audited samples.

## 13. What I would do next

- A larger, externally sourced evaluation set, plus a judge from another provider.
- OCR for scanned PDFs, and table extraction for borderless tables.
- A Redis-backed rate limiter and retrieval cache shared across instances.
- A shadow index and dual reads for embedding-model migrations.
- Rerank-free fast path for high-confidence keyword hits to cut latency.

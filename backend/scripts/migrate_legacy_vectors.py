"""One-off migration of documents indexed by the pre-v2 app.

The old app stored vectors (with chunk text in metadata) in Pinecone's default
namespace, keyed by user_id + source filename, and kept no raw file. This script
moves each legacy document into the versioned model: version 1 is created from the
stored chunk text, chunks are written to PostgreSQL, vectors are re-upserted into
the tenant namespace with the new metadata (reusing the existing embeddings), and
the document's active version is set.

The existing vectors must come from the model configured in EMBEDDING_MODEL_VERSION
(the old default, embed-english-v3.0). Otherwise re-upload the documents instead.

    python -m backend.scripts.migrate_legacy_vectors --dry-run
    python -m backend.scripts.migrate_legacy_vectors [--delete-legacy]
"""

import argparse
import asyncio
import hashlib
from collections import defaultdict
from typing import Dict, List, Tuple

from backend.auth.database import init_db
from backend.components import get_vector_store
from backend.config import EMBEDDING_MODEL_VERSION
from backend.db.connection import close_pools, db_session
from backend.documents import repository as repo
from backend.storage.vector_store import build_chunk_metadata

LEGACY_NAMESPACE = ""
FETCH_BATCH = 100


def _load_legacy_vectors(vs) -> Dict[Tuple[str, str], List[dict]]:
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    ids: List[str] = []
    for page in vs.index.list(namespace=LEGACY_NAMESPACE):
        ids.extend(page)
    for i in range(0, len(ids), FETCH_BATCH):
        fetched = vs.index.fetch(ids=ids[i:i + FETCH_BATCH], namespace=LEGACY_NAMESPACE)
        for vid, vec in fetched.vectors.items():
            meta = dict(vec.metadata or {})
            if "text" not in meta or "source" not in meta:
                continue
            groups[(str(meta.get("user_id", "")), meta["source"])].append(
                {"id": vid, "values": list(vec.values), "metadata": meta}
            )
    return groups


async def migrate(dry_run: bool, delete_legacy: bool) -> None:
    await init_db()
    vs = await asyncio.to_thread(get_vector_store)
    groups = await asyncio.to_thread(_load_legacy_vectors, vs)
    print(f"Found {sum(len(v) for v in groups.values())} legacy vectors in {len(groups)} (user, source) groups")

    async with db_session() as db:
        docs = await db.fetch_all(
            """SELECT d.* FROM documents d
               WHERE d.deleted_at IS NULL AND d.active_version IS NULL
                 AND NOT EXISTS (SELECT 1 FROM document_versions v WHERE v.document_id = d.id)"""
        )

    migrated_ids: List[str] = []
    for doc in docs:
        vectors = groups.get((str(doc["user_id"]), doc["filename"]))
        if not vectors:
            print(f"  skip  doc {doc['id']} {doc['filename']!r}: no legacy vectors (re-upload it)")
            continue
        vectors.sort(key=lambda v: (v["metadata"].get("chunk_index", 0)))
        print(f"  {'would migrate' if dry_run else 'migrate'} doc {doc['id']} {doc['filename']!r}: {len(vectors)} chunks")
        if dry_run:
            continue

        chunks, payload = [], []
        for index, v in enumerate(vectors):
            meta, text = v["metadata"], v["metadata"]["text"]
            content_hash = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            chunk = {
                "chunk_id": repo.chunk_id_for(doc["id"], 1, index, content_hash),
                "tenant_id": doc["tenant_id"],
                "document_id": doc["id"],
                "document_version": 1,
                "chunk_index": index,
                "page_number": int(meta["page"]) if meta.get("page") is not None else None,
                "section_title": meta.get("section_title") or meta.get("chapter_title"),
                "text": text,
                "token_count": int(meta["token_count"]) if meta.get("token_count") else None,
                "content_hash": content_hash,
                "embedding_model_version": EMBEDDING_MODEL_VERSION,
            }
            chunks.append(chunk)
            payload.append({"id": chunk["chunk_id"], "values": v["values"], "metadata": build_chunk_metadata(chunk)})

        doc_hash = "sha256:" + hashlib.sha256("".join(c["text"] for c in chunks).encode()).hexdigest()
        async with db_session() as db:
            await db.run(
                """INSERT INTO document_versions
                       (document_id, version, status, filename, object_key, content_hash, size_bytes, mime_type,
                        chunk_count, embedding_model_version, warnings, created_by, ready_at)
                   VALUES ($1, 1, 'processing', $2, NULL, $3, $4, $5, $6, $7, $8, $9, NOW())""",
                doc["id"], doc["filename"], doc_hash, doc["size_bytes"], doc["mime_type"], len(chunks),
                EMBEDDING_MODEL_VERSION, ["Migrated from the legacy index; the original file was not retained"],
                doc["user_id"],
            )
        await repo.replace_chunks(doc["tenant_id"], doc["id"], 1, chunks)
        await asyncio.to_thread(vs.upsert, doc["tenant_id"], payload)
        async with db_session() as db:
            async with db.transaction():
                await db.run("UPDATE document_versions SET status = 'ready' WHERE document_id = $1 AND version = 1", doc["id"])
                await db.run("UPDATE documents SET active_version = 1, updated_at = NOW() WHERE id = $1", doc["id"])
        migrated_ids.extend(v["id"] for v in vectors)

    if delete_legacy and migrated_ids and not dry_run:
        for i in range(0, len(migrated_ids), 1000):
            await asyncio.to_thread(vs.index.delete, ids=migrated_ids[i:i + 1000], namespace=LEGACY_NAMESPACE)
        print(f"Deleted {len(migrated_ids)} migrated legacy vectors")
    await close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete-legacy", action="store_true", help="Delete migrated vectors from the default namespace")
    args = parser.parse_args()
    asyncio.run(migrate(args.dry_run, args.delete_legacy))


if __name__ == "__main__":
    main()

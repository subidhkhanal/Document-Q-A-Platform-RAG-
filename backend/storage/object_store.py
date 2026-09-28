"""Raw document storage — the source of truth for uploaded bytes.

Objects are private; the application mediates every read after the same
authorization check used for retrieval (see GET /api/v1/documents/{id}/file).
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from backend.config import OBJECT_STORE, S3_BUCKET, S3_REGION, UPLOADS_DIR


def object_key(tenant_id: int, document_id: int, version: int, content_hash: str, extension: str) -> str:
    digest = content_hash.split(":", 1)[-1]  # no ":" in keys (NTFS alternate data streams)
    return f"tenants/{tenant_id}/documents/{document_id}/v{version}/{digest[:16]}{extension}"


class LocalObjectStore:
    def __init__(self, root: str = UPLOADS_DIR):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid object key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(lambda: self._path(key).unlink(missing_ok=True))


class S3ObjectStore:
    def __init__(self, bucket: str = S3_BUCKET, region: str = S3_REGION):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    async def get(self, key: str) -> bytes:
        resp = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(resp["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


class PostgresObjectStore:
    """Bytes in a bytea table: durable across restarts with no bucket to provision.
    Suited to demo-scale corpora; use S3 for large volumes."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        from backend.db.connection import db_session

        async with db_session() as db:
            await db.run(
                """INSERT INTO object_blobs (key, content_type, data) VALUES ($1, $2, $3)
                   ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, content_type = EXCLUDED.content_type""",
                key, content_type, data,
            )

    async def get(self, key: str) -> bytes:
        from backend.db.connection import db_session

        async with db_session() as db:
            data = await db.fetch_val("SELECT data FROM object_blobs WHERE key = $1", key)
        if data is None:
            raise FileNotFoundError(key)
        return bytes(data)

    async def delete(self, key: str) -> None:
        from backend.db.connection import db_session

        async with db_session() as db:
            await db.run("DELETE FROM object_blobs WHERE key = $1", key)


_store: Optional[object] = None


def get_object_store():
    global _store
    if _store is None:
        if OBJECT_STORE == "s3":
            _store = S3ObjectStore()
        elif OBJECT_STORE == "local":
            _store = LocalObjectStore()
        else:
            _store = PostgresObjectStore()
    return _store

"""Run untrusted-document parsing in a separate process with a wall-clock timeout
and (on Linux) an address-space limit, after an optional malware scan. A parser
crash, hang or memory blow-up kills only the child process."""

import multiprocessing as mp
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

from backend.config import MALWARE_SCAN_COMMAND, PARSER_ISOLATION, PARSER_MEMORY_LIMIT_MB, PARSER_TIMEOUT_SECONDS


class ParserError(Exception):
    """Parsing failed permanently; the upload is quarantined (job FAILED)."""


def _child(kind: str, path: str, filename: str, conn) -> None:
    try:
        try:
            import resource

            limit = PARSER_MEMORY_LIMIT_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass  # not available on this platform

        from backend.ingestion.parsers import parse_file

        conn.send(("ok", parse_file(kind, path, filename)))
    except MemoryError:
        conn.send(("error", "Parser exceeded its memory limit"))
    except Exception as e:  # noqa: BLE001 — report any parser failure to the parent
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        conn.close()


def _malware_scan(path: str) -> None:
    if not MALWARE_SCAN_COMMAND:
        return
    try:
        result = subprocess.run(shlex.split(MALWARE_SCAN_COMMAND) + [path], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        # Scanner infrastructure problem: transient, let the job retry.
        raise RuntimeError(f"Malware scanner unavailable: {e}") from e
    if result.returncode != 0:
        raise ParserError("Upload rejected by malware scan")


def parse_sandboxed(kind: str, data: bytes, filename: str, timeout: float = PARSER_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Blocking; call via asyncio.to_thread."""
    fd, path = tempfile.mkstemp(suffix=Path(filename).suffix.lower())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _malware_scan(path)

        if PARSER_ISOLATION == "thread":
            # The caller enforces the timeout (see worker._ingest); errors become ParserError.
            from backend.ingestion.parsers import UnsupportedDocument, parse_file

            try:
                return parse_file(kind, path, filename)
            except UnsupportedDocument:
                raise
            except Exception as e:  # noqa: BLE001 — malformed input: quarantine the upload
                raise ParserError(f"{type(e).__name__}: {e}") from e

        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_child, args=(kind, path, filename, child_conn), daemon=True)
        proc.start()
        child_conn.close()
        try:
            if not parent_conn.poll(timeout):
                raise ParserError(f"Parser timed out after {timeout:.0f}s")
            try:
                status, payload = parent_conn.recv()
            except EOFError:
                raise ParserError("Parser process crashed")
        finally:
            if proc.is_alive():
                proc.terminate()
            proc.join(5)
            parent_conn.close()

        if status != "ok":
            raise ParserError(payload)
        return payload
    finally:
        Path(path).unlink(missing_ok=True)

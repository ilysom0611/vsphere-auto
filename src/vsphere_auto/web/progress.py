"""In-memory live progress for running batches.

The web process runs the deploy threads, so per-VM "current operation" state
lives here: deploy code calls report(batch_id, vm_name, step_key, detail) and
the tasks API enriches running task rows with it. Deliberately volatile —
finished rows get their authoritative state from the DB; this only answers
"what is happening RIGHT NOW".
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# batch_id -> {vm_name: {"step": str, "detail": str, "ts": float}}
_live: dict[str, dict[str, dict]] = {}


def report(batch_id: str, name: str, step: str, detail: str = "") -> None:
    with _lock:
        _live.setdefault(str(batch_id), {})[name] = {"step": str(step), "detail": str(detail or ""), "ts": time.time()}


def snapshot(batch_id: str | None = None) -> dict:
    """Copy of live progress, optionally scoped to one batch."""
    with _lock:
        if batch_id is not None:
            b = _live.get(str(batch_id))
            return {n: dict(v) for n, v in b.items()} if b else {}
        return {b: {n: dict(v) for n, v in entries.items()} for b, entries in _live.items()}


def drop_batch(batch_id: str) -> None:
    with _lock:
        _live.pop(str(batch_id), None)

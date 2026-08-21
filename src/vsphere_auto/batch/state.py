"""Idempotency state persistence (SQLite)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..utils.paths import get_state_dir

VALID_BATCH_STATUSES = {"pending", "running", "success", "partial", "failed", "interrupted"}

# Keys whose values must never reach config_json in plaintext.
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "credential")


def _redact_config(obj: Any) -> Any:
    """Deep-copy with sensitive-key values replaced by '***'."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if any(part in k.lower() for part in _SENSITIVE_KEY_PARTS):
                out[k] = "***"
            else:
                out[k] = _redact_config(v)
        return out
    if isinstance(obj, list):
        return [_redact_config(x) for x in obj]
    return obj


def _db_path(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir) / "batch.db"
    return get_state_dir() / "batch.db"


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Best-effort WAL + busy timeout for concurrent writers (CentOS 7.9 may not support WAL)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def _chmod_sidecars(db: Path) -> None:
    """WAL/SHM sidecars contain the same data as the DB — protect them too."""
    for suffix in ("-wal", "-shm"):
        try:
            sidecar = Path(str(db) + suffix)
            if sidecar.exists():
                import os

                os.chmod(sidecar, 0o600)
        except Exception:
            pass


def init_db(state_dir: Path | None = None) -> Path:
    db = _db_path(state_dir)
    conn = _connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                batch_id TEXT,
                vm_name TEXT,
                spec_hash TEXT,
                status TEXT,
                spec_json TEXT,
                result_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                config_json TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        # Index for batch-scoped task listing
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_batch_id ON tasks(batch_id)")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA user_version = 1;")
        except Exception:
            pass
        conn.commit()
        try:
            import os

            os.chmod(db, 0o600)
        except Exception:
            pass
        _chmod_sidecars(db)
    finally:
        conn.close()
    return db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_batch(batch_id: str, config: dict[str, Any], state_dir: Path | None = None) -> None:
    """Insert a batch row. Config is redacted before persisting (no plaintext secrets).

    Raises ValueError if batch_id already exists — callers should allocate ids via
    :func:`create_batch_auto` to avoid races between concurrent deployments.
    """
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        try:
            conn.execute(
                "INSERT INTO batches (id, config_json, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (batch_id, json.dumps(_redact_config(config), ensure_ascii=False), "pending", _now(), _now()),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"batch id already exists: {batch_id}") from e
        conn.commit()
    finally:
        conn.close()


def create_batch_auto(config: dict[str, Any], state_dir: Path | None = None) -> str:
    """Atomically allocate the next batch id and insert the row.

    Uses BEGIN IMMEDIATE so two concurrent deployments can never compute the
    same ``batch-NNNN`` id and overwrite each other's rows.
    """
    init_db(state_dir)
    db = _db_path(state_dir)
    last_err: Exception | None = None
    for _ in range(5):
        conn = _connect(db)
        try:
            conn.isolation_level = None  # manual transaction control
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(CAST(substr(id, 7) AS INTEGER)), 0) FROM batches"
            ).fetchone()
            candidate = f"batch-{int(row[0] or 0) + 1:04d}"
            conn.execute(
                "INSERT INTO batches (id, config_json, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (candidate, json.dumps(_redact_config(config), ensure_ascii=False), "pending", _now(), _now()),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as e:
            last_err = e
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        finally:
            conn.close()
    raise RuntimeError(f"could not allocate a unique batch id after retries: {last_err}")


def update_batch_status(batch_id: str, status: str, state_dir: Path | None = None) -> None:
    if status not in VALID_BATCH_STATUSES:
        raise ValueError(f"invalid batch status {status!r}; expected one of {sorted(VALID_BATCH_STATUSES)}")
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        conn.execute("UPDATE batches SET status=?, updated_at=? WHERE id=?", (status, _now(), batch_id))
        conn.commit()
    finally:
        conn.close()


def recover_interrupted(state_dir: Path | None = None) -> tuple[int, int]:
    """Mark stale running/pending rows as interrupted after a crash/restart.

    Returns (tasks_marked, batches_marked). Call at service startup.
    """
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        cur_t = conn.execute("UPDATE tasks SET status='interrupted', updated_at=? WHERE status='running'", (_now(),))
        cur_b = conn.execute(
            "UPDATE batches SET status='interrupted', updated_at=? WHERE status IN ('pending','running')",
            (_now(),),
        )
        conn.commit()
        return cur_t.rowcount, cur_b.rowcount
    finally:
        conn.close()


def list_batches(state_dir: Path | None = None) -> list[dict[str, Any]]:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_task(task_id: str, batch_id: str, vm_name: str, spec_hash: str, status: str, spec: dict[str, Any], result: dict[str, Any] | None = None, state_dir: Path | None = None) -> None:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        row = conn.execute("SELECT created_at FROM tasks WHERE id=?", (task_id,)).fetchone()
        created = row["created_at"] if row and row["created_at"] else _now()
        conn.execute(
            "INSERT OR REPLACE INTO tasks (id, batch_id, vm_name, spec_hash, status, spec_json, result_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, batch_id, vm_name, spec_hash, status, json.dumps(spec, ensure_ascii=False), json.dumps(result or {}, ensure_ascii=False), created, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_tasks(batch_id: str | None = None, state_dir: Path | None = None) -> list[dict[str, Any]]:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        if batch_id:
            rows = conn.execute("SELECT * FROM tasks WHERE batch_id=? ORDER BY created_at", (batch_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task(task_id: str, state_dir: Path | None = None) -> dict[str, Any] | None:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def next_batch_id(state_dir: Path | None = None) -> str:
    """Preview-only helper; NOT race-safe. Prefer :func:`create_batch_auto`."""
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        row = conn.execute("SELECT COALESCE(MAX(CAST(substr(id, 7) AS INTEGER)), 0) FROM batches").fetchone()
        n = int(row[0] or 0) + 1 if row else 1
        while True:
            candidate = f"batch-{n:04d}"
            exists = conn.execute("SELECT 1 FROM batches WHERE id=?", (candidate,)).fetchone()
            if not exists:
                return candidate
            n += 1
    finally:
        conn.close()

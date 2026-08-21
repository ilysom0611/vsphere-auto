"""Idempotency state persistence (SQLite)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB = Path("state/batch.db")


def _db_path(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir) / "batch.db"
    if Path("vsphere-auto/state").exists():
        return Path("vsphere-auto/state/batch.db")
    return DEFAULT_DB


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


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
        conn.commit()
        try:
            os.chmod(db, 0o600)
        except Exception:
            pass
    finally:
        conn.close()
    return db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_batch(batch_id: str, config: dict[str, Any], state_dir: Path | None = None) -> None:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO batches (id, config_json, status, created_at, updated_at) VALUES (?,?,?,?,?)",
            (batch_id, json.dumps(config, ensure_ascii=False), "pending", _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_batch_status(batch_id: str, status: str, state_dir: Path | None = None) -> None:
    init_db(state_dir)
    conn = _connect(_db_path(state_dir))
    try:
        conn.execute("UPDATE batches SET status=?, updated_at=? WHERE id=?", (status, _now(), batch_id))
        conn.commit()
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
        conn.execute(
            "INSERT OR REPLACE INTO tasks (id, batch_id, vm_name, spec_hash, status, spec_json, result_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, batch_id, vm_name, spec_hash, status, json.dumps(spec, ensure_ascii=False), json.dumps(result or {}, ensure_ascii=False), _now(), _now()),
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
    init_db(state_dir)
    batches = list_batches(state_dir)
    return f"batch-{len(batches)+1:04d}"

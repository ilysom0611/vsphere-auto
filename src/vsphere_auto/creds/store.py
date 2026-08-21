"""SQLite credential store with Fernet-encrypted passwords."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .crypto import encrypt_password, decrypt_password

DEFAULT_DB = Path("state/creds.db")


@dataclass
class Creds:
    id: int
    name: str
    host: str
    port: int
    username: str
    password_enc: str
    type: str  # vcenter | esxi
    created_at: str
    updated_at: str

    def to_safe_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "hasPassword": bool(self.password_enc),
            "type": self.type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def decrypted_password(self, state_dir: Path | None = None) -> str:
        if not self.password_enc:
            return ""
        return decrypt_password(self.password_enc, state_dir)


def _db_path(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir) / "creds.db"
    # prefer vsphere-auto/state when running from repo root
    if Path("vsphere-auto/state").exists():
        return Path("vsphere-auto/state/creds.db")
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
            CREATE TABLE IF NOT EXISTS endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 443,
                username TEXT NOT NULL,
                password_enc TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT 'vcenter',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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


def list_creds(state_dir: Path | None = None) -> list[Creds]:
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM endpoints ORDER BY id").fetchall()
        return [Creds(**dict(r)) for r in rows]
    finally:
        conn.close()


def get_creds(creds_id: int, state_dir: Path | None = None) -> Optional[Creds]:
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM endpoints WHERE id=?", (creds_id,)).fetchone()
        return Creds(**dict(row)) if row else None
    finally:
        conn.close()


def get_by_name(name: str, state_dir: Path | None = None) -> Optional[Creds]:
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM endpoints WHERE name=?", (name,)).fetchone()
        return Creds(**dict(row)) if row else None
    finally:
        conn.close()


def resolve_creds(ref: str, state_dir: Path | None = None) -> Optional[Creds]:
    """Resolve by id (numeric string) or name."""
    if ref.isdigit():
        c = get_creds(int(ref), state_dir)
        if c:
            return c
    return get_by_name(ref, state_dir)


def create_creds(
    name: str,
    host: str,
    username: str,
    password: str,
    port: int = 443,
    cred_type: str = "vcenter",
    state_dir: Path | None = None,
) -> Creds:
    db = _db_path(state_dir)
    init_db(state_dir)
    enc = encrypt_password(password, state_dir) if password else ""
    now = _now()
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO endpoints (name, host, port, username, password_enc, type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, host, port, username, enc, cred_type, now, now),
        )
        conn.commit()
        return get_creds(cur.lastrowid, state_dir)  # type: ignore
    finally:
        conn.close()


def update_creds(
    creds_id: int,
    name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,  # None = unchanged, "" = clear, value = new
    cred_type: str | None = None,
    state_dir: Path | None = None,
) -> Optional[Creds]:
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM endpoints WHERE id=?", (creds_id,)).fetchone()
        if not row:
            return None
        cur = dict(row)
        if name is not None:
            cur["name"] = name
        if host is not None:
            cur["host"] = host
        if port is not None:
            cur["port"] = port
        if username is not None:
            cur["username"] = username
        if password is not None:
            cur["password_enc"] = encrypt_password(password, state_dir) if password else ""
        if cred_type is not None:
            cur["type"] = cred_type
        cur["updated_at"] = _now()
        conn.execute(
            "UPDATE endpoints SET name=?, host=?, port=?, username=?, password_enc=?, type=?, updated_at=? WHERE id=?",
            (cur["name"], cur["host"], cur["port"], cur["username"], cur["password_enc"], cur["type"], cur["updated_at"], creds_id),
        )
        conn.commit()
        return get_creds(creds_id, state_dir)
    finally:
        conn.close()


def delete_creds(creds_id: int, state_dir: Path | None = None) -> bool:
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        cur = conn.execute("DELETE FROM endpoints WHERE id=?", (creds_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

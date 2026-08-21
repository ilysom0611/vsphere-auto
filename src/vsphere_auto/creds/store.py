"""SQLite credential store with Fernet-encrypted passwords."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..utils.paths import get_state_dir
from .crypto import encrypt_password, decrypt_password

DEFAULT_DB = Path("state/creds.db")
STATE_ENV = "VSPHERE_STATE_DIR"


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
    return get_state_dir() / "creds.db"


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
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
        # WAL/SHM sidecars carry the same data — protect them too
        for suffix in ("-wal", "-shm"):
            try:
                sidecar = Path(str(db) + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
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
    """Resolve by id (numeric string) or name.

    If ref is all digits, try get_by_name first for names like "123" — only
    fall back to ID lookup if no name matches. This avoids misclassifying
    numeric names as IDs.
    """
    # Try name first (handles numeric names like "123")
    c = get_by_name(ref, state_dir)
    if c:
        return c
    if ref.isdigit():
        try:
            c2 = get_creds(int(ref), state_dir)
            if c2:
                return c2
        except Exception:
            pass
    return None


def create_creds(
    name: str,
    host: str,
    username: str,
    password: str,
    port: int = 443,
    cred_type: str = "vcenter",
    state_dir: Path | None = None,
) -> Creds:
    if not name or not name.strip():
        raise ValueError("name required")
    if not host or not host.strip():
        raise ValueError("host required")
    if not (1 <= int(port) <= 65535):
        raise ValueError(f"port out of range: {port}")
    if cred_type not in ("vcenter", "esxi"):
        raise ValueError(f"type must be vcenter|esxi, got {cred_type!r}")
    db = _db_path(state_dir)
    init_db(state_dir)
    enc = encrypt_password(password, state_dir) if password else ""
    now = _now()
    conn = _connect(db)
    try:
        try:
            cur = conn.execute(
                "INSERT INTO endpoints (name, host, port, username, password_enc, type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (name.strip(), host.strip(), int(port), username.strip(), enc, cred_type, now, now),
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e) or "unique" in str(e).lower():
                raise ValueError(f"Name already exists: {name}") from e
            raise
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
    if port is not None and not (1 <= int(port) <= 65535):
        raise ValueError(f"port out of range: {port}")
    if cred_type is not None and cred_type not in ("vcenter", "esxi"):
        raise ValueError(f"type must be vcenter|esxi, got {cred_type!r}")
    db = _db_path(state_dir)
    init_db(state_dir)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM endpoints WHERE id=?", (creds_id,)).fetchone()
        if not row:
            return None
        enc: str | None = None
        if password is not None:
            enc = encrypt_password(password, state_dir) if password else ""
        # Single atomic UPDATE with COALESCE: concurrent updates to different
        # fields can no longer overwrite each other (no read-merge-write gap).
        new_name = name.strip() if name is not None else None
        new_host = host.strip() if host is not None else None
        new_port = int(port) if port is not None else None
        new_user = username.strip() if username is not None else None
        new_updated = _now()
        try:
            conn.execute(
                """
                UPDATE endpoints SET
                    name=COALESCE(?, name),
                    host=COALESCE(?, host),
                    port=COALESCE(?, port),
                    username=COALESCE(?, username),
                    password_enc=COALESCE(?, password_enc),
                    type=COALESCE(?, type),
                    updated_at=?
                WHERE id=?
                """,
                (new_name, new_host, new_port, new_user, enc, cred_type, new_updated, creds_id),
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e) or "unique" in str(e).lower():
                raise ValueError(f"Name already exists: {new_name or row['name']}") from e
            raise
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

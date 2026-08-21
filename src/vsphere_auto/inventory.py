"""Inventory cache: store discovery results with TTL."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("state/inventory.json")
TTL_SECONDS = 3600


def _inv_path(state_dir: Path | None = None) -> Path:
    env = os.environ.get("VSPHERE_STATE_DIR")
    if env:
        return Path(env) / "inventory.json"
    if state_dir:
        return Path(state_dir) / "inventory.json"
    if Path("vsphere-auto/state").exists():
        return Path("vsphere-auto/state/inventory.json")
    return DEFAULT_PATH


def save_inventory(data: dict[str, Any], state_dir: Path | None = None) -> Path:
    p = _inv_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Do not mutate caller dict
    to_save = dict(data)
    to_save["_saved_at"] = time.time()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".inventory.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(to_save, ensure_ascii=False, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, p)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
    return p


def load_inventory(state_dir: Path | None = None, max_age: int = TTL_SECONDS) -> dict[str, Any] | None:
    p = _inv_path(state_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    saved = data.get("_saved_at", 0)
    if max_age > 0 and (time.time() - saved) > max_age:
        return None
    return data


def load_inventory_any(state_dir: Path | None = None) -> dict[str, Any] | None:
    return load_inventory(state_dir, max_age=0)

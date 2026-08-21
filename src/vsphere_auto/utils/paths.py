"""Centralised state-directory resolution.

Historically every module (batch/state.py, net/ippool.py, creds/store.py,
creds/crypto.py) resolved the state directory independently from the process
cwd, which meant systemd (cwd=/) and a manual shell (cwd=repo root) could end
up reading and writing *different* batch.db / ip_pools.json / .fernet.key
files — most visibly as "key mismatch" errors after restarting the service
from a different directory.

All modules must now go through :func:`get_state_dir` so there is exactly one
resolution rule:

1. ``$VSPHERE_STATE_DIR`` if set (systemd unit / start.sh may set this);
2. ``./vsphere-auto/state`` if it already exists (legacy nested layout);
3. ``./state`` if it already exists (legacy flat layout);
4. walk upwards from cwd looking for a project root (``pyproject.toml`` or
   ``.git``) and use ``<root>/state`` — makes manual runs from a subdirectory
   land in the same place as runs from the repo root;
5. ``./state`` as a last resort (created on demand).
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = "VSPHERE_STATE_DIR"


def _find_project_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists():
            return candidate
    return None


def get_state_dir() -> Path:
    """Return the single state directory for this installation."""
    env = os.environ.get(_ENV_VAR)
    if env:
        path = Path(env).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    cwd = Path.cwd()
    legacy_nested = cwd / "vsphere-auto" / "state"
    if legacy_nested.is_dir():
        return legacy_nested
    legacy_flat = cwd / "state"
    if legacy_flat.is_dir():
        return legacy_flat

    root = _find_project_root(cwd)
    if root is not None:
        return root / "state"

    fallback = cwd / "state"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

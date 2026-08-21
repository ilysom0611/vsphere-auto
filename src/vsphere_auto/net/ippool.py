"""Simple IP pool allocator (CIDR-based)."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_POOL_FILE = Path("state/ip_pools.json")


def _pool_path(state_dir: Path | None = None) -> Path:
    env = os.environ.get("VSPHERE_STATE_DIR")
    if env:
        return Path(env) / "ip_pools.json"
    if state_dir:
        return Path(state_dir) / "ip_pools.json"
    if Path("vsphere-auto/state").exists():
        return Path("vsphere-auto/state/ip_pools.json")
    return DEFAULT_POOL_FILE


def _load_pools(state_dir: Path | None = None) -> dict:
    p = _pool_path(state_dir)
    if not p.exists():
        return {"allocated": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("ip_pools.json corrupted (%s): %s — treating as empty (manual fix may be needed)", p, e)
        return {"allocated": []}


def _save_pools(data: dict, state_dir: Path | None = None) -> None:
    p = _pool_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via tmp + rename
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".ip_pools.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # optional file lock
            try:
                import fcntl  # type: ignore

                fcntl.flock(f, fcntl.LOCK_EX)
            except Exception:
                pass
            f.write(json.dumps(data, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
            try:
                import fcntl  # type: ignore

                fcntl.flock(f, fcntl.LOCK_UN)
            except Exception:
                pass
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def allocate_ip(cidr: str, gateway: str | None = None, allocated: list[str] | None = None, state_dir: Path | None = None) -> str | None:
    """Allocate next free IP from CIDR, skipping gateway/network/broadcast and already allocated."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        log.warning("allocate_ip: invalid cidr %r: %s", cidr, e)
        return None
    if net.prefixlen == 32:
        log.warning("allocate_ip: cidr %s is /32 (no usable hosts)", cidr)
        return None
    if net.prefixlen == 31:
        # RFC 3021: both addresses usable, but hosts() yields both — ok
        pass
    taken = set(allocated or [])
    # also load persisted
    persisted = _load_pools(state_dir).get("allocated", [])
    taken.update(persisted)
    if gateway:
        taken.add(gateway)
    for ip in net.hosts():
        s = str(ip)
        if s not in taken:
            return s
    return None


def persist_allocate(ip: str, state_dir: Path | None = None) -> None:
    # Use advisory lock + atomic write via _save_pools with lock
    # Re-read under lock to avoid read-modify-write race
    p = _pool_path(state_dir)
    # Simple retry with lock file
    for _ in range(5):
        data = _load_pools(state_dir)
        if ip in data.get("allocated", []):
            return
        data.setdefault("allocated", []).append(ip)
        try:
            _save_pools(data, state_dir)
            # verify
            check = _load_pools(state_dir)
            if ip in check.get("allocated", []):
                return
        except Exception as e:
            log.warning("persist_allocate %s failed: %s (retrying)", ip, e)
            import time

            time.sleep(0.05)
            continue
        return


def persist_release(ip: str, state_dir: Path | None = None) -> None:
    for _ in range(5):
        data = _load_pools(state_dir)
        if ip not in data.get("allocated", []):
            return
        data["allocated"].remove(ip)
        try:
            _save_pools(data, state_dir)
            return
        except Exception as e:
            log.warning("persist_release %s failed: %s (retrying)", ip, e)
            import time

            time.sleep(0.05)
            continue

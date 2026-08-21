"""Simple IP pool allocator (CIDR-based).

Persistence model: ``<state>/ip_pools.json`` guarded by a STABLE lock file
(``ip_pools.json.lock``, one inode for the lifetime of the installation).
Every read-modify-write cycle (allocate/release) holds the lock across
load → check → mutate → verify, so concurrent deployments cannot hand the
same address to two VMs.

Failure semantics are deliberately FAIL-CLOSED:
- a corrupt pool file aborts allocation (after backing the file up) rather
  than silently treating every address as free;
- ``persist_allocate`` raises after its retries instead of pretending
  success — an unpersisted lease would be handed out again later.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from ..utils.paths import get_state_dir

log = logging.getLogger(__name__)

_LOCK_TIMEOUT = 15.0
_RETRIES = 5


def _pool_path(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir) / "ip_pools.json"
    return get_state_dir() / "ip_pools.json"


# ---------------------------------------------------------------------------
# cross-process locking
# ---------------------------------------------------------------------------

def _lock_shared_impl():  # pragma: no cover - platform dispatch helper
    try:
        import fcntl  # type: ignore

        return "posix"
    except ImportError:
        import msvcrt  # type: ignore

        return msvcrt


def _acquire(f) -> None:
    impl = _lock_shared_impl()
    if impl == "posix":
        impl.flock(f.fileno(), impl.LOCK_EX)
        return
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while True:
        try:
            f.seek(0)
            impl.locking(f.fileno(), impl.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _release(f) -> None:
    impl = _lock_shared_impl()
    if impl == "posix":
        try:
            impl.flock(f.fileno(), impl.LOCK_UN)
        except Exception:
            pass
        return
    try:
        f.seek(0)
        impl.locking(f.fileno(), impl.LK_UNLCK, 1)
    except Exception:
        pass


@contextmanager
def _pool_lock(pool_file: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on a stable per-pool lock file."""
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(pool_file) + ".lock")
    with open(lock_path, "a+") as f:
        _acquire(f)
        try:
            yield
        finally:
            _release(f)


# ---------------------------------------------------------------------------
# load / save (callers must hold the pool lock for read-modify-write)
# ---------------------------------------------------------------------------

def _load_pools_unlocked(p: Path) -> dict:
    if not p.exists():
        return {"allocated": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("allocated", []), list):
            raise ValueError("unexpected structure")
        return data
    except Exception as e:
        backup = p.with_suffix(p.suffix + ".corrupt")
        try:
            os.replace(p, backup)
        except Exception:
            backup = None  # type: ignore[assignment]
        raise RuntimeError(
            f"IP pool file {p} is corrupted ({e}); "
            f"refusing to allocate from an unknown pool state."
            + (f" Corrupt file moved to {backup}." if backup else "")
            + " Restore the file or review/remove it manually, then retry."
        ) from e


def _save_pools_unlocked(data: dict, p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".ip_pools.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
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


def _load_pools(state_dir: Path | None = None) -> dict:
    """Convenience read (no mutation). Raises on corruption (fail-closed)."""
    return _load_pools_unlocked(_pool_path(state_dir))


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def allocate_ip(
    cidr: str,
    gateway: str | None = None,
    allocated: list[str] | None = None,
    state_dir: Path | None = None,
) -> Optional[str]:
    """Return the next free IPv4 in *cidr*, or None when exhausted.

    Raises ValueError on invalid/non-IPv4 cidr and RuntimeError when the
    persisted pool file is unreadable.
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"invalid ipPool.cidr {cidr!r}: {e}") from e
    if net.version != 4:
        raise ValueError(f"only IPv4 pools are supported, got {cidr!r}")
    if net.prefixlen >= 31:
        # /32 has no usable hosts; /31 yields both addresses via hosts()
        if net.prefixlen == 32:
            raise ValueError(f"cidr {cidr} is /32 (no usable host addresses)")

    taken = set(allocated or [])
    taken.update(_load_pools(state_dir).get("allocated", []))
    if gateway:
        gw = str(ipaddress.ip_address(gateway))  # normalize (also validates)
        if ipaddress.ip_address(gw) not in net:
            log.warning("gateway %s is outside ipPool.cidr %s", gateway, cidr)
        taken.add(gw)
    for ip in net.hosts():
        s = str(ip)
        if s not in taken:
            return s
    return None


def persist_allocate(ip: str, state_dir: Path | None = None) -> None:
    """Durably record a lease. Idempotent; raises RuntimeError on failure."""
    p = _pool_path(state_dir)
    last_err: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            with _pool_lock(p):
                data = _load_pools_unlocked(p)
                if ip in data.get("allocated", []):
                    return
                data.setdefault("allocated", []).append(ip)
                _save_pools_unlocked(data, p)
                if ip in _load_pools_unlocked(p).get("allocated", []):
                    return
                last_err = RuntimeError("post-write verification did not see the lease")
        except Exception as e:
            last_err = e
        time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"failed to persist IP allocation {ip} after {_RETRIES} attempts: {last_err}")


def persist_release(ip: str, state_dir: Path | None = None) -> None:
    """Remove a lease. Idempotent (releasing an unknown IP is a no-op)."""
    p = _pool_path(state_dir)
    last_err: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            with _pool_lock(p):
                data = _load_pools_unlocked(p)
                if ip not in data.get("allocated", []):
                    return
                data["allocated"].remove(ip)
                _save_pools_unlocked(data, p)
                return
        except Exception as e:
            last_err = e
        time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"failed to persist IP release {ip} after {_RETRIES} attempts: {last_err}")

"""Simple IP pool allocator (CIDR-based)."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Optional

DEFAULT_POOL_FILE = Path("state/ip_pools.json")


def _pool_path(state_dir: Path | None = None) -> Path:
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
    except Exception:
        return {"allocated": []}


def _save_pools(data: dict, state_dir: Path | None = None) -> None:
    p = _pool_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def allocate_ip(cidr: str, gateway: str | None = None, allocated: list[str] | None = None, state_dir: Path | None = None) -> str | None:
    """Allocate next free IP from CIDR, skipping gateway/network/broadcast and already allocated."""
    net = ipaddress.ip_network(cidr, strict=False)
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
    data = _load_pools(state_dir)
    if ip not in data.get("allocated", []):
        data.setdefault("allocated", []).append(ip)
        _save_pools(data, state_dir)


def persist_release(ip: str, state_dir: Path | None = None) -> None:
    data = _load_pools(state_dir)
    if ip in data.get("allocated", []):
        data["allocated"].remove(ip)
        _save_pools(data, state_dir)

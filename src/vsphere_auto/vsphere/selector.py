"""Auto resource selection scoring."""
from __future__ import annotations

from typing import Any


def select_cluster(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    clusters = inventory.get("clusters", [])
    if not clusters:
        return None
    if preferred and preferred != "auto":
        for c in clusters:
            if c["name"] == preferred:
                return c
        return None
    # auto: pick cluster with most hosts (simple heuristic)
    return max(clusters, key=lambda c: c.get("hostCount", 0))


def select_datastore(inventory: dict[str, Any], preferred: str | None = None, required_gb: int | None = None) -> dict[str, Any] | None:
    datastores = [d for d in inventory.get("datastores", []) if d.get("accessible")]
    if not datastores:
        return None
    if preferred and preferred != "auto":
        for d in datastores:
            if d["name"] == preferred:
                return d
        return None
    # filter by space if required
    if required_gb:
        need = required_gb * 1024**3
        candidates = [d for d in datastores if d.get("freeSpace", 0) >= need]
        if candidates:
            datastores = candidates
    # auto: most free space
    return max(datastores, key=lambda d: d.get("freeSpace", 0))


def select_network(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    nets = inventory.get("networks", [])
    if not nets:
        return None
    if preferred and preferred != "auto":
        for n in nets:
            if n["name"] == preferred:
                return n
        return None
    return nets[0] if nets else None


def select_folder(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    folders = inventory.get("folders", [])
    if not folders:
        return None
    if preferred and preferred != "auto":
        for f in folders:
            if f["name"] == preferred or f["name"] == preferred.split("/")[-1]:
                return f
        return None
    return folders[0] if folders else None


def auto_select_all(inventory: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return auto-selected resources dict for plan preview."""
    cfg = cfg or {}
    vc = cfg.get("vcenter", {})
    return {
        "cluster": select_cluster(inventory, vc.get("cluster")),
        "datastore": select_datastore(inventory, vc.get("datastore")),
        "network": select_network(inventory, vc.get("network")),
        "folder": select_folder(inventory, (cfg.get("defaults") or {}).get("folder")),
    }

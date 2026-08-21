"""Auto resource selection scoring."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def select_cluster(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    clusters = inventory.get("clusters", [])
    if not clusters:
        return None
    if preferred and preferred != "auto":
        pref = preferred.strip()
        for c in clusters:
            if c.get("name", "").strip() == pref:
                return c
        return None
    # auto: pick cluster with most hosts (simple heuristic)
    return max(clusters, key=lambda c: int(c.get("hostCount", 0) or 0))


def select_datastore(inventory: dict[str, Any], preferred: str | None = None, required_gb: int | None = None) -> dict[str, Any] | None:
    def _free(d: dict[str, Any]) -> int:
        try:
            return int(d.get("freeSpace", 0) or 0)
        except (TypeError, ValueError):
            return 0

    datastores = [d for d in inventory.get("datastores", []) if d.get("accessible")]
    if not datastores:
        return None
    if preferred and preferred != "auto":
        pref = preferred.strip()
        for d in datastores:
            if d.get("name", "").strip() == pref:
                # still validate capacity if required
                if required_gb and _free(d) < required_gb * 1024**3:
                    raise ValueError(f"Datastore {pref!r} has insufficient free space for {required_gb}GB")
                return d
        return None
    # filter by space if required
    if required_gb:
        need = required_gb * 1024**3
        candidates = [d for d in datastores if _free(d) >= need]
        if not candidates:
            raise ValueError(f"No datastore has {required_gb}GB free (max free: {max(_free(d) for d in datastores) / 1024**3:.1f}GB)")
        datastores = candidates
    # auto: most free space
    return max(datastores, key=_free)


def select_network(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    nets = inventory.get("networks", [])
    if not nets:
        return None
    if preferred and preferred != "auto":
        pref = preferred.strip()
        for n in nets:
            if n.get("name", "").strip() == pref:
                return n
        return None
    return nets[0] if nets else None


def select_folder(inventory: dict[str, Any], preferred: str | None = None) -> dict[str, Any] | None:
    folders = inventory.get("folders", [])
    if not folders:
        return None
    if preferred and preferred != "auto":
        pref = preferred.strip()
        # Prefer exact full-path match first, then leaf name as fallback
        for f in folders:
            if f.get("name", "").strip() == pref:
                return f
        # Also support "/DC/vm/folder" style — compare leaf
        leaf = pref.split("/")[-1].strip()
        matches = [f for f in folders if f.get("name", "").strip() == leaf]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log.warning("select_folder: ambiguous leaf %r matches %d folders, picking first", leaf, len(matches))
            return matches[0]
        return None
    return folders[0] if folders else None


def auto_select_all(inventory: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return auto-selected resources dict for plan preview."""
    cfg = cfg or {}
    vc = cfg.get("vcenter", {})
    # required_gb for datastore: take max diskGB across vms
    required_gb = None
    try:
        vms = cfg.get("vms") or []
        if vms:
            required_gb = max(int(vm.get("diskGB") or 0) for vm in vms) or None
    except Exception:
        pass
    try:
        ds = select_datastore(inventory, vc.get("datastore"), required_gb=required_gb)
    except ValueError as e:
        log.warning("auto_select_all datastore: %s", e)
        ds = None
    return {
        "cluster": select_cluster(inventory, vc.get("cluster")),
        "datastore": ds,
        "network": select_network(inventory, vc.get("network")),
        "folder": select_folder(inventory, (cfg.get("defaults") or {}).get("folder")),
    }

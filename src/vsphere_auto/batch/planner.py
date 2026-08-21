"""Batch planner: expand VMs, assign IPs, idempotency hash."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from typing import Any

from ..net.ippool import allocate_ip, persist_allocate

log = logging.getLogger(__name__)


def spec_hash(spec: dict[str, Any]) -> str:
    # Exclude internal keys (_*) at top level and _specHash inside networks
    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items() if not k.startswith("_")}
        if isinstance(obj, list):
            return [_clean(x) for x in obj]
        return obj

    normalized = json.dumps(_clean(spec), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def expand_batch(cfg: dict[str, Any], persist: bool = False) -> list[dict[str, Any]]:
    """Expand cfg['vms'] with naming template and IP auto allocation.

    If persist=True, auto-allocated IPs are persisted to state/ip_pools.json.
    Plan/dry-run must use persist=False (default) to avoid consuming IPs.
    """
    vms = cfg.get("vms", [])
    batch_cfg = cfg.get("batch") or {}
    naming = batch_cfg.get("naming")
    ip_pool = cfg.get("ipPool") or {}

    # If vms empty but count provided, generate
    if not vms and cfg.get("count") is not None:
        try:
            count = int(cfg["count"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid count: {cfg.get('count')!r}: {e}") from e
        if count < 0:
            raise ValueError(f"count must be >=0, got {count}")
        if count > 1000:
            raise ValueError(f"count too large (max 1000): {count}")
        template = cfg.get("template") or (cfg.get("defaults") or {}).get("template") or ""
        vms = []
        for i in range(1, count + 1):
            try:
                name = naming.format(index=i) if naming and "{index" in naming else f"vm-{i:02d}"
            except KeyError as e:
                raise ValueError(f"Invalid naming template {naming!r}: missing key {e}") from e
            except Exception as e:
                raise ValueError(f"Invalid naming template {naming!r}: {e}") from e
            vms.append({"name": name, "template": template})

    expanded: list[dict[str, Any]] = []
    allocated: list[str] = []
    cidr = ip_pool.get("cidr")
    gateway = ip_pool.get("gateway")

    for vm in vms:
        vm = copy.deepcopy(vm)
        # auto naming if missing
        if not vm.get("name") and naming:
            try:
                vm["name"] = naming.format(index=len(expanded) + 1)
            except KeyError as e:
                raise ValueError(f"Invalid naming template {naming!r}: missing key {e}") from e
            except Exception as e:
                raise ValueError(f"Invalid naming template {naming!r}: {e}") from e
        # IP auto
        for net in vm.get("networks", []) or []:
            if net.get("ip") in ("auto", None, "") and cidr:
                ip = allocate_ip(cidr, gateway, allocated)
                if ip:
                    net["ip"] = ip
                    allocated.append(ip)
                    if persist:
                        try:
                            persist_allocate(ip)
                        except Exception as e:
                            log.warning("persist_allocate %s failed: %s", ip, e)
                else:
                    log.warning("IP pool exhausted for %s (cidr=%s)", vm.get("name"), cidr)
        vm["_specHash"] = spec_hash({k: v for k, v in vm.items() if not k.startswith("_")})
        expanded.append(vm)
    return expanded

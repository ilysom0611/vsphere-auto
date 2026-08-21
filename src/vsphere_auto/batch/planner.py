"""Batch planner: expand VMs, assign IPs, idempotency hash."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..net.ippool import allocate_ip


def spec_hash(spec: dict[str, Any]) -> str:
    normalized = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def expand_batch(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand cfg['vms'] with naming template and IP auto allocation."""
    vms = cfg.get("vms", [])
    batch_cfg = cfg.get("batch") or {}
    naming = batch_cfg.get("naming")
    ip_pool = cfg.get("ipPool") or {}

    # If vms empty but count provided, generate
    if not vms and cfg.get("count"):
        count = int(cfg["count"])
        template = cfg.get("template") or (cfg.get("defaults") or {}).get("template") or ""
        vms = []
        for i in range(1, count + 1):
            name = naming.format(index=i) if naming and "{index" in naming else f"vm-{i:02d}"
            vms.append({"name": name, "template": template})

    expanded: list[dict[str, Any]] = []
    allocated: list[str] = []
    cidr = ip_pool.get("cidr")
    gateway = ip_pool.get("gateway")

    for vm in vms:
        vm = dict(vm)
        # auto naming if missing
        if not vm.get("name") and naming:
            vm["name"] = naming.format(index=len(expanded) + 1)
        # IP auto
        for net in vm.get("networks", []):
            if net.get("ip") in ("auto", None, "") and cidr:
                ip = allocate_ip(cidr, gateway, allocated)
                if ip:
                    net["ip"] = ip
                    allocated.append(ip)
        vm["_specHash"] = spec_hash({k: v for k, v in vm.items() if not k.startswith("_")})
        expanded.append(vm)
    return expanded

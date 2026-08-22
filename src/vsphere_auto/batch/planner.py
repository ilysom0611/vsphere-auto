"""Batch planner: expand VMs, assign IPs, idempotency hash."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from typing import Any

from ..net.ippool import allocate_ip, persist_allocate, persist_release

log = logging.getLogger(__name__)

_RFC1123_LABEL = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

# Per-VM spec keys recognised at config top level and in defaults — a count-
# generated batch inherits these; explicit vms[] entries inherit them from
# defaults for any key they do not set themselves.
_VM_SPEC_KEYS = (
    "template", "iso", "cpu", "memoryMB", "diskGB", "networks",
    "folder", "guestId", "provisioning", "resourcePool",
)


def validate_hostname(name: str) -> None:
    """Guest hostnames must be single RFC1123 labels (vCenter LinuxPrep rejects
    FQDNs/underscores with a confusing mid-task InvalidArgument)."""
    if not _RFC1123_LABEL.fullmatch(name or ""):
        raise ValueError(
            f"invalid VM name/hostname {name!r}: must be 1-63 chars of letters, digits "
            f"or '-', starting and ending with a letter or digit (no dots/underscores)"
        )


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


def _format_name(naming: str | None, index: int) -> str:
    """Apply a naming template; a template without {index} is an error, not a silent fallback."""
    if not naming:
        return f"vm-{index:02d}"
    try:
        name = naming.format(index=index)
    except (KeyError, IndexError) as e:
        raise ValueError(f"naming template {naming!r} must contain {{index}}: {e}") from e
    except Exception as e:
        raise ValueError(f"invalid naming template {naming!r}: {e}") from e
    return name


def _release_ips(ips: list[str]) -> None:
    for ip in reversed(ips):
        try:
            persist_release(ip)
        except Exception as e:
            log.error("rollback: failed to release IP %s — manual pool cleanup may be needed: %s", ip, e)


def expand_batch(cfg: dict[str, Any], persist: bool = False) -> list[dict[str, Any]]:
    """Expand cfg['vms'] with naming template and IP auto allocation.

    If persist=True, auto-allocated IPs are persisted to state/ip_pools.json.
    Plan/dry-run must use persist=False (default) to avoid consuming IPs.

    With persist=True the function is all-or-nothing on IP leases: any failure
    after some leases were taken releases them again before raising.
    """
    vms = cfg.get("vms", [])
    batch_cfg = cfg.get("batch") or {}
    naming = batch_cfg.get("naming")
    ip_pool = cfg.get("ipPool") or {}
    defaults = cfg.get("defaults") or {}

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
        # carry the top-level spec (cpu/memoryMB/diskGB/networks/iso/...) into
        # every generated VM — previously only name+template survived, so a
        # count-based request silently deployed 2 CPU/4 GB template defaults.
        base_spec = {k: copy.deepcopy(v) for k, v in cfg.items() if k in _VM_SPEC_KEYS}
        if template:
            base_spec["template"] = template
        vms = []
        for i in range(1, count + 1):
            entry = copy.deepcopy(base_spec)
            entry["name"] = _format_name(naming, i)
            vms.append(entry)

    expanded: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    allocated_local: list[str] = []     # every lease handed out during this call
    allocated_persisted: list[str] = []  # leases already written to disk
    explicit_ips: list[str] = []        # user-declared static IPs (must not be reused)
    cidr = ip_pool.get("cidr")
    gateway = ip_pool.get("gateway")

    try:
        for vm in vms:
            vm = copy.deepcopy(vm)
            # fill unset spec keys from defaults (explicit vms[] entries keep
            # their own values; count-generated entries already carry them)
            for key in _VM_SPEC_KEYS:
                if key not in vm and key in (defaults or {}):
                    vm[key] = copy.deepcopy(defaults[key])
            # hash source BEFORE IP injection: auto-assigned IPs must not enter
            # the spec hash (pool drift would break idempotency), explicit
            # user-declared IPs stay included.
            base_vm = copy.deepcopy({k: v for k, v in vm.items() if not k.startswith("_")})
            for n in base_vm.get("networks", []) or []:
                if isinstance(n.get("ip"), str) and n["ip"]:
                    explicit_ips.append(n["ip"])
            # auto naming if missing
            if not vm.get("name"):
                vm["name"] = _format_name(naming, len(expanded) + 1)
            name = vm["name"]
            if name in seen_names:
                raise ValueError(
                    f"duplicate VM name {name!r} produced by expansion — "
                    f"naming templates must include {{index}} when generating multiple VMs"
                )
            validate_hostname(name)
            seen_names.add(name)

            for net in vm.get("networks", []) or []:
                if net.get("ip") in ("auto", None, "") and cidr:
                    ip = allocate_ip(cidr, gateway, allocated_local + explicit_ips)
                    if ip is None:
                        if persist:
                            raise ValueError(
                                f"IP pool exhausted for cidr {cidr} (while planning {name!r}) — "
                                f"free addresses or shrink count"
                            )
                        log.warning("IP pool exhausted for %s (cidr=%s)", name, cidr)
                        continue
                    net["ip"] = ip
                    allocated_local.append(ip)
                    if persist:
                        persist_allocate(ip)
                        allocated_persisted.append(ip)
            vm["_specHash"] = spec_hash(base_vm)
            expanded.append(vm)
    except Exception:
        if allocated_persisted:
            log.warning("rolling back %d persisted IP lease(s) after planning failure", len(allocated_persisted))
            _release_ips(allocated_persisted)
        raise

    return expanded

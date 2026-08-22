"""Deploy API: plan + deploy (batch)."""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from ...batch.planner import expand_batch
from ...batch.state import create_batch_auto, update_batch_status
from ...inventory import load_inventory_any
from ...net.ippool import persist_release
from ...vsphere.selector import auto_select_all

log = logging.getLogger(__name__)

bp = Blueprint("deploy_api", __name__)

# Resource bounds for request validation.
MAX_VMS = 500
CPU_MIN, CPU_MAX = 1, 256
MEM_MIN, MEM_MAX = 128, 4_194_304
DISK_MIN, DISK_MAX = 1, 62000
PORT_MIN, PORT_MAX = 1, 65535


def _validation_error(msg: str):
    return jsonify({"error": msg, "detail_category": "validation"}), 400


def _classify(e: BaseException) -> str:
    """Map an unexpected exception to a coarse, non-leaky category."""
    s = f"{type(e).__name__}: {e}".lower()
    if "auth" in s or "permission" in s or "login" in s or "unauthorized" in s or "invalidcredential" in s:
        return "auth"
    if any(t in s for t in ("connection", "timeout", "timed out", "refused", "unreachable", "socket", "ssl", "dns", "resolve")):
        return "connection"
    return "validation" if isinstance(e, ValueError) else "connection"


def _internal_error(e: BaseException, action: str = "deployment failed"):
    log.exception("%s: unexpected error", action)
    return jsonify({"error": action, "detail_category": _classify(e)}), 500


def _parse_positive_int(value: Any, field: str, lo: int, hi: int) -> int | None:
    """Return the validated int, None if not provided, or raise ValueError."""
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    if not (lo <= n <= hi):
        raise ValueError(f"{field} must be between {lo} and {hi}, got {n}")
    return n


def _validate_vms(cfg: dict[str, Any]) -> str | None:
    """Static request validation. Returns an error message or None."""
    vms = cfg.get("vms")
    if vms is not None:
        if not isinstance(vms, list):
            return "vms must be an array"
        if len(vms) > MAX_VMS:
            return f"too many VMs: {len(vms)} (max {MAX_VMS})"
        for i, vm in enumerate(vms):
            if not isinstance(vm, dict):
                return f"vms[{i}] must be an object"
            label = vm.get("name") or f"vms[{i}]"
            try:
                _parse_positive_int(vm.get("cpu"), f"{label}: cpu", CPU_MIN, CPU_MAX)
                _parse_positive_int(vm.get("memoryMB"), f"{label}: memoryMB", MEM_MIN, MEM_MAX)
                _parse_positive_int(vm.get("diskGB"), f"{label}: diskGB", DISK_MIN, DISK_MAX)
            except ValueError as e:
                return str(e)
    vc = cfg.get("vcenter") or {}
    if vc.get("port") is not None:
        try:
            _parse_positive_int(vc.get("port"), "vcenter.port", PORT_MIN, PORT_MAX)
        except ValueError as e:
            return str(e)
    return None


def _auto_consumed_ips(orig_vms: list, expanded_vms: list) -> list[str]:
    """IPs allocated by expansion: present on expanded VMs but auto/empty in the
    original cfg (user-declared static IPs are never released here)."""
    explicit: set[str] = set()
    for vm in orig_vms:
        if not isinstance(vm, dict):
            continue
        for n in vm.get("networks") or []:
            ip = (n or {}).get("ip")
            if isinstance(ip, str) and ip and ip not in ("auto", "dhcp"):
                explicit.add(ip)
    consumed: list[str] = []
    seen: set[str] = set()
    for vm in expanded_vms:
        for n in vm.get("networks") or []:
            ip = (n or {}).get("ip")
            if isinstance(ip, str) and ip and ip not in ("auto", "dhcp") and ip not in explicit and ip not in seen:
                seen.add(ip)
                consumed.append(ip)
    return consumed


def _release_leases(ips: list[str]) -> None:
    for ip in ips:
        try:
            persist_release(ip)
        except Exception as e:
            log.error("rollback: failed to release IP %s — manual pool cleanup may be needed: %s", ip, e)


@bp.post("/api/plan")
def plan_api():
    data = request.get_json(force=True) or {}
    # cfg can be inline or from body
    cfg = data.get("config") or data
    err = _validate_vms(cfg)
    if err:
        return _validation_error(err)
    # auto-select preview
    inv = load_inventory_any()
    selection = auto_select_all(inv or {}, cfg) if inv else {}
    try:
        vms = expand_batch(cfg)
    except ValueError as e:
        return _validation_error(str(e))
    except Exception as e:
        return _internal_error(e, "planning failed")
    return jsonify({"selection": selection, "vms": vms, "count": len(vms)})


@bp.post("/api/deploy")
def deploy_api():
    try:
        return _deploy_impl()
    except ValueError as e:
        return _validation_error(str(e))
    except Exception as e:
        return _internal_error(e)


def _deploy_impl():
    data = request.get_json(force=True) or {}
    cfg = data.get("config") or data

    err = _validate_vms(cfg)
    if err:
        return _validation_error(err)

    orig_vms = cfg.get("vms") or []
    try:
        vms = expand_batch(cfg, persist=True)
    except ValueError as e:
        return _validation_error(str(e))
    # expand_batch rolls back its own leases when it raises; leases leak only
    # when expansion succeeds and a LATER step rejects the request — release
    # the newly-consumed IPs on every early-failure path below.
    consumed = _auto_consumed_ips(orig_vms, vms)
    try:
        return _deploy_after_expand(cfg, vms, consumed)
    except Exception:
        _release_leases(consumed)
        raise


def _deploy_after_expand(cfg: dict[str, Any], vms: list, consumed: list[str]):
    if not vms:
        raise ValueError("No VMs to deploy (vms empty)")

    # Resolve connection FIRST (reuse config resolver when possible) so a
    # validation failure returns 400 without leaving a phantom pending batch
    # row behind.
    creds_ref = (cfg.get("vcenter") or {}).get("credsRef")
    vc_raw = cfg.get("vcenter") or {}
    host = vc_raw.get("host")
    user = vc_raw.get("user") or vc_raw.get("username")
    pwd = vc_raw.get("password")
    # Track whether port was explicitly set in payload (mirrors config._raw_has_port)
    has_port = "port" in vc_raw and vc_raw.get("port") is not None
    try:
        port = int(vc_raw.get("port") or 443)
    except (TypeError, ValueError):
        port = 443
    datacenter = (cfg.get("vcenter") or {}).get("datacenter")
    vc_cluster = (cfg.get("vcenter") or {}).get("cluster")
    vc_datastore = (cfg.get("vcenter") or {}).get("datastore")
    defaults = cfg.get("defaults") or {}
    batch_cfg = cfg.get("batch") or {}
    try:
        concurrency = int(batch_cfg.get("concurrency") or 5)
    except (TypeError, ValueError):
        concurrency = 5
    on_error = batch_cfg.get("onError") or "continue"

    # resolve credsRef if present
    if creds_ref:
        from ...creds.store import resolve_creds

        c = resolve_creds(str(creds_ref))
        if not c:
            raise ValueError(f"credential not found: {creds_ref}")
        orig_host = (host or "").strip()
        host = orig_host or c.host
        user = (user or "").strip() or c.username
        pwd = pwd or c.decrypted_password()
        # Only keep JSON port if explicitly set and host was overridden;
        # otherwise use the saved credential's port.
        if not has_port or not orig_host:
            port = c.port
        # keep parsed port when has_port and orig_host truthy
    else:
        import os

        if not pwd:
            pwd = os.environ.get("VSPHERE_PASSWORD", "")

    if not (host or "").strip() or not (user or "").strip():
        raise ValueError("vCenter host/user not resolved (set vcenter.host/user or credsRef)")

    # Allocate the batch row atomically (redacts secrets before persisting) —
    # only after all request validation above has passed.
    batch_id = create_batch_auto(cfg)

    # For non-blocking: run in background thread and return batch_id immediately.
    import threading

    from ...batch.executor import run_batch
    from ...vsphere.client import connect, disconnect

    def deploy_one(vm: dict):
        from .. import progress

        template = vm.get("template")
        iso = vm.get("iso")
        name = vm.get("name")

        def step(key: str, detail: str = "") -> None:
            progress.report(batch_id, name, key, detail)
        cpu_raw = vm.get("cpu")
        mem_raw = vm.get("memoryMB")
        cpu = int(cpu_raw) if cpu_raw is not None else 2
        mem = int(mem_raw) if mem_raw is not None else 4096
        disk = vm.get("diskGB")
        folder = vm.get("folder") or defaults.get("folder")
        guest_id = vm.get("guestId") or defaults.get("guestId") or "ubuntu64Guest"
        nets = vm.get("networks") or []
        ip = None
        netmask = None
        gateway = None
        network_name = None
        if nets:
            n0 = nets[0]
            ip = n0.get("ip")
            network_name = n0.get("network") if n0.get("network") != "auto" else None
        ippool = cfg.get("ipPool") or {}
        if ip and ip not in ("dhcp", "auto"):
            gateway = ippool.get("gateway")
            netmask = ippool.get("netmask")
        # Build multi-NIC customization if multiple networks
        custom = None
        nics = None
        if nets and len(nets) > 1:
            nics = []
            for n in nets:
                _ip = n.get("ip")
                if _ip == "auto":
                    _ip = None
                nics.append({"ip": _ip, "netmask": ippool.get("netmask"), "gateway": ippool.get("gateway")})
        if (ip and ip not in ("auto", "dhcp")) or nics is not None:
            # Validation errors (bad hostname, missing netmask for static IP)
            # MUST propagate as ValueError -> HTTP 400. Silently deploying with
            # DHCP instead of the requested static IP is worse than failing.
            from ...vsphere.customization import build_linux_customization

            if nics is not None:
                custom = build_linux_customization(hostname=name, domain="", dns=ippool.get("dns"), nics=nics)
            else:
                custom = build_linux_customization(
                    hostname=name, domain="", dns=ippool.get("dns"), ip=ip if ip != "dhcp" else None, netmask=netmask, gateway=gateway
                )

        si = None
        try:
            step("conn")
            si = connect(host, port, user, pwd)
            from ...vsphere.deploy import find_vm, clone_from_template, create_vm_from_iso

            content = si.RetrieveContent()
            existing = find_vm(content, name, folder)
            if existing is not None:
                return {"ok": True, "skipped": True, "moid": getattr(existing, "_moId", "")}
            if existing is not None:
                return {"ok": True, "skipped": True, "moid": getattr(existing, "_moId", "")}

            if template:
                step("tpl", template or "")
                task = clone_from_template(
                    si,
                    template_name=template,
                    vm_name=name,
                    datacenter=datacenter,
                    cluster=vc_cluster if vc_cluster != "auto" else None,
                    datastore=vc_datastore if vc_datastore != "auto" else None,
                    folder_name=folder,
                    resource_pool=vm.get("resourcePool"),
                    cpu=cpu,
                    memory_mb=mem,
                    disk_gb=int(disk) if disk is not None else None,
                    customization_spec=custom,
                    network=network_name,
                    provisioning=vm.get("provisioning"),
                    host=vm.get("host"),
                )
                from ...vsphere.tasks import wait_for_task

                def _clone_progress(info):
                    pct = getattr(info, "progress", None)
                    desc = getattr(info, "descriptionId", "") or ""
                    step("clone", f"{pct}" if pct is not None else desc)

                step("clone", "0")
                res = wait_for_task(task, timeout=1800, on_poll=_clone_progress)
                if res["state"] == "success":
                    if custom is not None:
                        step("custom")
                    return {"ok": True, "moid": str(getattr(res.get("result"), "_moId", "")) if res.get("result") else ""}
                return {"ok": False, "error": res.get("error") or "Clone failed"}
            elif iso:
                step("iso", iso or "")
                ds_name = vc_datastore if vc_datastore and vc_datastore != "auto" else None
                iso_path = iso
                if iso_path.startswith("["):
                    try:
                        ds_name = iso_path.split("]")[0].lstrip("[")
                    except Exception:
                        pass
                if not ds_name:
                    return {"ok": False, "error": "Datastore required for ISO deploy", "errorKey": "ds_missing"}
                vm_obj = create_vm_from_iso(si, name, ds_name, iso_path, guest_id, cpu, mem, int(disk or 40), network_name, folder, datacenter, customization_spec=custom, host_name=vm.get("host"))
                return {"ok": True, "moid": getattr(vm_obj, "_moId", "") if vm_obj else ""}
            else:
                return {"ok": False, "error": "Either template or iso required", "errorKey": "tpl_missing"}
        finally:
            if si is not None:
                disconnect(si)

    def _bg():
        from .. import progress

        try:
            run_batch(batch_id, vms, deploy_one, concurrency=concurrency, on_error=on_error)
        except Exception:
            # Belt-and-braces: run_batch normally records failures itself, but
            # never leave a batch stuck in running/pending if anything escapes.
            log.exception("batch %s: background execution failed", batch_id)
            try:
                update_batch_status(batch_id, "failed")
            except Exception:
                log.exception("batch %s: could not mark failed after background error", batch_id)
        finally:
            progress.drop_batch(batch_id)

    th = threading.Thread(target=_bg, daemon=True)
    th.start()

    return jsonify({"batch_id": batch_id, "count": len(vms), "status": "running"}), 202

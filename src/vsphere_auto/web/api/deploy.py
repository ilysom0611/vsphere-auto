"""Deploy API: plan + deploy (batch)."""
from __future__ import annotations

import logging
import uuid
from flask import Blueprint, jsonify, request

from ...batch.planner import expand_batch
from ...batch.state import create_batch, next_batch_id
from ...inventory import load_inventory_any
from ...vsphere.selector import auto_select_all

log = logging.getLogger(__name__)

bp = Blueprint("deploy_api", __name__)


@bp.post("/api/plan")
def plan_api():
    data = request.get_json(force=True) or {}
    # cfg can be inline or from body
    cfg = data.get("config") or data
    # auto-select preview
    inv = load_inventory_any()
    selection = auto_select_all(inv or {}, cfg) if inv else {}
    try:
        vms = expand_batch(cfg)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"selection": selection, "vms": vms, "count": len(vms)})


@bp.post("/api/deploy")
def deploy_api():
    data = request.get_json(force=True) or {}
    cfg = data.get("config") or data
    try:
        vms = expand_batch(cfg)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not vms:
        return jsonify({"error": "No VMs to deploy (vms empty)"}), 400

    batch_id = next_batch_id()
    create_batch(batch_id, cfg)

    # Resolve connection — reuse config resolver when possible
    creds_ref = (cfg.get("vcenter") or {}).get("credsRef")
    host = (cfg.get("vcenter") or {}).get("host")
    user = (cfg.get("vcenter") or {}).get("user") or (cfg.get("vcenter") or {}).get("username")
    pwd = (cfg.get("vcenter") or {}).get("password")
    port = int((cfg.get("vcenter") or {}).get("port") or 443)
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
        if c:
            host = (host or "").strip() or c.host
            user = (user or "").strip() or c.username
            pwd = pwd or c.decrypted_password()
            port = port if (host or "").strip() else c.port
    else:
        import os

        if not pwd:
            pwd = os.environ.get("VSPHERE_PASSWORD", "")

    if not (host or "").strip() or not (user or "").strip():
        return jsonify({"error": "vCenter host/user not resolved (set vcenter.host/user or credsRef)"}), 400

    # For non-blocking: run in background thread and return batch_id immediately.
    import threading

    from ...batch.executor import run_batch
    from ...vsphere.client import connect, disconnect

    def deploy_one(vm: dict):
        template = vm.get("template")
        iso = vm.get("iso")
        name = vm.get("name")
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
        if (ip and ip not in ("auto",)) or nics is not None:
            try:
                from ...vsphere.customization import build_linux_customization

                if nics is not None:
                    custom = build_linux_customization(hostname=name, domain="", dns=ippool.get("dns"), nics=nics)
                else:
                    custom = build_linux_customization(
                        hostname=name, domain="", dns=ippool.get("dns"), ip=ip if ip != "dhcp" else None, netmask=netmask, gateway=gateway
                    )
            except Exception as e:
                log.warning("build_linux_customization for %s failed: %s", name, e)
                custom = None

        si = None
        try:
            si = connect(host, port, user, pwd)
            from ...vsphere.deploy import find_vm, clone_from_template, create_vm_from_iso

            content = si.RetrieveContent()
            existing = find_vm(content, name, folder)
            if existing is not None:
                return {"ok": True, "skipped": True, "moid": getattr(existing, "_moId", "")}

            if template:
                task = clone_from_template(
                    si,
                    template_name=template,
                    vm_name=name,
                    datacenter=datacenter,
                    cluster=vc_cluster if vc_cluster != "auto" else None,
                    datastore=vc_datastore if vc_datastore != "auto" else None,
                    folder_name=folder,
                    cpu=cpu,
                    memory_mb=mem,
                    disk_gb=int(disk) if disk is not None else None,
                    customization_spec=custom,
                )
                from ...vsphere.tasks import wait_for_task

                res = wait_for_task(task, timeout=1800)
                if res["state"] == "success":
                    return {"ok": True, "moid": str(getattr(res.get("result"), "_moId", "")) if res.get("result") else ""}
                return {"ok": False, "error": res.get("error") or "Clone failed"}
            elif iso:
                ds_name = vc_datastore if vc_datastore and vc_datastore != "auto" else None
                iso_path = iso
                if iso_path.startswith("["):
                    try:
                        ds_name = iso_path.split("]")[0].lstrip("[")
                    except Exception:
                        pass
                if not ds_name:
                    return {"ok": False, "error": "Datastore required for ISO deploy"}
                vm_obj = create_vm_from_iso(si, name, ds_name, iso_path, guest_id, cpu, mem, int(disk or 40), network_name, folder, datacenter)
                return {"ok": True, "moid": getattr(vm_obj, "_moId", "") if vm_obj else ""}
            else:
                return {"ok": False, "error": "Either template or iso required"}
        finally:
            if si is not None:
                disconnect(si)

    def _bg():
        run_batch(batch_id, vms, deploy_one, concurrency=concurrency, on_error=on_error)

    th = threading.Thread(target=_bg, daemon=True)
    th.start()

    return jsonify({"batch_id": batch_id, "count": len(vms), "status": "running"}), 202

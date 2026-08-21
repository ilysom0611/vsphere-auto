"""Deploy API: plan + deploy (batch)."""
from __future__ import annotations

import uuid
from flask import Blueprint, jsonify, request

from ...batch.planner import expand_batch
from ...batch.state import create_batch, next_batch_id
from ...inventory import load_inventory_any
from ...vsphere.selector import auto_select_all

bp = Blueprint("deploy_api", __name__)


@bp.post("/api/plan")
def plan_api():
    data = request.get_json(force=True) or {}
    # cfg can be inline or from body
    cfg = data.get("config") or data
    # auto-select preview
    inv = load_inventory_any()
    selection = auto_select_all(inv or {}, cfg) if inv else {}
    vms = expand_batch(cfg)
    return jsonify({"selection": selection, "vms": vms, "count": len(vms)})


@bp.post("/api/deploy")
def deploy_api():
    data = request.get_json(force=True) or {}
    cfg = data.get("config") or data
    vms = expand_batch(cfg)
    if not vms:
        return jsonify({"error": "No VMs to deploy (vms empty)"}), 400

    batch_id = next_batch_id()
    create_batch(batch_id, cfg)

    # Resolve connection for deploy function
    # Build deploy_fn that uses vsphere deploy helpers
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
    concurrency = int(batch_cfg.get("concurrency") or 5)
    on_error = batch_cfg.get("onError") or "continue"

    # resolve credsRef if present
    if creds_ref:
        from ...creds.store import resolve_creds

        c = resolve_creds(str(creds_ref))
        if c:
            host = host or c.host
            user = user or c.username
            pwd = pwd or c.decrypted_password()
            port = port if host else c.port
    else:
        # also check direct password via env already handled in config.py, but here we use raw body
        import os

        if not pwd:
            pwd = os.environ.get("VSPHERE_PASSWORD", "")

    if not host or not user:
        return jsonify({"error": "vCenter host/user not resolved (set vcenter.host/user or credsRef)"}), 400

    # For non-blocking: run in background thread and return batch_id immediately.
    # Simple approach: spawn thread
    import threading

    from ...batch.executor import run_batch
    from ...vsphere.client import connect, disconnect

    def deploy_one(vm: dict):
        # per-VM deploy: clone or iso
        template = vm.get("template")
        iso = vm.get("iso")
        name = vm.get("name")
        cpu = int(vm.get("cpu") or 2)
        mem = int(vm.get("memoryMB") or 4096)
        disk = vm.get("diskGB")
        folder = vm.get("folder") or defaults.get("folder")
        guest_id = vm.get("guestId") or defaults.get("guestId") or "ubuntu64Guest"
        # network/ip handling simplified: first network
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
        # customization
        custom = None
        if ip and ip not in ("auto",):
            try:
                from ...vsphere.customization import build_linux_customization

                custom = build_linux_customization(
                    hostname=name, domain="", dns=ippool.get("dns"), ip=ip if ip != "dhcp" else None, netmask=netmask, gateway=gateway
                )
            except Exception:
                custom = None

        si = connect(host, port, user, pwd)
        try:
            # idempotency: check if VM already exists and spec hash matches (handled in executor, but also direct check)
            from ...vsphere.deploy import find_vm, clone_from_template, create_vm_from_iso

            content = si.RetrieveContent()
            existing = find_vm(content, name, folder)
            # if exists, treat as success (idempotent) — executor already handles hash check, but double-check here
            # For now, if exists, return ok without cloning
            if existing is not None:
                # verify spec hash already handled; just return ok
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
                    customization_spec=custom,
                )
                from ...vsphere.tasks import wait_for_task

                res = wait_for_task(task, timeout=1800)
                if res["state"] == "success":
                    return {"ok": True, "moid": str(getattr(res.get("result"), "_moId", "")) if res.get("result") else ""}
                return {"ok": False, "error": res.get("error") or "Clone failed"}
            elif iso:
                # iso_path is datastore path like "[ds] iso/file.iso" or via vm.iso field
                ds_name = vc_datastore if vc_datastore and vc_datastore != "auto" else None
                # try parse iso string
                iso_path = iso
                if iso_path.startswith("["):
                    # extract ds name
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
            disconnect(si)

    def _bg():
        run_batch(batch_id, vms, deploy_one, concurrency=concurrency, on_error=on_error)

    th = threading.Thread(target=_bg, daemon=True)
    th.start()

    return jsonify({"batch_id": batch_id, "count": len(vms), "status": "running"}), 202

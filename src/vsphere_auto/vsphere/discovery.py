"""Discovery: enumerate vSphere inventory via pyVmomi."""
from __future__ import annotations

from typing import Any


def _get_obj(content, vim_type: list, name: str | None = None):
    """Helper to get managed objects via containerView."""
    view = content.viewManager.CreateContainerView(content.rootFolder, vim_type, True)
    objs = list(view.view)
    view.Destroy()
    if name:
        for o in objs:
            if getattr(o, "name", None) == name:
                return o
        return None
    return objs


def discover(si, datacenter: str | None = None) -> dict[str, Any]:
    """Enumerate inventory. Returns dict with datacenters, clusters, hosts, datastores, networks, folders, templates, etc."""
    content = si.RetrieveContent()
    result: dict[str, Any] = {}

    # Datacenters
    try:
        from pyVmomi import vim

        dcs = _get_obj(content, [vim.Datacenter])
        result["datacenters"] = [{"name": dc.name, "moid": dc._moId} for dc in dcs]

        # pick target DC
        target_dc = None
        if datacenter:
            for dc in dcs:
                if dc.name == datacenter:
                    target_dc = dc
                    break
        elif len(dcs) == 1:
            target_dc = dcs[0]

        # Clusters & hosts
        clusters = _get_obj(content, [vim.ClusterComputeResource])
        result["clusters"] = []
        for c in clusters:
            hosts = getattr(c, "host", []) or []
            result["clusters"].append(
                {
                    "name": c.name,
                    "moid": c._moId,
                    "hostCount": len(hosts),
                    "hosts": [{"name": h.name, "moid": h._moId} for h in hosts],
                }
            )

        standalone_hosts = _get_obj(content, [vim.ComputeResource])
        # filter out clusters already counted (ClusterComputeResource is subclass)
        result["standaloneHosts"] = [
            {"name": h.name, "moid": h._moId}
            for h in standalone_hosts
            if type(h).__name__ == "ComputeResource"
        ]

        # Datastores
        datastores = _get_obj(content, [vim.Datastore])
        result["datastores"] = []
        for ds in datastores:
            summary = getattr(ds, "summary", None)
            result["datastores"].append(
                {
                    "name": ds.name,
                    "moid": ds._moId,
                    "capacity": getattr(summary, "capacity", 0) if summary else 0,
                    "freeSpace": getattr(summary, "freeSpace", 0) if summary else 0,
                    "type": getattr(summary, "type", "") if summary else "",
                    "accessible": getattr(summary, "accessible", True) if summary else True,
                }
            )

        # Networks
        networks = _get_obj(content, [vim.Network])
        result["networks"] = [{"name": n.name, "moid": n._moId} for n in networks]

        # Folders
        folders = _get_obj(content, [vim.Folder])
        result["folders"] = [{"name": f.name, "moid": f._moId} for f in folders]

        # Resource pools
        rps = _get_obj(content, [vim.ResourcePool])
        result["resourcePools"] = [{"name": rp.name, "moid": rp._moId} for rp in rps]

        # Templates (VMs where config.template == True)
        vms = _get_obj(content, [vim.VirtualMachine])
        result["templates"] = []
        result["vms"] = []
        for vm in vms:
            cfg = getattr(vm, "config", None)
            is_template = bool(getattr(cfg, "template", False)) if cfg else False
            entry = {
                "name": vm.name,
                "moid": vm._moId,
                "guestId": getattr(cfg, "guestId", "") if cfg else "",
            }
            if is_template:
                result["templates"].append(entry)
            else:
                result["vms"].append(entry)

        # ISO images: scan datastores for *.iso (best-effort, may be slow)
        result["isoImages"] = []
        # We do not auto-scan all datastores by default (expensive). Caller can request iso scan explicitly.
        # Provide a separate function scan_iso_images() for on-demand.

    except Exception as e:
        result["error"] = str(e)

    return result


def scan_iso_images(si, datastore_name: str | None = None) -> list[dict[str, Any]]:
    """Scan datastore(s) for .iso files via Datastore Browser."""
    content = si.RetrieveContent()
    from pyVmomi import vim

    datastores = _get_obj(content, [vim.Datastore])
    if datastore_name:
        datastores = [ds for ds in datastores if ds.name == datastore_name]

    isos: list[dict[str, Any]] = []
    for ds in datastores:
        try:
            browser = ds.browser
            # SearchSpec for *.iso
            spec = vim.host.DatastoreBrowser.SearchSpec()
            spec.matchPattern = ["*.iso"]
            # Use SearchDatastoreSubFolders_Task would be async; use SearchDatastore with simple path
            # datastore path: "[ds] "
            ds_path = f"[{ds.name}] "
            task = browser.SearchDatastoreSubFolders_Task(ds_path, spec)
            # wait
            while task.info.state not in ("success", "error"):
                import time

                time.sleep(0.5)
            if task.info.state == "success" and task.info.result:
                for res in task.info.result:
                    folder = getattr(res, "folderPath", "")
                    for f in getattr(res, "file", []) or []:
                        isos.append(
                            {
                                "datastore": ds.name,
                                "path": f"{folder}{f.path}",
                                "fullPath": f"[{ds.name}] {folder}{f.path}",
                                "size": getattr(f, "fileSize", 0),
                            }
                        )
        except Exception:
            continue
    return isos


def test_connection(host: str, port: int, user: str, password: str) -> dict[str, Any]:
    """Test connection and return datacenter list."""
    from .client import connect, disconnect

    si = connect(host, port, user, password)
    try:
        inv = discover(si)
        return {"ok": True, "datacenters": inv.get("datacenters", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        disconnect(si)

"""Discovery: enumerate vSphere inventory via pyVmomi.

Compatible with vCenter/ESXi 6.7 through 8.0.  Property names and API
availability vary across versions so every access is guarded.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# Per-view timeout for 6.7 large inventories (CreateContainerView can block)
_VIEW_TIMEOUT = 15


def _get_obj(content, vim_type: list, name: str | None = None):
    """Helper to get managed objects via containerView with hard timeout."""
    view_holder: list[Any] = []
    exc_holder: list[BaseException] = []
    objs_holder: list[list[Any]] = []

    def _target():
        try:
            view = content.viewManager.CreateContainerView(content.rootFolder, vim_type, True)
            view_holder.append(view)
            try:
                objs_holder.append(list(view.view))
            except BaseException as e:
                exc_holder.append(e)
        except BaseException as e:
            exc_holder.append(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=_VIEW_TIMEOUT)
    if t.is_alive():
        log.warning("discover: CreateContainerView %s timed out after %ss (6.7 large inventory)", vim_type, _VIEW_TIMEOUT)
        # view may be leaked (daemon thread) but we return empty to keep discover progressing
        return [] if name is None else None
    if exc_holder:
        raise exc_holder[0]
    # cleanup view
    if view_holder:
        try:
            view_holder[0].Destroy()
        except Exception:
            pass
    objs = objs_holder[0] if objs_holder else []
    if name:
        for o in objs:
            if getattr(o, "name", None) == name:
                return o
        return None
    return objs


def _safe_list(view_result) -> list:
    if view_result is None:
        return []
    try:
        return list(view_result)
    except Exception:
        return []


def discover(si, datacenter: str | None = None) -> dict[str, Any]:
    """Enumerate inventory. Returns dict with datacenters, clusters, hosts, datastores, networks, folders, templates, etc."""
    content = si.RetrieveContent()
    result: dict[str, Any] = {}

    # API version hint (6.7 reports 6.7.x) — best-effort
    try:
        result["apiVersion"] = getattr(content.about, "apiVersion", "")
        result["version"] = getattr(content.about, "version", "")
        result["build"] = getattr(content.about, "build", "")
    except Exception:
        pass

    try:
        from pyVmomi import vim

        # Datacenters — present on vCenter; ESXi direct may report 0
        try:
            dcs = _get_obj(content, [vim.Datacenter])
        except Exception as e:
            log.warning("discover: failed to list Datacenter: %s", e)
            dcs = []
        result["datacenters"] = []
        for dc in dcs:
            try:
                result["datacenters"].append({"name": dc.name, "moid": getattr(dc, "_moId", str(dc))})
            except Exception:
                continue

        # Clusters & hosts — 6.7 compat: ClusterComputeResource may be absent
        result["clusters"] = []
        try:
            clusters = _get_obj(content, [vim.ClusterComputeResource])
        except Exception as e:
            log.warning("discover: failed to list clusters: %s", e)
            clusters = []
        for c in clusters:
            try:
                hosts = getattr(c, "host", None) or []
                result["clusters"].append(
                    {
                        "name": getattr(c, "name", ""),
                        "moid": getattr(c, "_moId", ""),
                        "hostCount": len(list(hosts)) if hosts else 0,
                        "hosts": [{"name": getattr(h, "name", ""), "moid": getattr(h, "_moId", "")} for h in (hosts or [])],
                    }
                )
            except Exception:
                continue

        # Standalone hosts: ComputeResource on vCenter includes clusters as subclass;
        # filter by exact type name so we only keep standalone hosts.
        result["standaloneHosts"] = []
        try:
            standalone = _get_obj(content, [vim.ComputeResource])
            for h in standalone:
                if type(h).__name__ == "ComputeResource":
                    result["standaloneHosts"].append({"name": getattr(h, "name", ""), "moid": getattr(h, "_moId", "")})
        except Exception as e:
            log.warning("discover: failed to list ComputeResource: %s", e)

        # Datastores — 6.7 compat: some fields absent, guard each
        result["datastores"] = []
        try:
            datastores = _get_obj(content, [vim.Datastore])
        except Exception as e:
            log.warning("discover: failed to list Datastore: %s", e)
            datastores = []
        for ds in datastores:
            try:
                summary = getattr(ds, "summary", None)
                result["datastores"].append(
                    {
                        "name": getattr(ds, "name", ""),
                        "moid": getattr(ds, "_moId", ""),
                        "capacity": getattr(summary, "capacity", 0) if summary else 0,
                        "freeSpace": getattr(summary, "freeSpace", 0) if summary else 0,
                        "type": getattr(summary, "type", "") if summary else "",
                        "accessible": getattr(summary, "accessible", True) if summary else True,
                    }
                )
            except Exception:
                continue

        # Networks — vim.Network exists on 6.7; DistributedVirtualPortgroup may not
        result["networks"] = []
        try:
            networks = _get_obj(content, [vim.Network])
            for n in networks:
                result["networks"].append({"name": getattr(n, "name", ""), "moid": getattr(n, "_moId", "")})
        except Exception as e:
            log.warning("discover: failed to list Network: %s", e)

        # Folders
        result["folders"] = []
        try:
            folders = _get_obj(content, [vim.Folder])
            for f in folders:
                result["folders"].append({"name": getattr(f, "name", ""), "moid": getattr(f, "_moId", "")})
        except Exception as e:
            log.warning("discover: failed to list Folder: %s", e)

        # Resource pools — may be minimal on ESXi direct
        result["resourcePools"] = []
        try:
            rps = _get_obj(content, [vim.ResourcePool])
            for rp in rps:
                result["resourcePools"].append({"name": getattr(rp, "name", ""), "moid": getattr(rp, "_moId", "")})
        except Exception as e:
            log.warning("discover: failed to list ResourcePool: %s", e)

        # Templates & VMs — 6.7: config.template may be missing on some VMs
        result["templates"] = []
        result["vms"] = []
        try:
            vms = _get_obj(content, [vim.VirtualMachine])
            for vm in vms:
                try:
                    cfg = getattr(vm, "config", None)
                    is_template = bool(getattr(cfg, "template", False)) if cfg is not None else False
                    entry = {
                        "name": getattr(vm, "name", ""),
                        "moid": getattr(vm, "_moId", ""),
                        "guestId": getattr(cfg, "guestId", "") if cfg is not None else "",
                    }
                    if is_template:
                        result["templates"].append(entry)
                    else:
                        result["vms"].append(entry)
                except Exception:
                    continue
        except Exception as e:
            log.warning("discover: failed to list VirtualMachine: %s", e)

        # ISO images: not auto-scanned (expensive). Caller uses scan_iso_images().
        result["isoImages"] = []

        # Helpful counts for the UI
        _clusters = result.get("clusters", [])
        _standalone = result.get("standaloneHosts", [])
        _host_count = sum(c.get("hostCount", 0) for c in _clusters) if _clusters else len(_standalone)
        result["summary"] = {
            "datacenters": len(result.get("datacenters", [])),
            "clusters": len(_clusters),
            "hosts": _host_count,
            "datastores": len(result.get("datastores", [])),
            "networks": len(result.get("networks", [])),
            "templates": len(result.get("templates", [])),
            "vms": len(result.get("vms", [])),
        }

    except Exception as e:
        # Never let discover fail entirely — return partial result plus error
        log.warning("discover: top-level error: %s", e, exc_info=True)
        result["error"] = str(e)
        # Ensure keys exist so callers don't KeyError
        for k in ("datacenters", "clusters", "standaloneHosts", "datastores", "networks", "folders", "resourcePools", "templates", "vms", "isoImages"):
            result.setdefault(k, [])

    return result


def scan_iso_images(si, datastore_name: str | None = None) -> list[dict[str, Any]]:
    """Scan datastore(s) for .iso files via Datastore Browser.

    Timeout/guarded so it never blocks discover.  6.7 compat: SearchSpec API
    is identical since 6.0.
    """
    content = si.RetrieveContent()
    from pyVmomi import vim

    try:
        datastores = _get_obj(content, [vim.Datastore])
    except Exception as e:
        log.warning("scan_iso_images: failed to list datastores: %s", e)
        return []

    if datastore_name:
        datastores = [ds for ds in datastores if getattr(ds, "name", None) == datastore_name]

    isos: list[dict[str, Any]] = []
    for ds in datastores:
        task = None
        try:
            browser = getattr(ds, "browser", None)
            if browser is None:
                continue
            spec = vim.host.DatastoreBrowser.SearchSpec()
            spec.matchPattern = ["*.iso"]
            ds_path = f"[{getattr(ds, 'name', '')}] "
            task = browser.SearchDatastoreSubFolders_Task(ds_path, spec)
            deadline = time.monotonic() + 30
            while task.info.state not in ("success", "error"):
                if time.monotonic() > deadline:
                    log.warning("scan_iso_images: timeout on datastore %s", getattr(ds, "name", ""))
                    try:
                        task.CancelTask()
                    except Exception:
                        pass
                    break
                time.sleep(0.5)
            if task.info.state == "success" and task.info.result:
                for res in task.info.result:
                    folder = getattr(res, "folderPath", "")
                    for f in getattr(res, "file", []) or []:
                        isos.append(
                            {
                                "datastore": getattr(ds, "name", ""),
                                "path": f"{folder}{f.path}",
                                "fullPath": f"[{getattr(ds, 'name', '')}] {folder}{f.path}",
                                "size": getattr(f, "fileSize", 0),
                            }
                        )
        except Exception as e:
            log.warning("scan_iso_images: datastore %s: %s", getattr(ds, "name", ""), e)
            # best-effort cancel orphan task
            if task is not None:
                try:
                    if getattr(getattr(task, "info", None), "state", None) not in ("success", "error"):
                        task.CancelTask()
                except Exception:
                    pass
            continue
    return isos


def test_connection(host: str, port: int, user: str, password: str) -> dict[str, Any]:
    """Test connection and return datacenter list + api version."""
    from .client import connect, disconnect

    si = None
    try:
        si = connect(host, port, user, password)
        # discover can block on 6.7 large inventories — enforce hard timeout
        inv_holder: list[dict[str, Any]] = []
        exc_holder: list[BaseException] = []

        def _discover_target():
            try:
                inv_holder.append(discover(si))
            except BaseException as e:
                exc_holder.append(e)

        t = threading.Thread(target=_discover_target, daemon=True)
        t.start()
        t.join(timeout=40)
        if t.is_alive():
            log.warning("test_connection: discover timed out after 40s for %s:%s", host, port)
            inv = {"error": "discover timed out after 40s (large inventory or 6.7 slow view)", "datacenters": [], "clusters": [], "datastores": [], "networks": [], "templates": [], "vms": [], "summary": {}}
        elif exc_holder:
            raise exc_holder[0]
        else:
            inv = inv_holder[0] if inv_holder else {}
        # Even on partial failure, return ok if we got datacenters
        errs = inv.get("error")
        if inv.get("datacenters") or not errs:
            return {
                "ok": True,
                "datacenters": inv.get("datacenters", []),
                "apiVersion": inv.get("apiVersion", ""),
                "version": inv.get("version", ""),
                "summary": inv.get("summary", {}),
                **({"warning": errs} if errs else {}),
            }
        return {"ok": False, "error": errs or "discover returned no datacenters"}
    except Exception as e:
        log.warning("test_connection %s:%s: %s", host, port, e, exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        if si is not None:
            disconnect(si)

"""Discovery: enumerate vSphere inventory via pyVmomi.

Compatible with vCenter/ESXi 6.7 through 8.0.  Property names and API
availability vary across versions so every access is guarded.
"""
from __future__ import annotations

import logging
import threading
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
    view = view_holder[0] if view_holder else None
    try:
        if exc_holder:
            raise exc_holder[0]
        objs = objs_holder[0] if objs_holder else []
    finally:
        # Destroy the view BEFORE propagating any held exception (no leak on error path)
        if view is not None:
            try:
                view.Destroy()
            except Exception:
                pass
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


class _InventoryTimeout(RuntimeError):
    """Raised when the bulk PropertyCollector retrieval exceeds its wall-clock budget."""


# All properties for the entire inventory are fetched in ONE PropertyCollector
# round trip.  The previous implementation did one CreateContainerView per type
# plus lazy per-object attribute access (= one SOAP RetrieveProperties call per
# object per property), i.e. thousands of round trips that hang on 6.7 with a
# large inventory.
_VM_PATHS = [
    "name",
    "config.template",
    "config.guestId",
    "config.guestFullName",
    "runtime.powerState",
    "host",
]
_DS_PATHS = ["name", "summary.capacity", "summary.freeSpace", "summary.type", "summary.accessible"]


def _collect_inventory(content, timeout: int = _VIEW_TIMEOUT) -> list[Any]:
    """Bulk-retrieve inventory properties via one ContainerView + PropertyFilterSpec.

    Returns list[vim.vmodl.query.PropertyCollector.ObjectContent].
    Raises _InventoryTimeout on hard timeout (the worker thread's finally block
    destroys the view whenever it eventually unblocks).
    """
    from pyVmomi import vim

    view_types = [
        vim.Datacenter,
        vim.ClusterComputeResource,
        vim.ComputeResource,
        vim.HostSystem,
        vim.Datastore,
        vim.Network,
        vim.Folder,
        vim.ResourcePool,
        vim.VirtualMachine,
    ]
    holder: list[Any] = []
    exc_holder: list[BaseException] = []

    def _spec(vtype, paths: list[str]):
        return vim.PropertySpec(type=vtype.__name__, all=False, pathSet=paths)

    def _target():
        view = None
        try:
            pc = content.propertyCollector
            view = content.viewManager.CreateContainerView(content.rootFolder, view_types, True)
            filter_spec = vim.PropertyFilterSpec(
                objectSet=[vim.ObjectSpec(obj=view)],
                propSet=[
                    _spec(vim.Datacenter, ["name"]),
                    _spec(vim.ClusterComputeResource, ["name", "host"]),
                    _spec(vim.ComputeResource, ["name", "host"]),
                    _spec(vim.HostSystem, ["name"]),
                    _spec(vim.Datastore, _DS_PATHS),
                    _spec(vim.Network, ["name"]),
                    _spec(vim.Folder, ["name"]),
                    _spec(vim.ResourcePool, ["name"]),
                    _spec(vim.VirtualMachine, _VM_PATHS),
                ],
                reportMissingObjectsInViews=True,
            )
            holder.append(pc.RetrieveProperties([filter_spec]))
        except BaseException as e:  # noqa: BLE001 - propagated to caller below
            exc_holder.append(e)
        finally:
            if view is not None:
                try:
                    view.Destroy()
                except Exception:
                    pass

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise _InventoryTimeout(
            f"Bulk inventory retrieval timed out after {timeout}s (CreateContainerView/RetrieveProperties blocked — 6.7 large inventory)"
        )
    if exc_holder:
        raise exc_holder[0]
    return holder[0] if holder else []


def _group_by_type(objs) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for oc in objs or []:
        try:
            grouped.setdefault(type(getattr(oc, "obj", None)).__name__, []).append(oc)
        except Exception:
            continue
    return grouped


def _props(oc) -> dict[str, Any]:
    """ObjectContent -> flat {propertyName: value} map."""
    out: dict[str, Any] = {}
    try:
        for p in getattr(oc, "prop", []) or []:
            out[p.name] = p.val
    except Exception:
        pass
    return out


def _moid(obj) -> str:
    try:
        return str(getattr(obj, "_moId", "") or "")
    except Exception:
        return ""


def discover(si, datacenter: str | None = None) -> dict[str, Any]:
    """Enumerate inventory. Returns dict with datacenters, clusters, hosts, datastores, networks, folders, templates, etc.

    Uses a single bulk PropertyCollector retrieval (no N+1 SOAP round trips).
    Non-fatal problems are recorded under ``_warnings`` instead of silently
    returning empty lists.
    """
    content = si.RetrieveContent()
    result: dict[str, Any] = {}
    warnings: list[str] = []
    result["_warnings"] = warnings

    # API version hint (6.7 reports 6.7.x) — best-effort
    try:
        result["apiVersion"] = getattr(content.about, "apiVersion", "")
        result["version"] = getattr(content.about, "version", "")
        result["build"] = getattr(content.about, "build", "")
    except Exception:
        pass

    try:
        objs = _collect_inventory(content)
    except Exception as e:
        # Never let discover fail entirely — record the problem and continue empty
        log.warning("discover: bulk inventory retrieval failed: %s", e, exc_info=True)
        warnings.append(f"inventory retrieval failed: {e}")
        objs = []
    grouped = _group_by_type(objs)

    # HostSystem moid->name lookup (cluster.host returns references only)
    host_names: dict[str, str] = {}
    for oc in grouped.get("HostSystem", []):
        host_names[_moid(oc.obj)] = _props(oc).get("name", "")

    def _host_entries(refs) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for h in refs or []:
            mid = getattr(h, "_moId", "")
            entries.append({"name": host_names.get(mid, "") or getattr(h, "name", ""), "moid": mid})
        return entries

    # Datacenters — present on vCenter; ESXi direct may report 0
    result["datacenters"] = [
        {"name": _props(oc).get("name", ""), "moid": _moid(oc.obj)} for oc in grouped.get("Datacenter", [])
    ]

    # Clusters & hosts — host count straight from len(cluster.host)
    result["clusters"] = []
    for oc in grouped.get("ClusterComputeResource", []):
        props = _props(oc)
        hosts = props.get("host") or []
        result["clusters"].append(
            {
                "name": props.get("name", ""),
                "moid": _moid(oc.obj),
                "hostCount": len(hosts),
                "hosts": _host_entries(hosts),
            }
        )

    # Standalone hosts: ComputeResource on vCenter includes clusters as subclass;
    # filter by exact type name so we only keep standalone hosts.
    result["standaloneHosts"] = [
        {"name": _props(oc).get("name", ""), "moid": _moid(oc.obj)}
        for oc in grouped.get("ComputeResource", [])
        if type(getattr(oc, "obj", None)).__name__ == "ComputeResource"
    ]

    # Datastores — missing summary props default exactly like the old lazy access
    result["datastores"] = []
    for oc in grouped.get("Datastore", []):
        props = _props(oc)
        result["datastores"].append(
            {
                "name": props.get("name", ""),
                "moid": _moid(oc.obj),
                "capacity": props.get("summary.capacity", 0),
                "freeSpace": props.get("summary.freeSpace", 0),
                "type": props.get("summary.type", ""),
                "accessible": props.get("summary.accessible", True),
            }
        )

    result["networks"] = [{"name": _props(oc).get("name", ""), "moid": _moid(oc.obj)} for oc in grouped.get("Network", [])]

    result["folders"] = [{"name": _props(oc).get("name", ""), "moid": _moid(oc.obj)} for oc in grouped.get("Folder", [])]

    result["resourcePools"] = [
        {"name": _props(oc).get("name", ""), "moid": _moid(oc.obj)} for oc in grouped.get("ResourcePool", [])
    ]

    # Templates & VMs — config.template may be absent on some 6.7 objects
    result["templates"] = []
    result["vms"] = []
    for oc in grouped.get("VirtualMachine", []):
        props = _props(oc)
        entry = {
            "name": props.get("name", ""),
            "moid": _moid(oc.obj),
            "guestId": props.get("config.guestId", ""),
            "powerState": str(props.get("runtime.powerState", "")),
        }
        gfn = props.get("config.guestFullName")
        if gfn:
            entry["guestFullName"] = gfn
        if bool(props.get("config.template", False)):
            result["templates"].append(entry)
        else:
            result["vms"].append(entry)

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

    return result


def scan_iso_images(si, datastore_name: str | None = None) -> list[dict[str, Any]]:
    """Scan datastore(s) for .iso files via Datastore Browser.

    Timeout/guarded so it never blocks discover.  6.7 compat: SearchSpec API
    is identical since 6.0.
    """
    content = si.RetrieveContent()
    from pyVmomi import vim

    from .tasks import wait_for_task

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
            # Reuse the shared task-wait helper (monotonic deadline + cancel on timeout)
            res = wait_for_task(task, timeout=60, interval=0.5)
            if res["state"] == "timeout":
                log.warning("scan_iso_images: timeout on datastore %s", getattr(ds, "name", ""))
            if res["state"] == "error":
                log.warning("scan_iso_images: search failed on datastore %s: %s", getattr(ds, "name", ""), res.get("error"))
            if res["state"] == "success" and res.get("result"):
                for folder_res in res["result"]:
                    folder = getattr(folder_res, "folderPath", "")
                    for f in getattr(folder_res, "file", []) or []:
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
            inv = {
                "error": "discover timed out after 40s (large inventory or 6.7 slow view)",
                "_warnings": ["discover timed out after 40s"],
                "datacenters": [],
                "clusters": [],
                "datastores": [],
                "networks": [],
                "templates": [],
                "vms": [],
                "summary": {},
            }
        elif exc_holder:
            raise exc_holder[0]
        else:
            inv = inv_holder[0] if inv_holder else {}
        # ok=True requires either datacenters, or a genuinely empty inventory
        # with zero warnings/errors (e.g. ESXi direct with nothing registered).
        errs = inv.get("error")
        warns = inv.get("_warnings") or []
        base = {
            "datacenters": inv.get("datacenters", []),
            "apiVersion": inv.get("apiVersion", ""),
            "version": inv.get("version", ""),
            "summary": inv.get("summary", {}),
        }
        if inv.get("datacenters"):
            warn = errs or ("; ".join(warns) if warns else "")
            return {"ok": True, **base, **({"warning": warn} if warn else {})}
        if errs or warns:
            msg = errs or "; ".join(warns)
            log.warning("test_connection %s:%s: empty inventory with problems: %s", host, port, msg)
            return {"ok": False, **base, "error": f"No datacenters found — {msg}"}
        log.warning("test_connection %s:%s: connected but inventory is genuinely empty (0 datacenters)", host, port)
        return {"ok": True, **base}
    except Exception as e:
        log.warning("test_connection %s:%s: %s", host, port, e, exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        if si is not None:
            disconnect(si)

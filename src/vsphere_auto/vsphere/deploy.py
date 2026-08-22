"""Deploy helpers: template clone and ISO-based VM creation."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Optional

log = logging.getLogger(__name__)

_TASK_POLL_INTERVAL = 0.5
_TASK_TIMEOUT_CLONE = 1800
_TASK_TIMEOUT_CREATE = 600

# provisioning -> VirtualDiskRelocateSpec.diskType
_PROVISIONING_MAP = {
    "thin": "thin",
    "thick": "preallocated",
    "eagerzeroedthick": "eagerZeroedThick",
}


@contextmanager
def _view(content, vim_types: list):
    """Contextmanager that guarantees Destroy() even on exception."""
    view = content.viewManager.CreateContainerView(content.rootFolder, vim_types, True)
    try:
        yield view
    finally:
        try:
            view.Destroy()
        except Exception:
            pass


def _collect_objs(content, root, vim_types: list) -> list:
    """All objects of vim_types under root via ONE container view.

    Two SOAP round trips total (CreateContainerView + view.view bulk response),
    regardless of inventory size — the lazy _walk() alternative costs one call
    per folder node, which stalls minutes on slow 6.7 appliances.
    """
    view = content.viewManager.CreateContainerView(root, list(vim_types), True)
    try:
        return list(view.view)
    finally:
        try:
            view.Destroy()
        except Exception:
            pass


def _bulk_props(content, root, vim_type, paths: list) -> tuple[dict, dict]:
    """Bulk-retrieve properties for every vim_type object under root.

    Same strategy as discovery._collect_inventory (validated on a 6.7.3
    appliance): explicit objectSet per object — no view-filter TraversalSpec,
    which some servers answer with an empty result set.

    Returns ({moid: obj}, {moid: {path: value}}).
    """
    from pyVmomi import vim

    objs = _collect_objs(content, root, [vim_type])
    by_moid = {str(getattr(o, "_moId", "")): o for o in objs}
    if not objs:
        return by_moid, {}
    ocs = content.propertyCollector.RetrieveProperties([
        vim.PropertyFilterSpec(
            objectSet=[vim.ObjectSpec(obj=o) for o in objs],
            propSet=[vim.PropertySpec(type=vim_type, all=False, pathSet=list(paths))],
        )
    ])
    props: dict[str, dict] = {}
    for oc in ocs:
        o = getattr(oc, "obj", None)
        d: dict = {}
        # field name differs across pyVmomi generations (propSet vs prop)
        for key in ("propSet", "prop"):
            entries = getattr(oc, key, None)
            if entries:
                for p in entries:
                    d[p.name] = p.val
                break
        props[str(getattr(o, "_moId", ""))] = d
    return by_moid, props


def _bulk_names(content, objs: list) -> dict:
    """{moid: name} for a list of managed-object refs, in ONE round trip."""
    from pyVmomi import vim

    if not objs:
        return {}
    try:
        ocs = content.propertyCollector.RetrieveProperties([
            vim.PropertyFilterSpec(
                objectSet=[vim.ObjectSpec(obj=o) for o in objs],
                propSet=[vim.PropertySpec(type=vim.Folder, all=False, pathSet=["name"])],
            )
        ])
    except Exception:
        return {}
    out: dict[str, str] = {}
    for oc in ocs:
        o = getattr(oc, "obj", None)
        for key in ("propSet", "prop"):
            entries = getattr(oc, key, None)
            if entries:
                out[str(getattr(o, "_moId", ""))] = next((p.val for p in entries if p.name == "name"), "")
                break
    return out


# ---------------------------------------------------------------------------
# scoped lookups — everything resolves inside a datacenter when one is given,
# and a NAMED object that cannot be found is an error, never a silent fallback.
# ---------------------------------------------------------------------------

def _resolve_dc(content, datacenter: str | None):
    """Return (dc, search_root). Raises ValueError when a *named* DC is missing.

    With datacenter=None the single available datacenter is used automatically
    (a direct-ESXi connection reports exactly one 'ha-datacenter'); multiple
    datacenters are an error — silent wrong-place deployment is worse.
    """
    from pyVmomi import vim

    dcs = _collect_objs(content, content.rootFolder, [vim.Datacenter])
    if datacenter:
        for d in dcs:
            if getattr(d, "name", None) == datacenter:
                return d, d
        raise ValueError(f"datacenter {datacenter!r} not found")
    if len(dcs) == 1:
        return dcs[0], dcs[0]
    if len(dcs) > 1:
        names = sorted(str(getattr(d, "name", "?")) for d in dcs)
        raise ValueError(f"multiple datacenters found {names} — specify vcenter.datacenter explicitly")
    return None, content.rootFolder


def _require_first(objs: list, kind: str, name: str):
    if not objs:
        raise ValueError(f"{kind} {name!r} not found")
    return objs[0]


def _collect_resource_pools(content, host_root) -> list:
    """All resource pools under a host folder.

    Resource pools hang off ComputeResource.resourcePool trees, NOT Folder
    childEntity — a plain _walk over the hostFolder never sees them.
    """
    from pyVmomi import vim

    out: list = []

    def _rp_tree(rp) -> None:
        out.append(rp)
        for child in list(getattr(rp, "resourcePool", []) or []):
            _rp_tree(child)

    for cr in _collect_objs(content, host_root, [vim.ComputeResource]):
        root_rp = getattr(cr, "resourcePool", None)
        if root_rp is not None:
            _rp_tree(root_rp)
    return out


def _pick_standalone_host(content, host_root, vim):
    """Placement fallback for cluster-less datacenters: the eligible connected
    non-maintenance standalone host running fewest VMs, with its root pool."""
    best = None
    for cr in _collect_objs(content, host_root, [vim.ComputeResource]):
        if isinstance(cr, vim.ClusterComputeResource):
            continue
        rp = getattr(cr, "resourcePool", None)
        for h in list(getattr(cr, "host", []) or []):
            runtime = getattr(h, "runtime", None)
            if runtime is None or getattr(runtime, "connectionState", None) != "connected":
                continue
            if getattr(runtime, "inMaintenanceMode", False):
                continue
            try:
                load = len(list(getattr(h, "vm", []) or []))
            except Exception:
                load = 0
            if best is None or load < best[0]:
                best = (load, h, rp)
    if best is None:
        return None, None
    return best[1], best[2]


def _pick_host(cluster_obj, vim):
    """Choose (host|None) for placement: DRS-enabled clusters delegate to
    vCenter (None); otherwise the eligible host running fewest VMs wins."""
    try:
        drs_enabled = bool(
            getattr(getattr(cluster_obj, "configurationEx", None), "drsConfig", None)
            and getattr(cluster_obj.configurationEx.drsConfig, "enabled", False)
        )
    except Exception:
        drs_enabled = False
    if drs_enabled:
        return None

    def _eligible(h) -> bool:
        runtime = getattr(h, "runtime", None)
        if runtime is None:
            return False
        if getattr(runtime, "connectionState", None) != "connected":
            return False
        return not bool(getattr(runtime, "inMaintenanceMode", False))

    candidates = []
    for h in list(getattr(cluster_obj, "host", []) or []):
        try:
            if _eligible(h):
                candidates.append((len(list(getattr(h, "vm", []) or [])), h))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def _folder_by_path(content, vm_folder_root, folder_name: str, vim):
    """Find a Folder under vmFolder subtree matching a '/'-separated path.
    Only folders whose childType includes VirtualMachine qualify.

    Child names are bulk-retrieved per segment (one RetrieveProperties call),
    not lazily per child — slow appliances make the lazy loop minutes-long.
    """
    parts = [p.strip() for p in folder_name.strip("/").split("/") if p.strip()]
    if not parts:
        return None

    def _has_childtype(f) -> bool:
        try:
            return any("VirtualMachine" in str(t) for t in (getattr(f, "childType", []) or []))
        except Exception:
            return True

    current = [vm_folder_root]
    for seg_idx, part in enumerate(parts):
        candidates: list = []
        for node in current:
            try:
                children = node.childEntity  # one SOAP call per node
            except AttributeError:
                continue
            candidates += [c for c in list(children) if isinstance(c, vim.Folder)]
        if seg_idx == 0:
            # The vmFolder root itself is a Folder (often literally named 'vm')
            # and appears in folder listings — but never in its own childEntity.
            candidates.append(vm_folder_root)
        if not candidates:
            return None
        names = _bulk_names(content, candidates)
        nxt = [
            c for c in candidates
            if names.get(str(getattr(c, "_moId", "")), getattr(c, "name", None)) == part and _has_childtype(c)
        ]
        if not nxt:
            return None
        current = nxt
    return current[0]


def _parent_chain_path(vm) -> list[str] | None:
    """Folder-name chain from the VM's parent up to (not including) a non-Folder."""
    from pyVmomi import vim

    names: list[str] = []
    node = getattr(vm, "parent", None)
    depth = 0
    while node is not None and depth < 32:
        if not isinstance(node, vim.Folder):
            break
        names.append(getattr(node, "name", "") or "")
        node = getattr(node, "parent", None)
        depth += 1
    names.reverse()
    return names if names else None


def find_vm(content, name: str, folder_name: str | None = None):
    """Find VM by name, optionally scoped to a folder ('/'-path supported).

    Names are bulk-retrieved in ONE PropertyCollector round trip — the previous
    lazy loop cost one SOAP call per VM and stalled minutes on slow appliances.
    A VM whose parent chain cannot be walked is treated as NON-matching when
    a folder filter was requested (previously it bypassed the filter, making
    the duplicate-name idempotency check unreliable).
    """
    from pyVmomi import vim

    req_parts = [p.strip() for p in (folder_name or "").split("/") if p.strip()]
    try:
        by_moid, props = _bulk_props(content, content.rootFolder, vim.VirtualMachine, ["name"])
    except Exception as e:
        log.warning("find_vm %r: bulk retrieval failed (%s) — falling back to lazy scan", name, e)
        by_moid, props = {}, {}
        with _view(content, [vim.VirtualMachine]) as view:
            for vm in list(view.view):
                mid = str(getattr(vm, "_moId", ""))
                try:
                    props[mid] = {"name": getattr(vm, "name", None)}
                except Exception:
                    continue
                by_moid[mid] = vm

    for mid, d in props.items():
        if d.get("name") != name:
            continue
        vm = by_moid.get(mid)
        if vm is None:
            continue
        if req_parts:
            chain = _parent_chain_path(vm)
            if chain is None:
                continue  # unfilterable — do NOT treat as a match
            if len(req_parts) > 1:
                # multi-segment path: require the tail of the chain to match
                if chain[-len(req_parts):] != req_parts:
                    continue
            else:
                if req_parts[0] not in chain:
                    continue
        return vm
    return None


def find_template(content, name: str, search_root=None):
    """Find a template by name. Bulk retrieval: 2 SOAP calls total.

    config.template may be rejected by restricted servers — retry with name
    only would mark every VM a template, so on failure fall back to a lazy
    scan of candidates whose name already matched.
    """
    from pyVmomi import vim

    root = search_root if search_root is not None else content.rootFolder
    try:
        by_moid, props = _bulk_props(content, root, vim.VirtualMachine, ["name", "config.template"])
        mids = [m for m, d in props.items() if d.get("name") == name and bool(d.get("config.template", False))]
    except Exception as e:
        log.warning("find_template %r: bulk retrieval failed (%s) — falling back to lazy scan", name, e)
        candidates = [o for o in _collect_objs(content, root, [vim.VirtualMachine])
                      if getattr(o, "name", None) == name]
        matches = [t for t in candidates
                   if getattr(getattr(t, "config", None), "template", False)]
        return matches[0] if matches else None
    if not mids:
        return None
    obj = by_moid.get(mids[0])
    # confirm via one lazy read so callers can rely on .config being populated
    if obj is not None and getattr(getattr(obj, "config", None), "template", False):
        return obj
    return None


def _poll_task(task, timeout: int, interval: float = _TASK_POLL_INTERVAL) -> dict[str, Any]:
    """Poll task with monotonic deadline. Returns task info snapshot."""
    deadline = time.monotonic() + timeout
    failures = 0
    while True:
        try:
            state = getattr(task.info, "state", "unknown")
            failures = 0
        except Exception as e:
            # RetrieveProperties may throw on fault; repeated read errors mean
            # the session/vCenter is gone — bail early instead of burning the
            # whole timeout window.
            failures += 1
            if failures >= 5:
                _try_cancel(task)
                return {"state": "error", "result": None, "error": f"Lost contact with vCenter while polling task: {e}"}
            if time.monotonic() > deadline:
                _try_cancel(task)
                return {"state": "timeout", "result": None, "error": f"Timeout polling task: {e}"}
            time.sleep(interval)
            continue
        if state in ("success", "error"):
            return {
                "state": state,
                "result": getattr(task.info, "result", None),
                "error": _format_task_error(task) if state == "error" else None,
            }
        if time.monotonic() > deadline:
            _try_cancel(task)
            return {"state": "timeout", "result": None, "error": "Timeout waiting for task"}
        time.sleep(interval)


def _try_cancel(task) -> None:
    try:
        task.CancelTask()
    except Exception:
        pass


def _format_task_error(task) -> str:
    err = getattr(getattr(task, "info", None), "error", None)
    if err is None:
        return "Unknown error"
    # LocalizedMethodFault has .fault, .localizedMessage, .msg
    parts: list[str] = []
    try:
        localized = getattr(err, "localizedMessage", None)
        if localized:
            parts.append(str(localized))
    except Exception:
        pass
    try:
        msg = getattr(err, "msg", None)
        if msg:
            parts.append(str(msg))
    except Exception:
        pass
    try:
        fault = getattr(err, "fault", None)
        if fault:
            parts.append(f"fault={fault!r}")
    except Exception:
        pass
    if parts:
        return " | ".join(parts)
    return str(err)


def _ethernet_card_backing(net_obj, vim):
    """Build the correct backing info for a resolved network object."""
    if isinstance(net_obj, vim.dvs.DistributedVirtualPortgroup):
        switch = getattr(getattr(net_obj, "config", None), "distributedVirtualSwitch", None)
        switch_uuid = getattr(switch, "uuid", None) if switch is not None else None
        if switch_uuid:
            return vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo(
                port=vim.dvs.PortConnection(portgroupKey=net_obj.key, switchUuid=switch_uuid)
            )
        log.warning("portgroup %s lacks switch uuid — falling back to deviceName backing", getattr(net_obj, "name", "?"))
    return vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(deviceName=getattr(net_obj, "name", ""))


def clone_from_template(
    si,
    template_name: str,
    vm_name: str,
    datacenter: str | None = None,
    cluster: str | None = None,
    datastore: str | None = None,
    folder_name: str | None = None,
    resource_pool: str | None = None,
    cpu: int | None = None,
    memory_mb: int | None = None,
    disk_gb: int | None = None,
    customization_spec=None,
    power_on: bool = True,
    network: str | None = None,
    provisioning: str | None = None,
) -> Any:
    """Clone VM from template. Returns vCenter Task.

    All named placements (datacenter/template/datastore/folder/resource_pool/
    cluster/network) resolve strictly inside the target datacenter and raise
    ValueError when missing — silent wrong-place deployment is not acceptable.
    """
    from pyVmomi import vim

    content = si.RetrieveContent()
    dc, scope = _resolve_dc(content, datacenter)

    # Templates live under the datacenter's vmFolder — walking rootFolder or
    # the Datacenter object itself finds nothing (Datacenter has no
    # childEntity; rootFolder's children are Datacenters, not Folders).
    vm_root = getattr(dc, "vmFolder", None) or scope
    tpl = find_template(content, template_name, search_root=vm_root)
    if tpl is None:
        raise ValueError(f"Template {template_name!r} not found"
                         + (f" in datacenter {datacenter!r}" if datacenter else ""))

    # Destination folder: default = template's parent, else dc.vmFolder/root
    dest_folder = getattr(tpl, "parent", None) or (getattr(dc, "vmFolder", None) if dc else content.rootFolder)
    if folder_name:
        f = _folder_by_path(content, vm_root, folder_name, vim)
        if f is None:
            raise ValueError(f"VM folder {folder_name!r} not found"
                             + (f" in datacenter {datacenter!r}" if datacenter else ""))
        dest_folder = f

    relocate = vim.vm.RelocateSpec()

    # Datastore
    ds_root = getattr(dc, "datastoreFolder", None) if dc else content.rootFolder
    if datastore:
        ds_objs = _collect_objs(content, ds_root, [vim.Datastore])
        relocate.datastore = _require_first([d for d in ds_objs if getattr(d, "name", None) == datastore],
                                            "datastore", datastore)

    # Resource pool
    host_root = getattr(dc, "hostFolder", None) if dc else content.rootFolder
    if resource_pool:
        rp_objs = _collect_resource_pools(content, host_root)
        relocate.pool = _require_first([p for p in rp_objs if getattr(p, "name", None) == resource_pool],
                                       "resource pool", resource_pool)

    # Cluster/host placement
    if cluster:
        cl_objs = _collect_objs(content, host_root, [vim.ClusterComputeResource])
        cl = _require_first([c for c in cl_objs if getattr(c, "name", None) == cluster], "cluster", cluster)
        chosen = _pick_host(cl, vim)
        if chosen is not None:
            relocate.host = chosen
        if relocate.pool is None:
            relocate.pool = getattr(cl, "resourcePool", None)
        # DRS clusters: leave host unset so vCenter places the VM
    elif relocate.pool is None:
        # Cluster-less datacenter (standalone hosts only): pick the least-loaded
        # connected host and its root pool, otherwise CloneVM has no placement.
        chosen, pool = _pick_standalone_host(content, host_root, vim)
        if chosen is not None:
            relocate.host = chosen
        if pool is not None:
            relocate.pool = pool

    # Target network remap — without this the cloned VM silently keeps the
    # template's portgroup regardless of what the user selected.
    net_device_changes: list = []
    if network:
        net_root = getattr(dc, "networkFolder", None) if dc else content.rootFolder
        net_objs = _collect_objs(content, net_root, [vim.Network])
        matches = [n for n in net_objs if getattr(n, "name", None) == network]
        if not matches and dc is None:
            with _view(content, [vim.Network]) as view:
                matches = [n for n in list(view.view) if getattr(n, "name", None) == network]
        if not matches:
            raise ValueError(f"network {network!r} not found"
                             + (f" in datacenter {datacenter!r}" if datacenter else ""))
        net_obj = matches[0]

        hardware = getattr(getattr(tpl, "config", None), "hardware", None)
        devices = list(getattr(hardware, "device", []) or []) if hardware is not None else []
        nics = [d for d in devices if isinstance(d, vim.vm.device.VirtualEthernetCard)]
        if nics:
            for nic in nics:
                new_nic = vim.vm.device.VirtualEthernetCard()  # placeholder replaced below
                # edit-in-place: reuse the concrete class so type-specific attrs survive
                spec = vim.vm.device.VirtualDeviceSpec(operation="edit")
                edited = _clone_ethernet_card(nic, vim)
                edited.backing = _ethernet_card_backing(net_obj, vim)
                spec.device = edited
                net_device_changes.append(spec)
        else:
            nic = vim.vm.device.VirtualVmxnet3()
            nic.key = 4000
            nic.backing = _ethernet_card_backing(net_obj, vim)
            nic.connectable = vim.vm.device.VirtualDevice.ConnectInfo(
                startConnected=True, allowGuestControl=True, connected=False
            )
            net_device_changes.append(vim.vm.device.VirtualDeviceSpec(operation="add", device=nic))

    # Disk provisioning override
    if provisioning:
        pkey = str(provisioning).strip().lower()
        if pkey not in _PROVISIONING_MAP:
            raise ValueError(f"invalid provisioning {provisioning!r}; expected one of "
                             f"{sorted(set(_PROVISIONING_MAP))}")
        hardware = getattr(getattr(tpl, "config", None), "hardware", None)
        devices = list(getattr(hardware, "device", []) or []) if hardware is not None else []
        disks = [d for d in devices if isinstance(d, vim.vm.device.VirtualDisk)]
        if disks:
            relocate.disk = [
                vim.vm.device.VirtualDiskRelocateSpec(disk=d, diskType=_PROVISIONING_MAP[pkey])
                for d in disks
            ]

    # Config spec — cpu/memory
    config_spec = None
    if cpu is not None or memory_mb is not None:
        config_spec = vim.vm.ConfigSpec()
        if cpu is not None:
            config_spec.numCPUs = cpu
        if memory_mb is not None:
            config_spec.memoryMB = memory_mb

    # Disk resize: extend first disk if disk_gb larger than template default.
    # Shrink is ignored with a warning.
    if disk_gb is not None:
        try:
            hardware = getattr(getattr(tpl, "config", None), "hardware", None)
            devices = list(getattr(hardware, "device", []) or []) if hardware is not None else []
            tpl_disks = [d for d in devices if isinstance(d, vim.vm.device.VirtualDisk)]
            if tpl_disks:
                cur_kb = getattr(tpl_disks[0], "capacityInKB", 0) or 0
                req_kb = int(disk_gb) * 1024 * 1024
                if req_kb > cur_kb and req_kb > 0:
                    if config_spec is None:
                        config_spec = vim.vm.ConfigSpec()
                    disk = tpl_disks[0]
                    new_disk = vim.vm.device.VirtualDisk()
                    for attr in ("capacityInKB", "controllerKey", "unitNumber", "key"):
                        try:
                            setattr(new_disk, attr, getattr(disk, attr))
                        except Exception:
                            pass
                    new_disk.capacityInKB = req_kb
                    try:
                        new_disk.backing = disk.backing
                    except Exception:
                        pass
                    spec = vim.vm.device.VirtualDeviceSpec(operation="edit", device=new_disk)
                    existing = getattr(config_spec, "deviceChange", None) or []
                    config_spec.deviceChange = list(existing) + [spec]
                elif req_kb and req_kb < cur_kb:
                    log.warning("clone_from_template: disk_gb %s smaller than template %s KB — shrink ignored", disk_gb, cur_kb)
        except Exception as e:
            log.warning("clone_from_template: disk resize handling failed: %s", e)

    # Merge NIC remap into relocate.deviceChange
    if net_device_changes:
        relocate.deviceChange = list(getattr(relocate, "deviceChange", None) or []) + net_device_changes

    clone_spec = vim.vm.CloneSpec(
        location=relocate,
        powerOn=power_on,
        template=False,
        config=config_spec,
        customization=customization_spec,
    )

    task = tpl.CloneVM_Task(folder=dest_folder, name=vm_name, spec=clone_spec)
    return task


def _clone_ethernet_card(nic, vim):
    """Re-create the same concrete ethernet card class preserving identity attrs."""
    new = type(nic)()
    for attr in ("key", "controllerKey", "unitNumber", "addressType", "macAddress",
                 "wakeOnLanEnabled", "resourceAllocation"):
        try:
            val = getattr(nic, attr, None)
            if val is not None:
                setattr(new, attr, val)
        except Exception:
            continue
    return new


def create_vm_from_iso(
    si,
    vm_name: str,
    datastore: str,
    iso_path: str,
    guest_id: str = "ubuntu64Guest",
    cpu: int = 2,
    memory_mb: int = 4096,
    disk_gb: int = 40,
    network_name: str | None = None,
    folder_name: str | None = None,
    datacenter: str | None = None,
    customization_spec=None,
) -> Any:
    """Create blank VM (SCSI controller + disk + NIC) and attach ISO.

    When *customization_spec* is provided it is applied via CustomizeVM_Task
    while the VM is still powered off; the caller controls power-on.
    Returns the created VM managed object.
    """
    from pyVmomi import vim

    content = si.RetrieveContent()
    dc, scope = _resolve_dc(content, datacenter)

    # ---- destination folder ----
    dest_folder = getattr(dc, "vmFolder", None) if dc else content.rootFolder
    if folder_name:
        vm_root = getattr(dc, "vmFolder", None) if dc else content.rootFolder
        f = _folder_by_path(content, vm_root, folder_name, vim)
        if f is None:
            raise ValueError(f"VM folder {folder_name!r} not found"
                             + (f" in datacenter {datacenter!r}" if datacenter else ""))
        dest_folder = f

    # ---- datastore (DC-scoped, strict) ----
    ds_root = getattr(dc, "datastoreFolder", None) if dc else content.rootFolder
    ds_matches = [d for d in _collect_objs(content, ds_root, [vim.Datastore]) if getattr(d, "name", None) == datastore]
    if not ds_matches:
        raise ValueError(f"datastore {datastore!r} not found"
                         + (f" in datacenter {datacenter!r}" if datacenter else ""))
    ds_obj = ds_matches[0]

    # ---- resource pool / host: strictly from this DC's clusters ----
    rp = None
    host_obj = None
    esxi_direct = False
    if dc:
        host_root = getattr(dc, "hostFolder", None)
        clusters = _collect_objs(content, host_root, [vim.ClusterComputeResource])
        if clusters:
            # prefer a cluster that yields an eligible host / usable pool
            for cl in clusters:
                rp = getattr(cl, "resourcePool", None)
                host_obj = _pick_host(cl, vim)
                if rp is not None:
                    break
        else:
            # cluster-less datacenter: least-loaded connected standalone host
            host_obj, rp = _pick_standalone_host(content, host_root, vim)
            if host_obj is None and rp is None:
                raise ValueError("no clusters or hosts found in datacenter "
                                 f"{getattr(dc, 'name', '?')!r}")
    else:
        # ESXi direct connection: no datacenters exist
        esxi_direct = True
        compute = None
        for child in list(content.rootFolder.childEntity):
            if isinstance(child, vim.ComputeResource):
                compute = child
                break
        if compute is None:
            raise ValueError("ESXi direct mode: no ComputeResource found")
        rp = getattr(compute, "resourcePool", None)
        hosts = list(getattr(compute, "host", []) or [])
        host_obj = hosts[0] if hosts else None
        dest_folder = getattr(compute, "vmFolder", None) or dest_folder
    if rp is None:
        raise ValueError("could not resolve a resource pool for VM creation")

    # ---- network (DC-scoped, strict) ----
    net_obj = None
    if network_name:
        net_root = getattr(dc, "networkFolder", None) if dc else content.rootFolder
        matches = [n for n in _collect_objs(content, net_root, [vim.Network]) if getattr(n, "name", None) == network_name]
        if not matches:
            raise ValueError(f"network {network_name!r} not found"
                             + (f" in datacenter {datacenter!r}" if datacenter else ""))
        net_obj = matches[0]

    # ---- ConfigSpec WITH devices: SCSI controller + disk + NIC ----
    files = vim.vm.FileInfo(logDirectory=None, snapshotDirectory=None,
                            suspendDirectory=None, vmPathName=f"[{datastore}] {vm_name}/{vm_name}.vmx")
    config = vim.vm.ConfigSpec(
        name=vm_name,
        guestId=guest_id,
        numCPUs=int(cpu),
        memoryMB=int(memory_mb),
        files=files,
    )

    scsi = vim.vm.device.VirtualLsiLogicSASController()
    scsi.key = 1000
    scsi.busNumber = 0
    scsi.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
    disk = vim.vm.device.VirtualDisk()
    disk.key = 2000
    disk.controllerKey = 1000
    disk.unitNumber = 0
    disk.capacityInKB = int(disk_gb) * 1024 * 1024
    disk.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
        fileName=f"[{datastore}] {vm_name}/{vm_name}.vmdk",
        diskMode="persistent",
    )
    device_change = [
        vim.vm.device.VirtualDeviceSpec(operation="add", device=scsi),
        vim.vm.device.VirtualDeviceSpec(operation="add", device=disk),
    ]

    old_guest = str(guest_id or "").lower().startswith(("win", "rhel5", "centos5", "sles1"))
    nic_cls = vim.vm.device.VirtualE1000 if old_guest else vim.vm.device.VirtualVmxnet3
    if net_obj is not None:
        nic = nic_cls()
        nic.key = 4000
        nic.backing = _ethernet_card_backing(net_obj, vim)
        nic.connectable = vim.vm.device.VirtualDevice.ConnectInfo(
            startConnected=True, allowGuestControl=True, connected=False
        )
        device_change.append(vim.vm.device.VirtualDeviceSpec(operation="add", device=nic))
    else:
        log.warning("create_vm_from_iso %s: no network selected — creating VM without NIC", vm_name)
    config.deviceChange = device_change

    task = dest_folder.CreateVM_Task(config=config, pool=rp, host=host_obj)

    res = _poll_task(task, timeout=_TASK_TIMEOUT_CREATE)
    if res["state"] == "timeout":
        raise RuntimeError(f"CreateVM timed out: {res.get('error')}")
    if res["state"] == "error":
        raise RuntimeError(f"CreateVM failed: {res.get('error')}")

    vm = res.get("result") or getattr(task.info, "result", None)

    # Apply guest customization BEFORE first power-on (CustomizeVM requires a
    # powered-off VM; the customization executes via VMware Tools at first boot).
    if customization_spec is not None and vm is not None:
        ct = vm.CustomizeVM_Task(customization_spec)
        cres = _poll_task(ct, timeout=300)
        if cres["state"] != "success":
            raise RuntimeError(f"guest customization failed for {vm_name}: {cres.get('error')}")
        log.info("guest customization applied to %s", vm_name)

    # Attach ISO (best-effort reconfigure)
    if iso_path and vm:
        try:
            hw = getattr(getattr(vm, "config", None), "hardware", None)
            devices = getattr(hw, "device", None) or []
            cdrom = None
            for dev in devices:
                if isinstance(dev, vim.vm.device.VirtualCdrom):
                    cdrom = dev
                    break
            operation = "edit" if cdrom is not None else "add"
            if cdrom is None:
                cdrom = vim.vm.device.VirtualCdrom()
                cdrom.key = -2
                try:
                    for d in devices:
                        if isinstance(d, vim.vm.device.VirtualIDEController):
                            cdrom.controllerKey = d.key
                            cdrom.unitNumber = 0
                            break
                except Exception:
                    pass
            # 6.7 GA IsoBackingInfo has only fileName; datastore attr is 6.7U2+/7.0
            try:
                backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path, datastore=ds_obj)
            except TypeError:
                backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path)
            cdrom.backing = backing
            cdrom.connectable = vim.vm.device.VirtualDevice.ConnectInfo(connected=True, startConnected=True)
            spec = vim.vm.ConfigSpec(deviceChange=[vim.vm.device.VirtualDeviceSpec(operation=operation, device=cdrom)])
            t2 = vm.ReconfigVM_Task(spec)
            r2 = _poll_task(t2, timeout=120)
            if r2["state"] == "error":
                log.warning("ISO attach failed for %s: %s", vm_name, r2.get("error"))
            elif r2["state"] == "timeout":
                log.warning("ISO attach timed out for %s", vm_name)
        except Exception as e:
            log.warning("ISO attach handling failed for %s: %s", vm_name, e)
    return vm

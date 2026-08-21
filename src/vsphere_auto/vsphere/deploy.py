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


def _find_by_name(content, vim_type, name: str):
    """Find first managed object with matching name using safe view lifetime."""
    from pyVmomi import vim  # noqa: F401

    with _view(content, [vim_type]) as view:
        for obj in list(view.view):
            try:
                if getattr(obj, "name", None) == name:
                    return obj
            except Exception:
                continue
    return None


def find_vm(content, name: str, folder_name: str | None = None):
    """Find VM by name, optionally scoped to folder leaf name."""
    from pyVmomi import vim

    with _view(content, [vim.VirtualMachine]) as view:
        for vm in list(view.view):
            try:
                if getattr(vm, "name", None) != name:
                    continue
            except Exception:
                continue
            if folder_name:
                parent = getattr(vm, "parent", None)
                if parent and getattr(parent, "name", "") != folder_name.split("/")[-1]:
                    continue
            return vm
    return None


def find_template(content, name: str):
    from pyVmomi import vim

    with _view(content, [vim.VirtualMachine]) as view:
        for vm in list(view.view):
            try:
                cfg = getattr(vm, "config", None)
                if getattr(vm, "name", None) == name and cfg and getattr(cfg, "template", False):
                    return vm
            except Exception:
                continue
    return None


def _poll_task(task, timeout: int, interval: float = _TASK_POLL_INTERVAL) -> dict[str, Any]:
    """Poll task with monotonic deadline. Returns task info snapshot."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            state = getattr(task.info, "state", "unknown")
        except Exception as e:
            # RetrieveProperties may throw on fault
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
) -> Any:
    """Clone VM from template. Returns vCenter Task."""
    from pyVmomi import vim

    content = si.RetrieveContent()
    tpl = find_template(content, template_name)
    if not tpl:
        raise ValueError(f"Template not found: {template_name}")

    # Resolve datacenter (kept for API compatibility; clone uses folder/pool/host placement)
    if datacenter:
        with _view(content, [vim.Datacenter]) as view:
            for dc in list(view.view):
                if getattr(dc, "name", None) == datacenter:
                    break

    # Folder
    dest_folder = getattr(tpl, "parent", None) or content.rootFolder
    if folder_name:
        leaf = folder_name.split("/")[-1]
        with _view(content, [vim.Folder]) as view:
            for f in list(view.view):
                if getattr(f, "name", None) == leaf:
                    dest_folder = f
                    break

    # Resource pool / datastore / host via RelocateSpec
    relocate = vim.vm.RelocateSpec()
    if datastore:
        with _view(content, [vim.Datastore]) as view:
            for ds in list(view.view):
                if getattr(ds, "name", None) == datastore:
                    relocate.datastore = ds
                    break
    if resource_pool:
        with _view(content, [vim.ResourcePool]) as view:
            for rp in list(view.view):
                if getattr(rp, "name", None) == resource_pool:
                    relocate.pool = rp
                    break
    if cluster:
        with _view(content, [vim.ClusterComputeResource]) as view:
            for cl in list(view.view):
                if getattr(cl, "name", None) == cluster:
                    hosts = getattr(cl, "host", []) or []
                    if hosts:
                        relocate.host = hosts[0]
                    break

    # Config spec — cpu/memory
    config_spec = None
    if cpu is not None or memory_mb is not None:
        config_spec = vim.vm.ConfigSpec()
        if cpu is not None:
            config_spec.numCPUs = cpu
        if memory_mb is not None:
            config_spec.memoryMB = memory_mb

    # Disk resize: extend first disk if disk_gb larger than template default.
    # Requires deviceChange on the clone spec.  If disk_gb is smaller we ignore
    # (shrink not supported) and log a warning.
    if disk_gb is not None and config_spec is not None:
        # We will append disk deviceChange if needed — handled below via
        # inspecting template hardware.  Best-effort; if it fails we still clone.
        try:
            tpl_disks = [d for d in getattr(getattr(tpl, "config", None), "hardware", None).device or [] if isinstance(d, vim.vm.device.VirtualDisk)] if getattr(tpl, "config", None) else []
            if tpl_disks:
                # template capacity is in KB
                cur_kb = getattr(tpl_disks[0], "capacityInKB", 0) or 0
                req_kb = int(disk_gb) * 1024 * 1024
                if req_kb > cur_kb and req_kb > 0:
                    if config_spec is None:
                        config_spec = vim.vm.ConfigSpec()
                    disk = tpl_disks[0]
                    # clone device and extend
                    new_disk = vim.vm.device.VirtualDisk()
                    # copy key/unitNumber etc from template disk where possible
                    for attr in ("capacityInKB", "controllerKey", "unitNumber", "key"):
                        try:
                            setattr(new_disk, attr, getattr(disk, attr))
                        except Exception:
                            pass
                    new_disk.capacityInKB = req_kb
                    # keep backing
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

    clone_spec = vim.vm.CloneSpec(
        location=relocate,
        powerOn=power_on,
        template=False,
        config=config_spec,
        customization=customization_spec,
    )

    task = tpl.CloneVM_Task(folder=dest_folder, name=vm_name, spec=clone_spec)
    return task


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
) -> Any:
    """Create blank VM and attach ISO. Returns VM managed object."""
    from pyVmomi import vim

    content = si.RetrieveContent()
    # Resolve datacenter
    dc = None
    if datacenter:
        with _view(content, [vim.Datacenter]) as view:
            for d in list(view.view):
                if getattr(d, "name", None) == datacenter:
                    dc = d
                    break
    if not dc:
        with _view(content, [vim.Datacenter]) as view:
            v = list(view.view)
            dc = v[0] if v else None

    dest_folder = getattr(dc, "vmFolder", None) if dc else content.rootFolder
    if folder_name:
        leaf = folder_name.split("/")[-1]
        with _view(content, [vim.Folder]) as view:
            for f in list(view.view):
                if getattr(f, "name", None) == leaf:
                    dest_folder = f
                    break

    ds_obj = None
    with _view(content, [vim.Datastore]) as view:
        for ds in list(view.view):
            if getattr(ds, "name", None) == datastore:
                ds_obj = ds
                break
    if not ds_obj:
        raise ValueError(f"Datastore not found: {datastore}")

    # Resource pool: use cluster's or standalone host's
    rp = None
    if dc:
        with _view(content, [vim.ClusterComputeResource]) as view:
            for cl in list(view.view):
                _rp = getattr(cl, "resourcePool", None)
                if _rp:
                    rp = _rp
                    break
        if not rp:
            with _view(content, [vim.ComputeResource]) as view:
                for cr in list(view.view):
                    _rp = getattr(cr, "resourcePool", None)
                    if _rp:
                        rp = _rp
                        break

    # Network
    net_obj = None
    if network_name:
        with _view(content, [vim.Network]) as view:
            for n in list(view.view):
                if getattr(n, "name", None) == network_name:
                    net_obj = n
                    break

    # Host placement: for DRS-disabled clusters, pick first host as fallback
    host_obj = None
    if dc:
        with _view(content, [vim.ClusterComputeResource]) as view:
            for cl in list(view.view):
                hosts = getattr(cl, "host", None) or []
                if hosts:
                    host_obj = hosts[0]
                    break
        if not host_obj:
            with _view(content, [vim.ComputeResource]) as view:
                # standalone host case: ComputeResource is the host container
                for cr in list(view.view):
                    # try to get host
                    h = getattr(cr, "host", None)
                    if h:
                        # host is a list with one HostSystem
                        try:
                            host_obj = list(h)[0] if hasattr(h, "__iter__") else h
                        except Exception:
                            host_obj = None
                        if host_obj:
                            break

    # Build ConfigSpec for blank VM — include datastore hint via Files
    files = vim.vm.FileInfo(logDirectory=None, snapshotDirectory=None, suspendDirectory=None, vmPathName=f"[{datastore}] {vm_name}/{vm_name}.vmx")
    config = vim.vm.ConfigSpec(
        name=vm_name,
        guestId=guest_id,
        numCPUs=cpu,
        memoryMB=memory_mb,
        files=files,
    )

    # Optionally attach network at creation time via deviceChange (best-effort)
    # Keep minimal to avoid 6.7 compat issues; network can be reconfigured post-create.

    task = dest_folder.CreateVM_Task(config=config, pool=rp, host=host_obj)

    res = _poll_task(task, timeout=_TASK_TIMEOUT_CREATE)
    if res["state"] == "timeout":
        raise RuntimeError(f"CreateVM timed out: {res.get('error')}")
    if res["state"] == "error":
        raise RuntimeError(f"CreateVM failed: {res.get('error')}")

    vm = res.get("result") or getattr(task.info, "result", None)
    # Attach ISO if provided (optional second step)
    if iso_path and vm:
        try:
            hw = getattr(getattr(vm, "config", None), "hardware", None)
            devices = getattr(hw, "device", None) or []
            for dev in devices:
                if isinstance(dev, vim.vm.device.VirtualCdrom):
                    cdrom = dev
                    # 6.7 GA IsoBackingInfo has only fileName; datastore attr is 6.7U2+/7.0
                    try:
                        backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path, datastore=ds_obj)
                    except TypeError:
                        backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path)
                    cdrom.backing = backing
                    cdrom.connectable = vim.vm.device.VirtualDevice.ConnectInfo(connected=True, startConnected=True)
                    spec = vim.vm.ConfigSpec(deviceChange=[vim.vm.device.VirtualDeviceSpec(operation="edit", device=cdrom)])
                    t2 = vm.ReconfigVM_Task(spec)
                    r2 = _poll_task(t2, timeout=120)
                    if r2["state"] == "error":
                        log.warning("ISO attach failed for %s: %s", vm_name, r2.get("error"))
                    elif r2["state"] == "timeout":
                        log.warning("ISO attach timed out for %s", vm_name)
                    break
        except Exception as e:
            log.warning("ISO attach handling failed for %s: %s", vm_name, e)
    return vm

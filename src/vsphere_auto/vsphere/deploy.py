"""Deploy helpers: template clone and ISO-based VM creation."""
from __future__ import annotations

from typing import Any, Optional


def find_vm(content, name: str, folder_name: str | None = None):
    """Find VM by name, optionally scoped to folder."""
    from pyVmomi import vim

    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    for vm in view.view:
        if vm.name == name:
            if folder_name:
                # check folder ancestry by traversing parent chain name match (best-effort)
                parent = getattr(vm, "parent", None)
                if parent and getattr(parent, "name", "") != folder_name.split("/")[-1]:
                    continue
            view.Destroy()
            return vm
    view.Destroy()
    return None


def find_template(content, name: str):
    from pyVmomi import vim

    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    for vm in view.view:
        cfg = getattr(vm, "config", None)
        if vm.name == name and cfg and getattr(cfg, "template", False):
            view.Destroy()
            return vm
    view.Destroy()
    return None


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

    # Resolve placements
    datacenter_obj = None
    if datacenter:
        for dc in content.viewManager.CreateContainerView(content.rootFolder, [vim.Datacenter], True).view:
            if dc.name == datacenter:
                datacenter_obj = dc
                break

    # Folder
    dest_folder = tpl.parent if hasattr(tpl, "parent") else content.rootFolder
    if folder_name:
        # best-effort find folder by name
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Folder], True)
        for f in view.view:
            if f.name == folder_name.split("/")[-1]:
                dest_folder = f
                break
        view.Destroy()

    # Resource pool / datastore / host via RelocateSpec
    relocate = vim.vm.RelocateSpec()
    if datastore:
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
        for ds in view.view:
            if ds.name == datastore:
                relocate.datastore = ds
                break
        view.Destroy()
    if resource_pool:
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.ResourcePool], True)
        for rp in view.view:
            if rp.name == resource_pool:
                relocate.pool = rp
                break
        view.Destroy()
    if cluster:
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.ClusterComputeResource], True)
        for cl in view.view:
            if cl.name == cluster:
                # pick first host if needed
                hosts = getattr(cl, "host", [])
                if hosts:
                    relocate.host = hosts[0]
                break
        view.Destroy()

    # Config spec
    config_spec = None
    if cpu or memory_mb:
        config_spec = vim.vm.ConfigSpec()
        if cpu:
            config_spec.numCPUs = cpu
        if memory_mb:
            config_spec.memoryMB = memory_mb

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
    # Resolve datacenter / folder / datastore / network
    dc = None
    if datacenter:
        for d in content.viewManager.CreateContainerView(content.rootFolder, [vim.Datacenter], True).view:
            if d.name == datacenter:
                dc = d
                break
    if not dc:
        # pick first DC
        dcs = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datacenter], True).view
        dc = dcs[0] if dcs else None

    dest_folder = dc.vmFolder if dc else content.rootFolder  # type: ignore
    if folder_name:
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Folder], True)
        for f in view.view:
            if f.name == folder_name.split("/")[-1]:
                dest_folder = f
                break
        view.Destroy()

    ds_obj = None
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
    for ds in view.view:
        if ds.name == datastore:
            ds_obj = ds
            break
    view.Destroy()
    if not ds_obj:
        raise ValueError(f"Datastore not found: {datastore}")

    # Resource pool: use cluster's or DC's
    rp = None
    if dc:
        # try cluster resource pool
        cl_view = content.viewManager.CreateContainerView(content.rootFolder, [vim.ClusterComputeResource], True)
        for cl in cl_view.view:
            if cl.resourcePool:
                rp = cl.resourcePool
                break
        cl_view.Destroy()
        if not rp:
            # standalone host
            cr_view = content.viewManager.CreateContainerView(content.rootFolder, [vim.ComputeResource], True)
            for cr in cr_view.view:
                if getattr(cr, "resourcePool", None):
                    rp = cr.resourcePool
                    break
            cr_view.Destroy()

    # Network
    net_obj = None
    if network_name:
        net_view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Network], True)
        for n in net_view.view:
            if n.name == network_name:
                net_obj = n
                break
        net_view.Destroy()

    # Build ConfigSpec for blank VM
    files = vim.vm.FileInfo(logDirectory=None, snapshotDirectory=None, suspendDirectory=None, vmPathName=f"[{datastore}] {vm_name}/{vm_name}.vmx")
    config = vim.vm.ConfigSpec(
        name=vm_name,
        guestId=guest_id,
        numCPUs=cpu,
        memoryMB=memory_mb,
        files=files,
    )

    # Best-effort: rely on vCenter to place via storage/host defaults; caller should have selected datastore
    task = dest_folder.CreateVM_Task(config=config, pool=rp, host=None)
    # Wait for creation
    while task.info.state not in ("success", "error"):
        import time

        time.sleep(0.5)
    if task.info.state == "error":
        raise RuntimeError(f"CreateVM failed: {task.info.error}")

    vm = task.info.result
    # Attach ISO if provided (optional second step; simplified)
    if iso_path and vm:
        try:
            spec = vim.vm.ConfigSpec()
            # Find CD-ROM device
            for dev in vm.config.hardware.device:
                if isinstance(dev, vim.vm.device.VirtualCdrom):
                    cdrom = dev
                    backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path, datastore=ds_obj)
                    cdrom.backing = backing
                    cdrom.connectable = vim.vm.device.VirtualDevice.ConnectInfo(connected=True, startConnected=True)
                    spec.deviceChange = [vim.vm.device.VirtualDeviceSpec(operation="edit", device=cdrom)]
                    t2 = vm.ReconfigVM_Task(spec)
                    while t2.info.state not in ("success", "error"):
                        import time

                        time.sleep(0.3)
                    break
        except Exception:
            pass
    return vm

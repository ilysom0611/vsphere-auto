"""Guest customization spec builders (LinuxPrep / Sysprep)."""
from __future__ import annotations

from typing import Any


def build_linux_customization(
    hostname: str,
    domain: str = "",
    dns: list[str] | None = None,
    ip: str | None = None,
    netmask: str | None = None,
    gateway: str | None = None,
    nics: list[dict[str, str | None]] | None = None,
):
    """Build vim.vm.customization.Specification for Linux.

    Supports single NIC via ip/netmask/gateway or multi-NIC via nics=[
      {"ip": "10.0.0.10", "netmask": "255.255.255.0", "gateway": "10.0.0.1"},
      {"ip": "dhcp"}, ...
    ].
    """
    try:
        from pyVmomi import vim
    except ImportError:
        return None

    # Identity
    linux_prep = vim.vm.customization.LinuxPrep()
    linux_prep.hostName = vim.vm.customization.FixedName(name=hostname)
    # Only set domain if explicitly provided (empty means no domain)
    linux_prep.domain = domain if domain else ""

    # Global IP settings
    global_ip = vim.vm.customization.GlobalIPSettings()
    if dns:
        global_ip.dnsServerList = dns

    # Adapter mappings
    nic_defs: list[dict[str, str | None]]
    if nics is not None:
        nic_defs = nics
    elif ip is not None or netmask is not None or gateway is not None:
        nic_defs = [{"ip": ip, "netmask": netmask, "gateway": gateway}]
    else:
        nic_defs = [{"ip": None}]

    adapters: list[Any] = []
    for nic in nic_defs:
        _ip = nic.get("ip")
        _netmask = nic.get("netmask")
        _gateway = nic.get("gateway")
        adapter = vim.vm.customization.AdapterMapping()
        ip_settings = vim.vm.customization.IPSettings()
        # ip == None/"" -> treat as DHCP (explicit caller should pass "dhcp")
        if _ip and _ip not in ("dhcp", "auto"):
            fixed = vim.vm.customization.FixedIp(ipAddress=_ip)
            ip_settings.ip = fixed
            if _netmask:
                ip_settings.subnetMask = _netmask
            if _gateway:
                ip_settings.gateway = [_gateway]
            if dns:
                ip_settings.dnsServerList = dns
        elif _ip == "":
            # Empty string is not a valid FixedIp — treat as DHCP with warning
            import logging

            logging.getLogger(__name__).warning("build_linux_customization: empty ip string treated as DHCP for %s", hostname)
            ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
        else:
            ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
        adapter.ip = ip_settings
        adapters.append(adapter)

    spec = vim.vm.customization.Specification()
    spec.identity = linux_prep
    spec.globalIPSettings = global_ip
    spec.nicSettingMap = adapters
    return spec


def build_windows_customization(
    hostname: str,
    password: str = "",
    domain: str = "WORKGROUP",
    dns: list[str] | None = None,
    ip: str | None = None,
    netmask: str | None = None,
    gateway: str | None = None,
    nics: list[dict[str, str | None]] | None = None,
    timezone: int = 210,
    domain_admin: str = "Administrator",
):
    """Build vim.vm.customization.Specification for Windows (Sysprep).

    Args:
        timezone: VMware timezone index (210=China Standard Time). Caller should
                  pass appropriate value for target region.
        domain_admin: domain join admin username.
        nics: same multi-NIC format as Linux.
    """
    try:
        from pyVmomi import vim
    except ImportError:
        return None

    gui = vim.vm.customization.GuiUnattended()
    gui.autoLogon = False
    gui.password = vim.vm.customization.Password(value=password, plainText=True) if password else None
    gui.timeZone = timezone

    ident = vim.vm.customization.Identification()
    if domain and domain.upper() != "WORKGROUP":
        ident.joinDomain = domain
        ident.domainAdmin = domain_admin
        ident.domainAdminPassword = vim.vm.customization.Password(value=password, plainText=True) if password else None
    else:
        ident.joinWorkgroup = domain or "WORKGROUP"

    sysprep = vim.vm.customization.Sysprep()
    sysprep.guiUnattended = gui
    sysprep.identification = ident
    sysprep.hostName = vim.vm.customization.FixedName(name=hostname)

    global_ip = vim.vm.customization.GlobalIPSettings()
    if dns:
        global_ip.dnsServerList = dns

    # Multi-NIC support
    nic_defs: list[dict[str, str | None]]
    if nics is not None:
        nic_defs = nics
    elif ip is not None or netmask is not None or gateway is not None:
        nic_defs = [{"ip": ip, "netmask": netmask, "gateway": gateway}]
    else:
        nic_defs = [{"ip": None}]

    adapters: list[Any] = []
    for nic in nic_defs:
        _ip = nic.get("ip")
        _netmask = nic.get("netmask")
        _gateway = nic.get("gateway")
        adapter = vim.vm.customization.AdapterMapping()
        ip_settings = vim.vm.customization.IPSettings()
        if _ip and _ip not in ("dhcp", "auto"):
            ip_settings.ip = vim.vm.customization.FixedIp(ipAddress=_ip)
            if _netmask:
                ip_settings.subnetMask = _netmask
            if _gateway:
                ip_settings.gateway = [_gateway]
        elif _ip == "":
            import logging

            logging.getLogger(__name__).warning("build_windows_customization: empty ip string treated as DHCP for %s", hostname)
            ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
        else:
            ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
        adapter.ip = ip_settings
        adapters.append(adapter)

    spec = vim.vm.customization.Specification()
    spec.identity = sysprep
    spec.globalIPSettings = global_ip
    spec.nicSettingMap = adapters
    return spec

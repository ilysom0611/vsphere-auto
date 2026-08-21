"""Guest customization spec builders (LinuxPrep / Sysprep)."""
from __future__ import annotations

from typing import Any


def build_linux_customization(hostname: str, domain: str = "", dns: list[str] | None = None, ip: str | None = None, netmask: str | None = None, gateway: str | None = None):
    """Build vim.vm.customization.Specification for Linux."""
    try:
        from pyVmomi import vim
    except ImportError:
        return None

    # Identity
    linux_prep = vim.vm.customization.LinuxPrep()
    linux_prep.hostName = vim.vm.customization.FixedName(name=hostname)
    linux_prep.domain = domain or "localdomain"

    # Global IP settings
    global_ip = vim.vm.customization.GlobalIPSettings()
    if dns:
        global_ip.dnsServerList = dns

    # Adapter mapping
    adapter = vim.vm.customization.AdapterMapping()
    ip_settings = vim.vm.customization.IPSettings()
    if ip and ip != "dhcp" and ip != "auto":
        fixed = vim.vm.customization.FixedIp(ipAddress=ip)
        ip_settings.ip = fixed
        if netmask:
            ip_settings.subnetMask = netmask
        if gateway:
            ip_settings.gateway = [gateway]
        if dns:
            ip_settings.dnsServerList = dns
    else:
        # DHCP
        ip_settings.ip = vim.vm.customization.DhcpIpGenerator()

    adapter.ip = ip_settings
    spec = vim.vm.customization.Specification()
    spec.identity = linux_prep
    spec.globalIPSettings = global_ip
    spec.nicSettingMap = [adapter]
    return spec


def build_windows_customization(hostname: str, password: str = "", domain: str = "WORKGROUP", dns: list[str] | None = None, ip: str | None = None, netmask: str | None = None, gateway: str | None = None):
    """Build vim.vm.customization.Specification for Windows (Sysprep)."""
    try:
        from pyVmomi import vim
    except ImportError:
        return None

    gui = vim.vm.customization.GuiUnattended()
    gui.autoLogon = False
    gui.password = vim.vm.customization.Password(value=password, plainText=True) if password else None
    gui.timeZone = 210  # UTC+8 example; caller can override

    ident = vim.vm.customization.Identification()
    if domain and domain.upper() != "WORKGROUP":
        ident.joinDomain = domain
        ident.domainAdmin = "Administrator"
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

    adapter = vim.vm.customization.AdapterMapping()
    ip_settings = vim.vm.customization.IPSettings()
    if ip and ip != "dhcp" and ip != "auto":
        ip_settings.ip = vim.vm.customization.FixedIp(ipAddress=ip)
        if netmask:
            ip_settings.subnetMask = netmask
        if gateway:
            ip_settings.gateway = [gateway]
    else:
        ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
    adapter.ip = ip_settings

    spec = vim.vm.customization.Specification()
    spec.identity = sysprep
    spec.globalIPSettings = global_ip
    spec.nicSettingMap = [adapter]
    return spec

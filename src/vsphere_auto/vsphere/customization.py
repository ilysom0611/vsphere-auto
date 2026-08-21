"""Guest customization spec builders (LinuxPrep / Sysprep)."""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

log = logging.getLogger(__name__)


def validate_hostname(hostname: str) -> str:
    """Validate RFC1123 single-label hostname. Raises ValueError otherwise."""
    h = (hostname or "").strip()
    if not h:
        raise ValueError("hostname is empty")
    if "." in h:
        raise ValueError(
            f"hostname {hostname!r} must be a single label without dots "
            "(FQDN domains go in the 'domain' field)"
        )
    if len(h) > 63:
        raise ValueError(f"hostname {hostname!r} exceeds 63 characters (RFC 1123)")
    if not all(c.isalnum() or c == "-" for c in h):
        raise ValueError(f"hostname {hostname!r} may only contain letters, digits and hyphens (RFC 1123)")
    if h.startswith("-") or h.endswith("-"):
        raise ValueError(f"hostname {hostname!r} must not start or end with a hyphen (RFC 1123)")
    return h


def _validate_static_ip(ip: str | None, netmask: str | None, context: str) -> None:
    """Validate a static IP binding: netmask required and both parseable."""
    if netmask is None or not str(netmask).strip():
        raise ValueError(f"{context}: subnet_mask/netmask is required when a static IP ({ip}) is used")
    try:
        ipaddress.ip_address(str(ip).strip())
    except ValueError as e:
        raise ValueError(f"{context}: invalid IP address {ip!r}: {e}") from e
    try:
        ipaddress.ip_address(str(netmask).strip())
    except ValueError as e:
        raise ValueError(f"{context}: invalid subnet mask {netmask!r}: {e}") from e


def _build_nic_adapters(vim, nic_defs: list[dict[str, str | None]], dns: list[str] | None, hostname: str):
    """Shared NIC AdapterMapping builder with static-IP validation."""
    adapters: list[Any] = []
    for idx, nic in enumerate(nic_defs):
        _ip = nic.get("ip")
        _netmask = nic.get("netmask")
        _gateway = nic.get("gateway")
        adapter = vim.vm.customization.AdapterMapping()
        ip_settings = vim.vm.customization.IPSettings()
        # ip == None/""/"dhcp"/"auto" -> DHCP
        if _ip is not None and str(_ip).strip().lower() not in ("", "dhcp", "auto"):
            _validate_static_ip(str(_ip), _netmask, f"{hostname} NIC{idx}")
            fixed = vim.vm.customization.FixedIp(ipAddress=str(_ip).strip())
            ip_settings.ip = fixed
            ip_settings.subnetMask = str(_netmask).strip()
            if _gateway:
                ip_settings.gateway = [_gateway]
            if dns:
                ip_settings.dnsServerList = dns
        else:
            if _ip == "":
                log.warning("customization for %s NIC%d: empty ip string treated as DHCP", hostname, idx)
            ip_settings.ip = vim.vm.customization.DhcpIpGenerator()
        adapter.ip = ip_settings
        adapters.append(adapter)
    return adapters


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

    hostname = validate_hostname(hostname)

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

    adapters = _build_nic_adapters(vim, nic_defs, dns, hostname)

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

    hostname = validate_hostname(hostname)

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

    adapters = _build_nic_adapters(vim, nic_defs, dns, hostname)

    spec = vim.vm.customization.Specification()
    spec.identity = sysprep
    spec.globalIPSettings = global_ip
    spec.nicSettingMap = adapters
    return spec

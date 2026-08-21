"""Pydantic config models and YAML loader with credsRef resolution."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field


class VCenterConfig(BaseModel):
    host: Optional[str] = None
    port: int = 443
    user: Optional[str] = None
    username: Optional[str] = None  # alias for user
    password: Optional[str] = None
    datacenter: Optional[str] = None
    cluster: Optional[str] = None
    datastore: Optional[str] = None
    network: Optional[str] = None
    credsRef: Optional[str] = None  # reference to saved creds name/id

    def effective_user(self) -> Optional[str]:
        u = (self.user or "").strip()
        if u:
            return u
        un = (self.username or "").strip()
        return un or None


class DefaultsConfig(BaseModel):
    folder: str = "workloads/demo"
    # Reserved for future use — not consumed by any code path yet (kept so old
    # YAML files keep validating; values are silently ignored).
    resourcePool: Optional[str] = None
    guestId: str = "ubuntu64Guest"
    firmware: Literal["bios", "efi"] = "bios"
    hardwareVersion: Optional[str] = None
    # Consumed by batch.planner.expand_batch when generating VMs from `count`.
    template: Optional[str] = None


class BatchConfig(BaseModel):
    concurrency: int = 5
    onError: Literal["continue", "fail-fast"] = "continue"
    naming: str = "demo-{index:02d}"


class IpPoolConfig(BaseModel):
    cidr: Optional[str] = None
    gateway: Optional[str] = None
    netmask: Optional[str] = None
    dns: list[str] = Field(default_factory=list)
    # Reserved for future use — leases are always persisted to the default
    # state dir (state/ip_pools.json); this knob is currently not consumed.
    poolsFile: Optional[str] = None


class VmNetworkConfig(BaseModel):
    network: str = "auto"
    ip: str = "auto"  # auto | dhcp | explicit


class VmSpec(BaseModel):
    name: str
    template: Optional[str] = None
    iso: Optional[str] = None
    cpu: int = 2
    memoryMB: int = 4096
    diskGB: Optional[int] = None
    guestId: Optional[str] = None
    folder: Optional[str] = None
    networks: list[VmNetworkConfig] = Field(default_factory=lambda: [VmNetworkConfig()])
    # Reserved for future use — not consumed by any code path yet.
    extraConfig: dict[str, str] = Field(default_factory=dict)


class AppConfig(BaseModel):
    vcenter: VCenterConfig = Field(default_factory=VCenterConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    ipPool: IpPoolConfig = Field(default_factory=IpPoolConfig)
    vms: list[VmSpec] = Field(default_factory=list)
    # Top-level fields consumed by batch.planner.expand_batch when `vms` is
    # empty: generate `count` VMs named via batch.naming from `template`.
    # Must live on the model (not just YAML) or model_dump() drops them.
    count: Optional[int] = None
    template: Optional[str] = None


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    raw_text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text) or {}
    # Track which keys were explicitly in YAML for port handling
    _raw_vc = data.get("vcenter") if isinstance(data.get("vcenter"), dict) else {}
    # env interpolation for password
    if isinstance(data.get("vcenter"), dict) and not data["vcenter"].get("password"):
        env_pwd = os.environ.get("VSPHERE_PASSWORD")
        if env_pwd:
            data["vcenter"]["password"] = env_pwd
    cfg = AppConfig.model_validate(data)
    # stash raw port presence for resolve
    object.__setattr__(cfg.vcenter, "_raw_has_port", "port" in _raw_vc)
    return cfg


def resolve_vcenter_creds(cfg: AppConfig, state_dir: Path | None = None) -> tuple[str, int, str, str]:
    """Return (host, port, user, password) resolving credsRef if present."""
    vc = cfg.vcenter
    if vc.credsRef:
        from .creds.store import resolve_creds

        creds = resolve_creds(vc.credsRef, state_dir)
        if not creds:
            raise ValueError(f"credsRef not found: {vc.credsRef}")
        pwd = creds.decrypted_password(state_dir)
        host = (vc.host or "").strip() or creds.host
        user = vc.effective_user() or creds.username
        # Only override port if explicitly set in YAML
        has_port = getattr(vc, "_raw_has_port", False)
        port = vc.port if has_port and (vc.host or "").strip() else creds.port
        if not host or not user:
            raise ValueError(f"vCenter host/user not resolved (host={host!r} user={user!r})")
        return host, port, user, pwd
    host = (vc.host or "").strip()
    user = (vc.effective_user() or "").strip()
    pwd = (vc.password or "").strip() or os.environ.get("VSPHERE_PASSWORD", "").strip()
    # also support password file
    pwd_file = os.environ.get("VSPHERE_PASSWORD_FILE")
    if not pwd and pwd_file and Path(pwd_file).exists():
        pwd = Path(pwd_file).read_text(encoding="utf-8").strip()
    if not host or not user:
        raise ValueError(f"vCenter host/user not resolved (host={host!r} user={user!r}) — set vcenter.host/user or credsRef")
    return host, vc.port, user, pwd

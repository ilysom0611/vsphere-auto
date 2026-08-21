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
        return self.user or self.username


class DefaultsConfig(BaseModel):
    folder: str = "workloads/demo"
    resourcePool: Optional[str] = None
    guestId: str = "ubuntu64Guest"
    firmware: Literal["bios", "efi"] = "bios"
    hardwareVersion: Optional[str] = None


class BatchConfig(BaseModel):
    concurrency: int = 5
    onError: Literal["continue", "fail-fast"] = "continue"
    naming: str = "demo-{index:02d}"


class IpPoolConfig(BaseModel):
    cidr: Optional[str] = None
    gateway: Optional[str] = None
    netmask: Optional[str] = None
    dns: list[str] = Field(default_factory=list)
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
    extraConfig: dict[str, str] = Field(default_factory=dict)


class AppConfig(BaseModel):
    vcenter: VCenterConfig = Field(default_factory=VCenterConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    ipPool: IpPoolConfig = Field(default_factory=IpPoolConfig)
    vms: list[VmSpec] = Field(default_factory=list)


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # env interpolation for password
    if isinstance(data.get("vcenter"), dict) and not data["vcenter"].get("password"):
        env_pwd = os.environ.get("VSPHERE_PASSWORD")
        if env_pwd:
            data["vcenter"]["password"] = env_pwd
    return AppConfig.model_validate(data)


def resolve_vcenter_creds(cfg: AppConfig, state_dir: Path | None = None) -> tuple[str, int, str, str]:
    """Return (host, port, user, password) resolving credsRef if present."""
    vc = cfg.vcenter
    if vc.credsRef:
        from .creds.store import resolve_creds

        creds = resolve_creds(vc.credsRef, state_dir)
        if not creds:
            raise ValueError(f"credsRef not found: {vc.credsRef}")
        pwd = creds.decrypted_password(state_dir)
        # allow override host/user from config if explicitly set
        host = vc.host or creds.host
        user = vc.effective_user() or creds.username
        port = vc.port if vc.host else creds.port
        return host, port, user, pwd
    host = vc.host or ""
    user = vc.effective_user() or ""
    pwd = vc.password or os.environ.get("VSPHERE_PASSWORD", "")
    # also support password file
    pwd_file = os.environ.get("VSPHERE_PASSWORD_FILE")
    if not pwd and pwd_file and Path(pwd_file).exists():
        pwd = Path(pwd_file).read_text(encoding="utf-8").strip()
    return host, vc.port, user, pwd

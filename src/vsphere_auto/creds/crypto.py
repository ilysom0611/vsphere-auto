"""Fernet key management and encrypt/decrypt helpers."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_FILE = Path("state/.fernet.key")
ENV_KEY = "VSPHERE_AUTO_KEY"


def _load_key(state_dir: Path | None = None) -> bytes:
    """Resolve Fernet key: env > state/.fernet.key (auto-create) > error."""
    env_val = os.environ.get(ENV_KEY)
    if env_val:
        try:
            # validate by attempting to use it
            Fernet(env_val.encode() if isinstance(env_val, str) else env_val)
            return env_val.encode() if isinstance(env_val, str) else env_val
        except Exception as e:
            raise ValueError(f"Invalid {ENV_KEY}: {e}") from e

    key_file = (state_dir or Path("state")) / ".fernet.key"
    # also try vsphere-auto/state when cwd is repo root
    candidates = [key_file, Path("vsphere-auto") / key_file, DEFAULT_KEY_FILE]
    for cand in candidates:
        if cand.exists():
            return cand.read_bytes().strip()

    # auto-create
    key = Fernet.generate_key()
    # prefer state_dir if given, else vsphere-auto/state if exists, else state/
    target = key_file
    if state_dir:
        target = Path(state_dir) / ".fernet.key"
    elif Path("vsphere-auto/state").exists():
        target = Path("vsphere-auto/state/.fernet.key")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(key)
    try:
        os.chmod(target, 0o600)
    except Exception:
        pass
    return key


def get_fernet(state_dir: Path | None = None) -> Fernet:
    key = _load_key(state_dir)
    return Fernet(key)


def encrypt_password(plain: str, state_dir: Path | None = None) -> str:
    f = get_fernet(state_dir)
    return f.encrypt(plain.encode()).decode()


def decrypt_password(token: str, state_dir: Path | None = None) -> str:
    f = get_fernet(state_dir)
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt password — key mismatch or corrupted data") from e

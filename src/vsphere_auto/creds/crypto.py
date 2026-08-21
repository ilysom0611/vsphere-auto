"""Fernet key management and encrypt/decrypt helpers."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_FILE = Path("state/.fernet.key")
ENV_KEY = "VSPHERE_AUTO_KEY"
STATE_ENV = "VSPHERE_STATE_DIR"


def _state_dir_resolved(state_dir: Path | None) -> Path | None:
    env = os.environ.get(STATE_ENV)
    if env:
        return Path(env)
    return state_dir


def _key_candidates(state_dir: Path | None) -> list[Path]:
    sd = _state_dir_resolved(state_dir)
    if sd is not None:
        # Use state_dir explicitly; avoid cwd-dependent fallbacks
        return [Path(sd) / ".fernet.key"]
    # Legacy cwd-dependent fallbacks
    key_file = Path("state/.fernet.key")
    return [key_file, Path("vsphere-auto") / key_file, DEFAULT_KEY_FILE]


def _load_key(state_dir: Path | None = None) -> bytes:
    """Resolve Fernet key: env > state/.fernet.key (auto-create) > error."""
    env_val = os.environ.get(ENV_KEY)
    if env_val is not None:
        env_val = env_val.strip()
        if env_val:
            try:
                Fernet(env_val.encode() if isinstance(env_val, str) else env_val)
                return env_val.encode() if isinstance(env_val, str) else env_val
            except Exception as e:
                raise ValueError(f"Invalid {ENV_KEY}: {e}") from e

    candidates = _key_candidates(state_dir)
    for cand in candidates:
        if cand.exists():
            try:
                data = cand.read_bytes().strip()
            except Exception:
                continue
            if data:
                return data

    # auto-create atomically (O_EXCL) to avoid race
    key = Fernet.generate_key()
    sd = _state_dir_resolved(state_dir)
    if sd is not None:
        target = Path(sd) / ".fernet.key"
    elif Path("vsphere-auto/state").exists():
        target = Path("vsphere-auto/state/.fernet.key")
    else:
        target = Path("state/.fernet.key")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic create: tmp + rename with O_EXCL check
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        # Another process won the race — read its key
        for cand in candidates:
            if cand.exists():
                try:
                    return cand.read_bytes().strip()
                except Exception:
                    continue
        # fallback to our generated key
        return key
    except Exception:
        # Fallback to non-atomic write (e.g., Windows or no O_EXCL support)
        try:
            fd2, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".fernet.")
            try:
                os.write(fd2, key)
                os.fsync(fd2)
                os.close(fd2)
                fd2 = -1
                os.replace(tmp, target)
                try:
                    os.chmod(target, 0o600)
                except Exception:
                    pass
            finally:
                if fd2 != -1:
                    try:
                        os.close(fd2)
                    except Exception:
                        pass
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except Exception:
                    pass
        except Exception:
            # last resort
            try:
                target.write_bytes(key)
                try:
                    os.chmod(target, 0o600)
                except Exception:
                    pass
            except Exception:
                pass
    else:
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
    except (ValueError, Exception) as e:
        # Malformed base64 / wrong key length etc.
        if "InvalidToken" in type(e).__name__:
            raise ValueError("Failed to decrypt password — key mismatch or corrupted data") from e
        raise ValueError(f"Failed to decrypt password: {e}") from e

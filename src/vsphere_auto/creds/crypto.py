"""Fernet key management and encrypt/decrypt helpers."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..utils.paths import get_state_dir

ENV_KEY = "VSPHERE_AUTO_KEY"


def _key_candidates(state_dir: Path | None) -> list[Path]:
    if state_dir is not None:
        return [Path(state_dir) / ".fernet.key"]
    return [get_state_dir() / ".fernet.key"]


def _read_key_file(cand: Path) -> bytes | None:
    try:
        data = cand.read_bytes().strip()
    except Exception:
        return None
    return data or None


def _load_key(state_dir: Path | None = None) -> bytes:
    """Resolve Fernet key: env > state/.fernet.key (auto-create) > error.

    Safety rule: an existing non-empty key file is NEVER overwritten by a
    newly generated key — doing so would permanently destroy every stored
    password. All creation races resolve to "adopt the winner's key".
    """
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
        existing = _read_key_file(cand)
        if existing:
            return existing

    # auto-create atomically (O_EXCL) to avoid race
    key = Fernet.generate_key()
    target = candidates[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        return key
    except FileExistsError:
        # Another process won the race — adopt its key.
        for cand in candidates:
            existing = _read_key_file(cand)
            if existing:
                return existing
        raise RuntimeError(
            f"key file {target} exists but could not be read — refusing to "
            f"generate a replacement (stored passwords would be lost)"
        )
    except Exception:
        # Non-atomic fallback (e.g., no O_EXCL support). Before replacing,
        # re-check: another process may have created a valid key meanwhile.
        existing = _read_key_file(target)
        if existing:
            return existing
        fd2: int = -1
        tmp: str | None = None
        try:
            fd2, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".fernet.")
            os.write(fd2, key)
            os.fsync(fd2)
            os.close(fd2)
            fd2 = -1
            # Final guard immediately before replace
            existing = _read_key_file(target)
            if existing:
                return existing
            os.replace(tmp, target)
            tmp = None
            try:
                os.chmod(target, 0o600)
            except Exception:
                pass
            return key
        except Exception as e:
            raise RuntimeError(f"could not create key file {target}: {e}") from e
        finally:
            if fd2 != -1:
                try:
                    os.close(fd2)
                except Exception:
                    pass
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass


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
    except Exception as e:
        raise ValueError(f"Failed to decrypt password: {e}") from e

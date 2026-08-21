"""Logging setup with sensitive field masking."""
from __future__ import annotations

import logging

SENSITIVE_KEYS = {"password", "pwd", "password_enc", "token", "passwd", "secret", "api_key", "hashed_pwd", "pwd_enc", "vsphere_password"}


def mask_dict(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        lk = k.lower()
        # exact or substring match for common variants like userPassword, apiKey
        if lk in SENSITIVE_KEYS or any(sk in lk for sk in SENSITIVE_KEYS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = mask_dict(v)
        elif isinstance(v, list):
            masked_list = []
            for item in v:
                if isinstance(item, dict):
                    masked_list.append(mask_dict(item))
                else:
                    masked_list.append(item)
            out[k] = masked_list
        else:
            out[k] = v
    return out


def setup_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, level.upper(), None)
    if lvl is None:
        lvl = logging.INFO
        logging.getLogger().warning("Unknown LOG_LEVEL %r, falling back to INFO", level)
    # basicConfig is no-op if handlers already configured (e.g. gunicorn) — force level
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=lvl, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        root.setLevel(lvl)
        for h in root.handlers:
            try:
                h.setLevel(lvl)
            except Exception:
                pass

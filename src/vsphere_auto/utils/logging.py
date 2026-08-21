"""Sensitive-data masking for logs."""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

SENSITIVE_KEYS = {
    "password", "pwd", "password_enc", "token", "passwd", "secret",
    "api_key", "hashed_pwd", "pwd_enc", "vsphere_password",
    "credential", "authorization", "session",
}

# Values registered here are literally masked out of any rendered log record.
_registered_secrets: set[str] = set()

_ENV_SECRET_RE = re.compile(r".*(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|_KEY)$", re.IGNORECASE)


def register_secret(value: str) -> None:
    """Register a literal secret value to be masked from log output."""
    if value and len(value) >= 4:
        _registered_secrets.add(value)


def _collect_env_secrets() -> None:
    for k, v in os.environ.items():
        if _ENV_SECRET_RE.match(k) and v:
            register_secret(v.strip())


def mask_text(text: str) -> str:
    """Replace registered secret values with '***'."""
    if not _registered_secrets:
        return text
    for s in _registered_secrets:
        if s in text:
            text = text.replace(s, "***")
    return text


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


class SensitiveDataFilter(logging.Filter):
    """Masks registered secret values from every rendered log message."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
            masked = mask_text(msg)
            if masked != msg:
                record.msg = masked
                record.args = ()
        except Exception:
            pass
        return True


def setup_logging(level: str = "INFO", log_file=None) -> None:
    lvl = getattr(logging, level.upper(), None)
    if lvl is None:
        lvl = logging.INFO
        logging.getLogger().warning("Unknown LOG_LEVEL %r, falling back to INFO", level)

    _collect_env_secrets()
    sensitive_filter = SensitiveDataFilter()

    handlers: list[logging.Handler] = []
    if log_file is not None:
        try:
            from logging.handlers import RotatingFileHandler

            handlers.append(
                RotatingFileHandler(str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
            )
        except Exception:
            pass

    root = logging.getLogger()
    if not root.handlers:
        if handlers:
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            stream = logging.StreamHandler()
            stream.setFormatter(fmt)
            handlers.append(stream)
            root.setLevel(lvl)
            for h in handlers:
                h.setLevel(lvl)
                h.addFilter(sensitive_filter)
                root.addHandler(h)
        else:
            logging.basicConfig(level=lvl, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            for h in root.handlers:
                h.addFilter(sensitive_filter)
    else:
        root.setLevel(lvl)
        for h in root.handlers:
            try:
                h.setLevel(lvl)
            except Exception:
                pass
            h.addFilter(sensitive_filter)

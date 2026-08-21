"""Logging setup with sensitive field masking."""
from __future__ import annotations

import logging

SENSITIVE_KEYS = {"password", "pwd", "password_enc", "token"}


def mask_dict(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k.lower() in SENSITIVE_KEYS:
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = mask_dict(v)
        else:
            out[k] = v
    return out


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

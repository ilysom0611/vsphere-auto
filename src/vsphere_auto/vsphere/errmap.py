"""Map deployment failures to stable error keys + actionable hints.

vSphere fault strings are cryptic ("InsufficientResourcesFault", "Unable to
access file [ds] .vmtx"). The web UI shows a translated, actionable message
for known keys (i18n `err.<key>`) and keeps the raw text in an expandable
detail — so users see WHAT to do, not just that it failed.

classify() never raises: unknown errors fall through to the generic key.
"""
from __future__ import annotations

import re

# key -> matched substrings (case-insensitive). Order matters: first match
# wins, so specific patterns must precede generic ones.
_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # our own ValueError from clone_from_template: pinned host lacks the
    # template's datastore mount
    ("host_ds_access", ("does not have datastore",)),
    # template/disk files unreachable from the target host/datastore
    ("host_ds_access", ("unable to access file", "cannot access file", "not accessible")),
    # datastore full
    ("ds_space", (
        "insufficient disk space",
        "insufficientdisk",
        "out of disk space",
        "no space left",
        "datastore is full",
        "not enough space",
        "exceeds the maximum supported size",  # 2TB-ish vdHelper cases aside, usually capacity
    )),
    # host cpu/mem slots
    ("host_resources", (
        "insufficientresourcesfault",
        "insufficient resources",
        "insufficient memory resources",
        "insufficient cpu resources",
        "not enough licenses",
        "host does not have enough",
    )),
    ("auth", (
        "invalidlogin",
        "cannot complete login due to incorrect user name or password",
        "incorrect user name or password",
        "authentication failure",
    )),
    ("permission", ("permission to perform", "no permission", "notauthenticated", "privilege")),
    ("duplicate", ("duplicatename", "already exists")),
    ("connection", (
        "connection refused",
        "timed out",
        "timeout",
        "unreachable",
        "name or service not known",
        "getaddrinfo failed",
        "socket",
        "ssl:",
        "remote server is not a vmware",
    )),
    ("maintenance", ("in maintenance mode", "maintenancemode")),
]

# Our own ValueErrors use stable phrasing; check with regexes.
_OUR: list[tuple[str, str]] = [
    ("dc_missing", r"datacenter .* not found"),
    ("tpl_missing", r"template .* not found"),
    ("folder_missing", r"VM folder .* not found"),
    ("host_missing", r"host .* not found"),
    ("net_missing", r"network .* not found"),
    ("ds_missing", r"datastore .* not found"),
    ("prov_invalid", r"invalid provisioning"),
    ("rp_missing", r"resource pool|no usable resource pool"),
]


def classify(error: object) -> str:
    """Return a stable error key for an exception or its string."""
    s = f"{error}".strip()
    low = s.lower()
    for key, needles in _PATTERNS:
        for n in needles:
            if n in low:
                return key
    for key, rx in _OUR:
        if re.search(rx, s, re.IGNORECASE):
            return key
    return "generic"


def decorate(result: dict, exc_or_error: object) -> dict:
    """Attach 'errorKey' to a task-result dict (in place) and return it."""
    try:
        result["errorKey"] = classify(exc_or_error)
    except Exception:
        pass
    return result

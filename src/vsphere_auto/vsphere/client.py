"""vSphere connection helper with retry and insecure option.

vSphere 6.7 (and some 6.5) hosts often negotiate TLS 1.0/1.1 or use ciphers
rejected by Python 3.11 + OpenSSL 3.x at SECLEVEL 2.  The default
`ssl._create_unverified_context()` is therefore not enough — we lower the
security level and explicitly allow TLS 1.0+ so old hosts still connect.
"""
from __future__ import annotations

import logging
import ssl

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


def _create_ssl_context(insecure: bool = True) -> ssl.SSLContext | None:
    if not insecure:
        return None
    # Start from an unverified context (no cert check) then relax further
    # for 6.7 compat.  Modern OpenSSL rejects legacy ciphers at SECLEVEL 2;
    # lowering to 1 lets 6.7 handshakes succeed without weakening anything
    # beyond "already insecure" (cert verification is already off).
    try:
        ctx = ssl._create_unverified_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    # Allow TLS 1.0+ — 6.7 typically uses 1.2 but some builds fall back to 1.0
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1  # type: ignore[attr-defined]
    except Exception:
        try:
            ctx.options &= ~ssl.OP_NO_TLSv1
            ctx.options &= ~ssl.OP_NO_TLSv1_1
        except Exception:
            pass
    # Relax cipher security level for old hosts (OpenSSL 3.x).  Best-effort.
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    except Exception:
        pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect(host: str, port: int, user: str, password: str, insecure: bool = True):
    """Return ServiceInstance. Raises on failure with a helpful message."""
    try:
        from pyVim.connect import SmartConnect
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = _create_ssl_context(insecure)
    kwargs: dict = {"host": host, "port": port, "user": user, "pwd": password}
    if ctx is not None:
        kwargs["sslContext"] = ctx

    try:
        si = SmartConnect(**kwargs)
    except ssl.SSLError as e:
        # Common on 6.7 with strict ciphers / old TLS; hint the fix.
        msg = (
            f"TLS handshake failed to {host}:{port}: {e}. "
            "vSphere 6.7 uses older TLS/ciphers — the client already lowers SECLEVEL; "
            "if this persists, check network/firewall and that the host is reachable on 443."
        )
        log.warning(msg)
        raise RuntimeError(msg) from e
    except Exception as e:
        # pyVmomi wraps SOAP faults as generic Exception; surface the cause
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user}: {e}") from e

    if not si:
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user} (SmartConnect returned None)")
    return si


def disconnect(si) -> None:
    try:
        from pyVim.connect import Disconnect

        Disconnect(si)
    except Exception:
        pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def connect_with_retry(host: str, port: int, user: str, password: str, insecure: bool = True):
    return connect(host, port, user, password, insecure=insecure)

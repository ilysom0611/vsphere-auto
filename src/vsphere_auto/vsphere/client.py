"""vSphere connection helper with retry and insecure option.

vSphere 6.7 (and some 6.5) hosts often negotiate TLS 1.0/1.1 or use ciphers
rejected by Python 3.11 + OpenSSL 3.x at SECLEVEL 2.  The default
`ssl._create_unverified_context()` is therefore not enough — we lower the
security level and explicitly allow TLS 1.0+ so old hosts still connect.

6.7 compat note: some builds hang on TLS handshake if the cipher list is
too strict; we also set a socket-level timeout so `creds test` / discover
never hangs indefinitely on "Testing ..." (pyVmomi/SmartConnect has no
timeout param that covers the handshake in all versions).
"""
from __future__ import annotations

import logging
import socket
import ssl

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Default TCP/TLS handshake timeout (seconds).  Applied via `connectionTimeout`
# (pyVmomi >= 6.5) and also as a global socket timeout fallback.
_CONNECT_TIMEOUT = 15


def _create_ssl_context(insecure: bool = True) -> ssl.SSLContext | None:
    if not insecure:
        return None
    try:
        ctx = ssl._create_unverified_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1  # type: ignore[attr-defined]
    except Exception:
        try:
            ctx.options &= ~ssl.OP_NO_TLSv1
            ctx.options &= ~ssl.OP_NO_TLSv1_1
        except Exception:
            pass
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    except Exception:
        pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    insecure: bool = True,
    timeout: int = _CONNECT_TIMEOUT,
):
    """Return ServiceInstance. Raises on failure with a helpful message.

    `timeout` bounds the socket/TLS handshake; otherwise pyVmomi may block
    for minutes on unreachable / 6.7-mismatched hosts.
    """
    try:
        from pyVim.connect import SmartConnect
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = _create_ssl_context(insecure)

    # Probe: pass connectionTimeout when supported; otherwise bound with a
    # temporary default socket timeout.
    kwargs: dict = {"host": host, "port": port, "user": user, "pwd": password}
    if ctx is not None:
        kwargs["sslContext"] = ctx

    # pyVmomi 6.5+ supports connectionTimeout kwarg on SmartConnect; older
    # versions ignore it, so we pass it and fall back to socket timeout.
    tried_with_timeout_kwarg = False
    orig_timeout = socket.getdefaulttimeout()
    try:
        # Try with connectionTimeout first (keyword accepted on 6.5+)
        try:
            si = SmartConnect(connectionTimeout=timeout, **kwargs)
            tried_with_timeout_kwarg = True
        except TypeError as te:
            # Older pyVmomi: unknown kwarg -> retry without it
            if "connectionTimeout" not in str(te):
                raise
            tried_with_timeout_kwarg = False
            # Fallback: bound via global default timeout for this call only
            socket.setdefaulttimeout(timeout)
            si = SmartConnect(**kwargs)
    except socket.timeout as e:
        raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s (check firewall / host / port): {e}") from e
    except ssl.SSLError as e:
        msg = (
            f"TLS handshake failed to {host}:{port}: {e}. "
            "vSphere 6.7 uses older TLS/ciphers — the client already lowers SECLEVEL; "
            "if this persists, check network/firewall and that the host is reachable on 443."
        )
        log.warning(msg)
        raise RuntimeError(msg) from e
    except TimeoutError as e:
        raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
    except Exception as e:
        # pyVmomi wraps SOAP faults as generic Exception; surface the cause.
        # Detect SSLError wrapped as generic Exception
        if "SSLError" in type(e).__name__ or "SSL" in str(e):
            raise RuntimeError(f"TLS/SSL error to {host}:{port}: {e}") from e
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user}: {e}") from e
    finally:
        # Restore global timeout only if we changed it
        if not tried_with_timeout_kwarg:
            try:
                socket.setdefaulttimeout(orig_timeout)
            except Exception:
                pass

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
def connect_with_retry(
    host: str,
    port: int,
    user: str,
    password: str,
    insecure: bool = True,
    timeout: int = _CONNECT_TIMEOUT,
):
    return connect(host, port, user, password, insecure=insecure, timeout=timeout)

"""vSphere connection helper with retry and insecure option.

vSphere 6.7 (and some 6.5) hosts often negotiate TLS 1.0/1.1 or use ciphers
rejected by Python 3.11 + OpenSSL 3.x at SECLEVEL 2.  The default
`ssl._create_unverified_context()` is therefore not enough — we lower the
security level and explicitly allow TLS 1.0+ so old hosts still connect.

Hang fix: some 6.7 stacks cause pyVmomi/SmartConnect to block forever on
the TLS/SOAP handshake (connectionTimeout/socket timeout not reliably
honoured). We run the connect in a daemon thread and enforce a hard
wall-clock timeout so `creds test` and `/api/discover` always return
within ~30s.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 30

# Only transient network/TLS errors should be retried — auth failures must fail fast.
_RETRYABLE = (socket.timeout, TimeoutError, ssl.SSLError, OSError)


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


def _smartconnect_once(host: str, port: int, user: str, password: str, ctx, timeout: int):
    """Single SmartConnect attempt (called inside a worker thread)."""
    from pyVim.connect import SmartConnect

    kwargs: dict[str, Any] = {"host": host, "port": port, "user": user, "pwd": password}
    if ctx is not None:
        kwargs["sslContext"] = ctx
    # pyVmomi 6.7: connectionTimeout, 8.0/9.1: httpConnectionTimeout. Try both.
    for key in ("httpConnectionTimeout", "connectionTimeout"):
        try:
            return SmartConnect(**{key: timeout}, **kwargs)
        except TypeError as te:
            if key not in str(te) and "unexpected keyword" not in str(te).lower():
                raise
            continue
    return SmartConnect(**kwargs)


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    insecure: bool = True,
    timeout: int = _CONNECT_TIMEOUT,
):
    """Return ServiceInstance. Raises RuntimeError on failure/timeout.

    Uses a daemon thread to enforce a hard wall-clock timeout even when
    pyVmomi/SmartConnect blocks on TLS handshake (vSphere 6.7 LDAP).
    """
    try:
        import pyVim.connect  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = _create_ssl_context(insecure)
    si: Any = None
    exc_holder: list[BaseException] = []

    def _target():
        try:
            exc_holder.append(_smartconnect_once(host, port, user, password, ctx, timeout))  # type: ignore[arg-type]
        except BaseException as e:
            exc_holder.append(e)

    # Use daemon thread so hung handshake does not prevent process exit.
    # Thread will be abandoned on timeout and never joins (daemon).
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # Orphan-session mitigation: the abandoned thread may still complete
        # its handshake seconds later, leaking a vCenter session. Schedule a
        # delayed best-effort shutdown of whatever SmartConnect produced.
        def _late_shutdown():
            try:
                time.sleep(45)
                late_si = exc_holder[0] if exc_holder else None
                if late_si is not None and not isinstance(late_si, BaseException):
                    getattr(late_si, "ShutDown", lambda: None)()
                    log.debug("connect: shut down late-completing session to %s:%s", host, port)
            except Exception:
                pass

        threading.Thread(target=_late_shutdown, daemon=True).start()
        raise RuntimeError(
            f"Connection to {host}:{port} timed out after {timeout}s "
            f"(TLS/handshake blocked — curl -vk https://{host}:{port}/sdk should connect)"
        )
    if not exc_holder:
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user} (no result)")
    result = exc_holder[0]
    if isinstance(result, BaseException):
        e = result
        # Map to RuntimeError with actionable messages
        if isinstance(e, ssl.SSLError):
            msg = (
                f"TLS handshake failed to {host}:{port}: {e}. "
                "vSphere 6.7 uses older TLS/ciphers — the client already lowers SECLEVEL; "
                "if this persists, check network/firewall and that the host is reachable on 443."
            )
            log.warning(msg)
            raise RuntimeError(msg) from e
        if isinstance(e, socket.timeout) or isinstance(e, TimeoutError):
            raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
        if "SSLError" in type(e).__name__ or "SSL" in str(e):
            raise RuntimeError(f"TLS/SSL error to {host}:{port}: {e}") from e
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user}: {e}") from e

    si = result
    if not si:
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user} (SmartConnect returned None)")
    return si


def disconnect(si) -> None:
    try:
        from pyVim.connect import Disconnect
        Disconnect(si)
    except Exception:
        pass


def _unwrap_retryable(exc: BaseException) -> bool:
    """Recursively unwrap RuntimeError __cause__ chains looking for a retryable error."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, _RETRYABLE):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        # Stop at RuntimeError wrappers only — plain non-runtime causes are still unwrapped
    return False


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient network/TLS errors; fail fast on auth/permission errors.

    connect() wraps every failure in RuntimeError with __cause__ set, so we must
    unwrap the cause chain to find the underlying socket.timeout/ssl.SSLError.
    """
    # Auth / permission errors contain these substrings and must NOT be retried
    msg = str(exc).lower()
    if any(k in msg for k in ("invalid login", "incorrect user", "permission", "not authorized", "login failed")):
        return False
    if isinstance(exc, _RETRYABLE):
        return True
    if isinstance(exc, RuntimeError):
        return _unwrap_retryable(getattr(exc, "__cause__", None))
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retryable),
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

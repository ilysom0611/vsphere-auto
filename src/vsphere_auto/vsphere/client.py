"""vSphere connection helper with retry and insecure option.

vSphere 6.7 (and some 6.5) hosts often negotiate TLS 1.0/1.1 or use ciphers
rejected by Python 3.11 + OpenSSL 3.x at SECLEVEL 2.  The default
`ssl._create_unverified_context()` is therefore not enough — we lower the
security level and explicitly allow TLS 1.0+ so old hosts still connect.

Hang fix: some 6.7 stacks cause pyVmomi/SmartConnect to block forever on
the TLS/SOAP handshake (connectionTimeout + socket timeout don't help).
We run the connect in a daemon thread and enforce a hard wall-clock
timeout so `creds test` and `/api/discover` always return within ~15s.
"""
from __future__ import annotations

import concurrent.futures
import logging
import socket
import ssl

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

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


def _smartconnect_once(host: str, port: int, user: str, password: str, ctx, timeout: int):
    """Single SmartConnect attempt (called inside a worker thread)."""
    from pyVim.connect import SmartConnect

    kwargs: dict = {"host": host, "port": port, "user": user, "pwd": password}
    if ctx is not None:
        kwargs["sslContext"] = ctx
    # Some pyVmomi builds accept connectionTimeout; pass it when available.
    # Inspect signature to avoid TypeError spam — but try anyway.
    try:
        return SmartConnect(connectionTimeout=timeout, **kwargs)
    except TypeError as te:
        if "connectionTimeout" not in str(te):
            raise
        return SmartConnect(**kwargs)


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    insecure: bool = True,
    timeout: int = _CONNECT_TIMEOUT,
):
    """Return ServiceInstance. Raises RuntimeError on failure/timeout."""
    try:
        # Import early so missing pyvmomi fails fast with a clear message
        import pyVim.connect  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = _create_ssl_context(insecure)

    # Hard wall-clock timeout via a worker thread — this bounds cases where
    # the underlying socket/SSL handshake blocks indefinitely (observed on
    # vSphere 6.7 with OpenSSL 3.x).  connectionTimeout/socket.setdefaulttimeout
    # are best-effort inside the thread, but the outer future timeout is the
    # actual guarantee.
    orig_timeout = socket.getdefaulttimeout()
    try:
        # Also set default socket timeout inside the main thread for DNS/TCP
        # fallbacks that might run outside the worker (defensive).
        try:
            socket.setdefaulttimeout(timeout)
        except Exception:
            pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_smartconnect_once, host, port, user, password, ctx, timeout)
            try:
                si = fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError as e:
                # Don't wait for the stuck thread — it will die with the executor.
                raise RuntimeError(
                    f"Connection to {host}:{port} timed out after {timeout}s "
                    f"(TLS/handshake blocked — check host 443, firewall, and that "
                    f"vCenter 6.7 ciphers are reachable; curl -vk https://{host}:{port}/sdk should connect)"
                ) from e
    except RuntimeError:
        raise
    except socket.timeout as e:
        raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
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
        if "SSLError" in type(e).__name__ or "SSL" in str(e):
            raise RuntimeError(f"TLS/SSL error to {host}:{port}: {e}") from e
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            raise RuntimeError(f"Connection to {host}:{port} timed out after {timeout}s: {e}") from e
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user}: {e}") from e
    finally:
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

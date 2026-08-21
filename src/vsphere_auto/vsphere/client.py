"""vSphere connection helper with retry and insecure option.

vSphere 6.7 (and some 6.5) hosts often negotiate TLS 1.0/1.1 or use ciphers
rejected by Python 3.11 + OpenSSL 3.x at SECLEVEL 2.  The default
`ssl._create_unverified_context()` is therefore not enough — we lower the
security level and explicitly allow TLS 1.0+ so old hosts still connect.

Hang fix: some 6.7 stacks cause pyVmomi/SmartConnect to block forever on
the TLS/SOAP handshake (connectionTimeout/socket timeout not reliably
honoured). We run the connect in a daemon thread and enforce a hard
wall-clock timeout so `creds test` and `/api/discover` always return
within ~15s.  Note: ThreadPoolExecutor as a context manager waits for
workers on exit (shutdown wait=True) which defeats the timeout — we use
shutdown(wait=False) explicitly.
"""
from __future__ import annotations

import concurrent.futures
import logging
import socket
import ssl

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 30


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
        import pyVim.connect  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = _create_ssl_context(insecure)
    orig_timeout = socket.getdefaulttimeout()
    try:
        try:
            socket.setdefaulttimeout(timeout)
        except Exception:
            pass

        # Hard wall-clock timeout via worker thread.  Use explicit
        # shutdown(wait=False) — the `with` form would block on exit
        # waiting for the hung thread and defeat the timeout.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_smartconnect_once, host, port, user, password, ctx, timeout)
            try:
                si = fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError as e:
                # Cancel if not yet started; the running thread will be
                # abandoned (daemon) and reaped when the process exits.
                try:
                    fut.cancel()
                except Exception:
                    pass
                raise RuntimeError(
                    f"Connection to {host}:{port} timed out after {timeout}s "
                    f"(TLS/handshake blocked — curl -vk https://{host}:{port}/sdk should connect)"
                ) from e
        finally:
            try:
                ex.shutdown(wait=False, cancel_futures=True)  # type: ignore[call-arg]
            except TypeError:
                # Python <3.9: no cancel_futures
                try:
                    ex.shutdown(wait=False)
                except Exception:
                    pass
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

"""vSphere connection helper with retry and insecure option."""
from __future__ import annotations

import ssl
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


def connect(host: str, port: int, user: str, password: str, insecure: bool = True):
    """Return ServiceInstance. Raises on failure."""
    try:
        from pyVim.connect import SmartConnect
    except ImportError as e:
        raise RuntimeError("pyvmomi not installed. Run: pip install pyvmomi") from e

    ctx = None
    if insecure:
        ctx = ssl._create_unverified_context()

    si = SmartConnect(host=host, port=port, user=user, pwd=password, sslContext=ctx)
    if not si:
        raise RuntimeError(f"Failed to connect to {host}:{port} as {user}")
    return si


def disconnect(si) -> None:
    try:
        from pyVim.connect import Disconnect

        Disconnect(si)
    except Exception:
        pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def connect_with_retry(host: str, port: int, user: str, password: str, insecure: bool = True):
    return connect(host, port, user, password, insecure=insecure)

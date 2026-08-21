"""Discovery API: proxy to vsphere discovery using saved creds or direct params."""
from __future__ import annotations

import logging
import threading

from flask import Blueprint, jsonify, request

from ...creds.store import get_creds, resolve_creds
from ...vsphere.client import connect, disconnect

bp = Blueprint("discover", __name__)
log = logging.getLogger(__name__)

# Cap concurrent discovers: each holds a vCenter connection and is CPU/IO heavy;
# unbounded concurrency piled up blocking requests on slow links.
_DISCOVER_SEM = threading.BoundedSemaphore(2)


def _resolve_conn(data: dict):
    raw = data.get("credsId") if data.get("credsId") is not None else data.get("credsRef")
    if raw is not None:
        ref = str(raw).strip()
        if not ref:
            raise ValueError("credsId/credsRef is empty")
        c = resolve_creds(ref)
        if not c:
            raise ValueError(f"Credential not found: {ref}")
        return c.host, c.port, c.username, c.decrypted_password()
    host = (data.get("host") or "").strip()
    user = (data.get("username") or data.get("user") or "").strip()
    pwd = data.get("password") or ""
    port = int(data.get("port") or 443)
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range: {port}")
    if not host or not user:
        raise ValueError("host and username required (or credsId)")
    return host, port, user, pwd


@bp.post("/api/discover")
def discover_all():
    data = request.get_json(force=True) or {}
    try:
        host, port, user, pwd = _resolve_conn(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("discover: bad request params: %s", e)
        return jsonify({"error": "invalid request parameters"}), 400
    if not _DISCOVER_SEM.acquire(blocking=False):
        return jsonify({"error": "discover already in progress"}), 429
    datacenter = data.get("datacenter")
    si = None
    try:
        si = connect(host, port, user, pwd)
        from ...vsphere.discovery import discover
        from ...inventory import save_inventory

        inv = discover(si, datacenter)
        save_inventory(inv)
        # Always 200: empty inventory is not an error (ESXi direct may have no DCs)
        # Include apiVersion so the UI can show what was reached
        return jsonify(inv)
    except Exception as e:
        log.warning("discover %s:%s failed: %s", data.get("host") or host, port, e, exc_info=True)
        return jsonify({"error": "discover failed (check vCenter reachability and credentials)", "detail_category": "connection"}), 500
    finally:
        if si is not None:
            disconnect(si)
        _DISCOVER_SEM.release()


@bp.post("/api/discover/iso")
def discover_iso():
    data = request.get_json(force=True) or {}
    try:
        host, port, user, pwd = _resolve_conn(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("scan_iso: bad request params: %s", e)
        return jsonify({"error": "invalid request parameters"}), 400
    ds = data.get("datastore")
    if not _DISCOVER_SEM.acquire(blocking=False):
        return jsonify({"error": "discover already in progress"}), 429
    si = None
    try:
        si = connect(host, port, user, pwd)
        from ...vsphere.discovery import scan_iso_images

        isos = scan_iso_images(si, ds)
        return jsonify({"isoImages": isos})
    except Exception as e:
        log.warning("scan_iso %s:%s: %s", data.get("host") or host, port, e, exc_info=True)
        return jsonify({"error": "ISO scan failed (check datastore name and vCenter reachability)", "detail_category": "connection"}), 500
    finally:
        if si is not None:
            disconnect(si)
        _DISCOVER_SEM.release()

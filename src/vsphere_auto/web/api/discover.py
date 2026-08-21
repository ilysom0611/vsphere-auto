"""Discovery API: proxy to vsphere discovery using saved creds or direct params."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ...creds.store import get_creds, resolve_creds
from ...vsphere.client import connect, disconnect

bp = Blueprint("discover", __name__)
log = logging.getLogger(__name__)


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
        return jsonify({"error": str(e)}), 400
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
        return jsonify({"error": f"Discover failed: {e!s}", "detail": str(e)}), 500
    finally:
        if si is not None:
            disconnect(si)


@bp.post("/api/discover/iso")
def discover_iso():
    data = request.get_json(force=True) or {}
    try:
        host, port, user, pwd = _resolve_conn(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    ds = data.get("datastore")
    si = None
    try:
        si = connect(host, port, user, pwd)
        from ...vsphere.discovery import scan_iso_images

        isos = scan_iso_images(si, ds)
        return jsonify({"isoImages": isos})
    except Exception as e:
        log.warning("scan_iso %s:%s: %s", data.get("host") or host, port, e, exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if si is not None:
            disconnect(si)

"""Discovery API: proxy to vsphere discovery using saved creds or direct params."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...creds.store import get_creds, resolve_creds
from ...vsphere.client import connect, disconnect

bp = Blueprint("discover", __name__)


def _resolve_conn(data: dict):
    creds_id = data.get("credsId") or data.get("credsRef")
    if creds_id is not None:
        c = resolve_creds(str(creds_id)) or get_creds(int(creds_id)) if str(creds_id).isdigit() else resolve_creds(str(creds_id))
        # fallback try both
        if not c and str(creds_id).isdigit():
            from ...creds.store import get_creds as gc

            c = gc(int(creds_id))
        if not c:
            raise ValueError(f"Credential not found: {creds_id}")
        return c.host, c.port, c.username, c.decrypted_password()
    # direct
    host = data.get("host") or ""
    user = data.get("username") or data.get("user") or ""
    pwd = data.get("password") or ""
    port = int(data.get("port") or 443)
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
    datacenter = data.get("datacenter")
    try:
        si = connect(host, port, user, pwd)
        try:
            from ...vsphere.discovery import discover
            from ...inventory import save_inventory

            inv = discover(si, datacenter)
            save_inventory(inv)
            return jsonify(inv)
        finally:
            disconnect(si)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.post("/api/discover/iso")
def discover_iso():
    data = request.get_json(force=True) or {}
    try:
        host, port, user, pwd = _resolve_conn(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    ds = data.get("datastore")
    try:
        si = connect(host, port, user, pwd)
        try:
            from ...vsphere.discovery import scan_iso_images

            isos = scan_iso_images(si, ds)
            return jsonify({"isoImages": isos})
        finally:
            disconnect(si)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

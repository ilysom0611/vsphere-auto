"""Creds API blueprint: CRUD + test connection."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ...creds.store import list_creds, get_creds, create_creds, update_creds, delete_creds

log = logging.getLogger(__name__)

bp = Blueprint("creds", __name__)

PORT_MIN, PORT_MAX = 1, 65535


def _parse_port(value, default: int = 443) -> int:
    """Parse a request-supplied port; raises ValueError with a safe message."""
    if value is None or value == "":
        return default
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("port must be an integer")
    if not (PORT_MIN <= port <= PORT_MAX):
        raise ValueError(f"port must be between {PORT_MIN} and {PORT_MAX}")
    return port


@bp.get("/api/creds")
def list_api():
    creds = list_creds()
    return jsonify([c.to_safe_dict() for c in creds])


@bp.post("/api/creds")
def create_api():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    host = (data.get("host") or "").strip()
    username = (data.get("username") or data.get("user") or "").strip()
    password = data.get("password") or ""
    ctype = data.get("type") or "vcenter"
    if not name or not host or not username:
        return jsonify({"error": "name, host, username required"}), 400
    try:
        port = _parse_port(data.get("port"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        c = create_creds(name, host, username, password, port, ctype)
    except Exception as e:
        msg = str(e)
        if "UNIQUE" in msg or "unique" in msg.lower():
            return jsonify({"error": f"Name already exists: {name}"}), 409
        log.warning("create credential %r failed: %s", name, msg)
        return jsonify({"error": "could not save credential (check name/port values)"}), 400
    return jsonify(c.to_safe_dict()), 201


@bp.put("/api/creds/<int:cid>")
def update_api(cid: int):
    data = request.get_json(force=True) or {}
    # password: if omitted -> unchanged; if "" -> clear; if value -> new
    pwd = data.get("password") if "password" in data else None
    port = None
    if "port" in data and data["port"] is not None:
        try:
            port = _parse_port(data["port"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    try:
        c = update_creds(
            cid,
            name=data.get("name"),
            host=data.get("host"),
            port=port,
            username=data.get("username") or data.get("user"),
            password=pwd,
            cred_type=data.get("type"),
        )
    except Exception as e:
        log.warning("update credential %d failed: %s", cid, e)
        return jsonify({"error": "could not update credential (check field values)"}), 400
    if not c:
        return jsonify({"error": "Not found"}), 404
    return jsonify(c.to_safe_dict())


@bp.delete("/api/creds/<int:cid>")
def delete_api(cid: int):
    ok = delete_creds(cid)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@bp.post("/api/creds/<int:cid>/test")
def test_api(cid: int):
    c = get_creds(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    pwd = c.decrypted_password()
    # allow override password in body for test without saving
    data = request.get_json(silent=True) or {}
    if data.get("password"):
        pwd = data["password"]
    from ...vsphere.discovery import test_connection

    try:
        res = test_connection(c.host, c.port, c.username, pwd)
        return jsonify(res)
    except Exception as e:
        log.warning("test credential %d (%s:%s) failed: %s", cid, c.host, c.port, e)
        return jsonify({"ok": False, "error": "connection test failed (check host/port/credentials)", "detail_category": "connection"}), 200


@bp.post("/api/creds/test")
def test_direct():
    """Test connection without saving (direct host/user/password)."""
    data = request.get_json(force=True) or {}
    host = (data.get("host") or "").strip()
    user = (data.get("username") or data.get("user") or "").strip()
    pwd = data.get("password") or ""
    if not host or not user:
        return jsonify({"error": "host and username required"}), 400
    try:
        port = _parse_port(data.get("port"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    from ...vsphere.discovery import test_connection

    try:
        res = test_connection(host, port, user, pwd)
        return jsonify(res)
    except Exception as e:
        log.warning("direct test %s:%s failed: %s", host, port, e)
        return jsonify({"ok": False, "error": "connection test failed (check host/port/credentials)", "detail_category": "connection"}), 200

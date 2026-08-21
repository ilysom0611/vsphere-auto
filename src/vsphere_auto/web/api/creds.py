"""Creds API blueprint: CRUD + test connection."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...creds.store import list_creds, get_creds, create_creds, update_creds, delete_creds

bp = Blueprint("creds", __name__)


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
    port = int(data.get("port") or 443)
    ctype = data.get("type") or "vcenter"
    if not name or not host or not username:
        return jsonify({"error": "name, host, username required"}), 400
    try:
        c = create_creds(name, host, username, password, port, ctype)
    except Exception as e:
        msg = str(e)
        if "UNIQUE" in msg or "unique" in msg.lower():
            return jsonify({"error": f"Name already exists: {name}"}), 409
        return jsonify({"error": msg}), 400
    return jsonify(c.to_safe_dict()), 201


@bp.get("/api/creds/<int:cid>")
def get_api(cid: int):
    c = get_creds(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    return jsonify(c.to_safe_dict())


@bp.put("/api/creds/<int:cid>")
def update_api(cid: int):
    data = request.get_json(force=True) or {}
    # password: if omitted -> unchanged; if "" -> clear; if value -> new
    pwd = data.get("password") if "password" in data else None
    c = update_creds(
        cid,
        name=data.get("name"),
        host=data.get("host"),
        port=int(data["port"]) if "port" in data and data["port"] is not None else None,
        username=data.get("username") or data.get("user"),
        password=pwd,
        cred_type=data.get("type"),
    )
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
        return jsonify({"ok": False, "error": str(e)}), 200


@bp.post("/api/creds/test")
def test_direct():
    """Test connection without saving (direct host/user/password)."""
    data = request.get_json(force=True) or {}
    host = (data.get("host") or "").strip()
    user = (data.get("username") or data.get("user") or "").strip()
    pwd = data.get("password") or ""
    port = int(data.get("port") or 443)
    if not host or not user:
        return jsonify({"error": "host and username required"}), 400
    from ...vsphere.discovery import test_connection

    try:
        res = test_connection(host, port, user, pwd)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

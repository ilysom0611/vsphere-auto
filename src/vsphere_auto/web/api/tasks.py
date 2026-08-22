"""Tasks/batches API."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from ...batch.state import list_batches, list_tasks, get_task

bp = Blueprint("tasks_api", __name__)


def _strip_config(batch: dict[str, Any]) -> dict[str, Any]:
    """Remove config_json from a batch dict before it reaches clients.

    config_json may contain connection metadata (host/user); secrets are
    already redacted at write time but there is no reason to expose it here.
    """
    out = dict(batch)
    out.pop("config_json", None)
    return out


@bp.get("/api/batches")
def list_batches_api():
    return jsonify([_strip_config(b) for b in list_batches()])


@bp.get("/api/batches/<bid>")
def get_batch_api(bid: str):
    for b in list_batches():
        if b["id"] == bid:
            tasks = list_tasks(bid)
            return jsonify({**_strip_config(b), "tasks": tasks})
    return jsonify({"error": "Not found"}), 404


@bp.get("/api/tasks")
def list_tasks_api():
    batch_id = request.args.get("batch_id") or request.args.get("batchId")
    tasks = list_tasks(batch_id)
    # Enrich running/pending rows with live "current operation" state from the
    # in-process registry (same process runs the deploy threads). Finished
    # rows keep their authoritative DB result only.
    from .. import progress

    live = progress.snapshot()
    for t in tasks:
        if t.get("status") in ("running", "pending"):
            entry = (live.get(str(t.get("batch_id", ""))) or {}).get(t.get("vm_name", ""))
            if entry:
                t["live"] = entry
    return jsonify(tasks)


@bp.get("/api/tasks/<tid>")
def get_task_api(tid: str):
    t = get_task(tid)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)

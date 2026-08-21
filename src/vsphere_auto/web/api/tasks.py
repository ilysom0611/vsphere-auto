"""Tasks/batches API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...batch.state import list_batches, list_tasks, get_task

bp = Blueprint("tasks_api", __name__)


@bp.get("/api/batches")
def list_batches_api():
    return jsonify(list_batches())


@bp.get("/api/batches/<bid>")
def get_batch_api(bid: str):
    from ...batch.state import list_batches as lb

    for b in lb():
        if b["id"] == bid:
            tasks = list_tasks(bid)
            return jsonify({**b, "tasks": tasks})
    return jsonify({"error": "Not found"}), 404


@bp.get("/api/tasks")
def list_tasks_api():
    batch_id = request.args.get("batch_id") or request.args.get("batchId")
    return jsonify(list_tasks(batch_id))


@bp.get("/api/tasks/<tid>")
def get_task_api(tid: str):
    t = get_task(tid)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)


@bp.get("/api/health")
def health():
    return jsonify({"ok": True})

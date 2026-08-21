"""Flask app factory."""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, render_template, request
from pathlib import Path

log = logging.getLogger(__name__)


def create_app() -> Flask:
    # templates/static live next to this file: src/vsphere_auto/web/templates, static
    base = Path(__file__).parent
    app = Flask(__name__, template_folder=str(base / "templates"), static_folder=str(base / "static"))

    # Blueprints
    from .api.creds import bp as creds_bp
    from .api.discover import bp as discover_bp
    from .api.deploy import bp as deploy_bp
    from .api.tasks import bp as tasks_bp

    app.register_blueprint(creds_bp)
    app.register_blueprint(discover_bp)
    app.register_blueprint(deploy_bp)
    app.register_blueprint(tasks_bp)

    # Best-effort recovery: mark tasks/batches left in running/pending by a
    # previous crash as interrupted so the UI does not show phantom runs.
    try:
        from ..batch.state import recover_interrupted

        tasks_marked, batches_marked = recover_interrupted()
        if tasks_marked or batches_marked:
            log.info("startup recovery: marked %d task(s), %d batch(es) as interrupted", tasks_marked, batches_marked)
    except Exception as e:
        log.warning("startup recovery of interrupted batches failed: %s", e)

    # Optional API-token auth. When VSPHERE_API_TOKEN is unset/empty the app
    # behaves exactly as before (loopback-bound deployments, no auth).
    api_token = (os.environ.get("VSPHERE_API_TOKEN") or "").strip()
    if api_token:

        @app.before_request
        def _check_api_token():
            provided = request.headers.get("X-API-Token", "").strip()
            if not provided:
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    provided = auth[len("Bearer "):].strip()
            if provided != api_token:
                return jsonify({"error": "unauthorized: missing or invalid API token"}), 401
            return None

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/settings")
    def settings():
        return render_template("settings.html")

    @app.get("/tasks")
    def tasks_page():
        return render_template("tasks.html")

    @app.get("/api/health")
    def health_root():
        return {"ok": True}

    return app

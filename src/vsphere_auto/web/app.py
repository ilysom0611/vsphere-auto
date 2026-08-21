"""Flask app factory."""
from __future__ import annotations

from flask import Flask, render_template, send_from_directory
from pathlib import Path


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

"""Flask front end.

Three real defects in the previous version are fixed here.

1. ``/analyze`` accepted any absolute path from the request body and read it,
   so anyone who could reach the port could walk the whole filesystem. The
   path is now confined to a configurable workspace root.
2. ``/api/file/<filename>`` re-parsed the hardcoded ``sample_legacy_codebase``
   directory regardless of what had been analysed, so the source shown was
   from the wrong project. It now serves the file that was actually analysed.
3. The whole result lived in one module-level global, so two users clobbered
   each other and a restart lost everything. Runs are now keyed and cached.
"""

from __future__ import annotations

import html
import os
import threading
import uuid
from typing import Dict, Optional

from flask import Flask, abort, jsonify, render_template, request, send_file

from ..analysis.engine import Analysis, analyse
from ..languages import language_table
from ..models import AnalysisConfig
from ..report import html_report, markdown


class Workspace:
    """Confines every path the server will touch to one root directory."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)

    def resolve(self, candidate: str) -> str:
        """Absolute path for ``candidate``, or raise if it escapes the root."""
        if not candidate:
            raise ValueError("No path given.")
        target = candidate if os.path.isabs(candidate) else \
            os.path.join(self.root, candidate)
        target = os.path.realpath(target)
        if target != self.root and not target.startswith(self.root + os.sep):
            raise PermissionError(
                f"Path is outside the workspace root ({self.root}).")
        return target


class Store:
    """Analyses kept in memory, keyed so concurrent users do not collide."""

    def __init__(self, limit: int = 8):
        self._items: Dict[str, Analysis] = {}
        self._order: list = []
        self._lock = threading.Lock()
        self.limit = limit
        self.latest: Optional[str] = None

    def put(self, analysis: Analysis) -> str:
        key = uuid.uuid4().hex[:12]
        with self._lock:
            self._items[key] = analysis
            self._order.append(key)
            while len(self._order) > self.limit:
                self._items.pop(self._order.pop(0), None)
            self.latest = key
        return key

    def get(self, key: Optional[str]) -> Optional[Analysis]:
        with self._lock:
            if key:
                return self._items.get(key)
            return self._items.get(self.latest) if self.latest else None


def create_app(workspace_root: Optional[str] = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    workspace = Workspace(
        workspace_root
        or os.environ.get("CODECARTOGRAPHER_ROOT")
        or os.getcwd())
    store = Store()
    app.config["WORKSPACE"] = workspace
    app.config["STORE"] = store

    # ---------------------------------------------------------------- pages

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            workspace=workspace.root,
            languages=language_table(),
            suggestions=_suggest(workspace.root),
        )

    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html", run=request.args.get("run", ""))

    @app.route("/file")
    def file_view():
        analysis = store.get(request.args.get("run"))
        if analysis is None:
            abort(404, "No analysis has been run yet.")
        rel = request.args.get("path", "")
        sf = analysis.file(rel)
        if sf is None:
            abort(404, f"{rel} was not part of the last analysis.")
        return render_template(
            "file.html",
            file=sf.to_dict(include_source=True),
            complexity=analysis.complexity.get(rel, {}),
            risk=analysis.risk.get(rel, {}),
            classifications=analysis.classifications.get(rel, []),
            smells=[s for s in analysis.smells if s["file"] == rel],
            rules=(analysis.business.get("by_file") or {}).get(rel, []),
            run=request.args.get("run", ""),
        )

    # ------------------------------------------------------------------ api

    @app.post("/analyze")
    def start_analysis():
        payload = request.get_json(silent=True) or {}
        raw_path = (payload.get("directory_path") or "").strip()
        try:
            target = workspace.resolve(raw_path)
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not os.path.isdir(target):
            return jsonify({"error": f"Not a directory: {raw_path}"}), 404

        config = AnalysisConfig(
            root=target,
            languages=payload.get("languages") or None,
            use_git_history=bool(payload.get("use_git_history", True)),
        )
        if payload.get("exclude"):
            config.exclude_globs = list(config.exclude_globs) + \
                [g.strip() for g in str(payload["exclude"]).split(",") if g.strip()]

        try:
            analysis = analyse(config)
        except Exception as exc:
            return jsonify({"error": f"Analysis failed: {exc}"}), 500

        if not analysis.files:
            return jsonify({
                "error": "No source files this build understands were found "
                         "in that directory.",
                "supported": [l["label"] for l in language_table()],
            }), 422

        key = store.put(analysis)
        return jsonify({
            "success": True,
            "run": key,
            "overview": analysis.overview(),
        })

    @app.get("/api/analysis")
    def api_analysis():
        analysis = store.get(request.args.get("run"))
        if analysis is None:
            return jsonify({"error": "No analysis has been run yet."}), 404
        return jsonify(analysis.to_dict())

    @app.get("/api/file")
    def api_file():
        analysis = store.get(request.args.get("run"))
        if analysis is None:
            return jsonify({"error": "No analysis has been run yet."}), 404
        rel = request.args.get("path", "")
        sf = analysis.file(rel)
        if sf is None:
            return jsonify({"error": f"Unknown file: {rel}"}), 404
        return jsonify({
            "file": sf.to_dict(include_source=True),
            "complexity": analysis.complexity.get(rel, {}),
            "risk": analysis.risk.get(rel, {}),
            "classifications": analysis.classifications.get(rel, []),
            "smells": [s for s in analysis.smells if s["file"] == rel],
        })

    @app.get("/api/languages")
    def api_languages():
        return jsonify(language_table())

    @app.get("/export/<kind>")
    def export(kind: str):
        analysis = store.get(request.args.get("run"))
        if analysis is None:
            abort(404, "No analysis has been run yet.")
        name = os.path.basename(analysis.config.root) or "codebase"
        if kind == "json":
            return app.response_class(
                _json_dump(analysis),
                mimetype="application/json",
                headers={"Content-Disposition":
                         f'attachment; filename="{name}-analysis.json"'})
        if kind == "markdown":
            return app.response_class(
                markdown.render(analysis),
                mimetype="text/markdown",
                headers={"Content-Disposition":
                         f'attachment; filename="{name}-report.md"'})
        if kind == "html":
            return app.response_class(
                html_report.render(analysis),
                mimetype="text/html",
                headers={"Content-Disposition":
                         f'attachment; filename="{name}-report.html"'})
        abort(404, f"Unknown export format: {kind}")

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": str(error)}), 404
        return render_template("error.html", message=str(error)), 404

    return app


def _json_dump(analysis: Analysis) -> str:
    import json
    return json.dumps(analysis.to_dict(), indent=2, default=str)


def _suggest(root: str, limit: int = 12) -> list:
    """Directories in the workspace worth offering as a starting point."""
    out = []
    try:
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if name.startswith(".") or not os.path.isdir(path):
                continue
            out.append(name)
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


app = None  # created by the CLI so the workspace root can be configured

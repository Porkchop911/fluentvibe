"""Stdlib HTTP server for the local workspace setup helper."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import service

STATIC_DIR = Path(__file__).resolve().parent / "static"


def serve_workspace_app(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), WorkspaceAppHandler)
    url = f"http://{host}:{port}"
    print(f"Workspace setup app: {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


class WorkspaceAppHandler(BaseHTTPRequestHandler):
    server_version = "fluentvibe-workspace-app/0.1"

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        target = STATIC_DIR / ("index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/"))
        if parsed.path.startswith("/api/") or not target.exists():
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/workspaces":
                self._json(service.list_workspaces())
            elif parsed.path == "/api/configurations":
                self._json(service.list_configurations())
            elif parsed.path == "/api/configuration":
                qs = parse_qs(parsed.query)
                self._json(service.configuration_detail(guid=_first(qs, "guid")))
            elif parsed.path == "/api/workspace":
                qs = parse_qs(parsed.query)
                self._json(service.workspace_detail(
                    name=_first(qs, "name"),
                    guid=_first(qs, "guid"),
                ))
            elif parsed.path == "/api/labware":
                qs = parse_qs(parsed.query)
                self._json(service.search_labware(
                    query=_first(qs, "query") or "",
                    category=_first(qs, "category"),
                    limit=int(_first(qs, "limit") or 50),
                ))
            elif parsed.path == "/api/liquid-classes":
                self._json(service.list_liquid_classes())
            elif parsed.path == "/api/catalog-info":
                self._json(service.catalog_info())
            elif parsed.path == "/api/capabilities":
                self._json(service.capabilities())
            elif parsed.path == "/api/profiles":
                self._json(service.list_profiles())
            elif parsed.path == "/api/profile":
                qs = parse_qs(parsed.query)
                self._json(service.load_profile(_first(qs, "name") or ""))
            elif parsed.path == "/api/job":
                qs = parse_qs(parsed.query)
                self._json(service.job_status(_first(qs, "id") or ""))
            elif parsed.path == "/api/suggest-roles":
                qs = parse_qs(parsed.query)
                self._json(service.suggest_roles(
                    workspace_name=_first(qs, "workspace_name") or _first(qs, "name"),
                    workspace_guid=_first(qs, "workspace_guid") or _first(qs, "guid"),
                ))
            else:
                self._static(parsed.path)
        except Exception as exc:
            self._json({"ok": False, "message": str(exc)}, status=400)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/save-profile":
                self._json(service.save_profile(self._read_json()))
            elif parsed.path == "/api/propose-workspace-modules":
                self._json(service.propose_workspace_modules(self._read_json()))
            elif parsed.path.startswith("/api/jobs/"):
                kind = parsed.path.rsplit("/", 1)[-1]
                self._json(service.submit_job(kind, self._read_json()))
            else:
                self._json({"ok": False, "message": "Not found"}, status=404)
        except Exception as exc:
            self._json({"ok": False, "message": str(exc)}, status=400)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the terminal readable; API errors are returned as JSON.
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents and target != static_root:
            self._json({"ok": False, "message": "Not found"}, status=404)
            return
        if not target.exists() or not target.is_file():
            self._json({"ok": False, "message": "Not found"}, status=404)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _first(qs: dict[str, list[str]], key: str) -> str | None:
    values = qs.get(key)
    return values[0] if values else None

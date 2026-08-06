"""
Clew Web UI Server — v2.2.0.

Replaces the legacy PySide6 / QWebEngineView desktop GUI with a
plain HTTP server that serves:

  • Static frontend assets  →  GET /, /app.js, /style.css, /apple-design.css, /assets/*
  • Clew JSON REST API      →  /api/*          (delegated to ClewAPIHandler)
  • Server-Sent Events      →  /api/chat/stream, /api/agent/stream (SSE)

Run it from the CLI:

    clew                       # default port 18732, host 127.0.0.1
    clew --port 8000           # custom port
    clew --host 0.0.0.0        # share on LAN
    clew --project /path/to/x  # open a project

Then point a browser at http://127.0.0.1:18732/  and the GUI loads.

Architecture
------------
    ┌─────────────────────────────────────────────┐
    │  Browser  →  http://127.0.0.1:PORT/         │
    │                                             │
    │  ClewWebServer  (HTTPServer, threaded)      │
    │   └─ ClewWebHandler (subclass of            │
    │        ClewAPIHandler)                      │
    │       ├─ do_GET  → static OR super().do_GET │
    │       ├─ do_POST → super().do_POST (API)    │
    │       └─ do_DELETE / OPTIONS → super()      │
    └─────────────────────────────────────────────┘

Design notes
------------
* Zero Qt / PySide6 dependency. Pure stdlib http.server.
* :class:`ClewWebHandler` subclasses :class:`clew.api_server.ClewAPIHandler`
  so every REST + SSE endpoint stays identical to the legacy embedded
  API server — the same handler code handles both static and API paths.
* Static files are served relative to ``clew/web/`` so the existing
  HTML/CSS/JS frontend keeps working — only the QWebChannel script
  tag was removed from ``index.html``.
* The same auth bearer token that protected the legacy HTTP API
  protects the new one — it is shipped to the browser via
  ``GET /api/status`` in the initial handshake.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import urlparse

from .api_server import ClewAPIServer, ClewAPIHandler, ServerContext, _find_free_port
from .utils import setup_logging

logger = logging.getLogger(__name__)

__version__ = "2.2.0"

# Default port — kept identical to the legacy embedded API server so
# existing users / scripts that hit ``http://127.0.0.1:18732`` keep
# working without configuration changes.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18732


# ── Static-file helpers ────────────────────────────────────────────────

def _web_dir() -> Path:
    """Directory that holds ``index.html`` / ``app.js`` / ``style.css``."""
    return Path(__file__).resolve().parent / "web"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".mjs":  "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".ico":  "image/x-icon",
    ".icns": "application/octet-stream",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
    ".otf":  "font/otf",
    ".map":  "application/json; charset=utf-8",
}


def _safe_static_path(requested: str) -> Optional[Path]:
    """Resolve *requested* to a real file under ``web/`` or ``assets/``.

    Returns ``None`` if the path escapes the sandbox root or doesn't
    exist. Used by :class:`ClewWebHandler` for non-API GET requests.
    """
    if not requested or requested == "/":
        requested = "/index.html"
    # Strip query string + fragment
    requested = requested.split("?", 1)[0].split("#", 1)[0]
    rel = requested.lstrip("/")
    web_root = _web_dir().resolve()
    asset_root = _assets_dir().resolve()

    # Try clew/web/ first.
    candidate = (web_root / rel).resolve()
    try:
        candidate.relative_to(web_root)
    except ValueError:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate

    # Fallback: try clew/assets/. This lets the HTML reference
    # /assets/logo.png without needing a copy in clew/web/assets/.
    # We deliberately try BOTH web/ and assets/ for the SAME relative
    # path — so /assets/logo.png resolves to clew/assets/assets/logo.png
    # if the user nested it that way, OR clew/assets/logo.png directly.
    asset_candidate = (asset_root / rel).resolve()
    try:
        asset_candidate.relative_to(asset_root)
    except ValueError:
        return None
    if asset_candidate.exists() and asset_candidate.is_file():
        return asset_candidate
    # Last try: strip a leading "assets/" segment, since the URL
    # /assets/logo.png is conventionally meant to map to
    # clew/assets/logo.png (not clew/assets/assets/logo.png).
    if rel.startswith("assets/"):
        direct = (asset_root / rel[len("assets/"):]).resolve()
        try:
            direct.relative_to(asset_root)
        except ValueError:
            return None
        if direct.exists() and direct.is_file():
            return direct
    return None


def _content_type_for(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


# ── Combined handler (static + API) ────────────────────────────────────

class ClewWebHandler(ClewAPIHandler):
    """Single handler that serves static files AND the REST + SSE API.

    Subclasses :class:`clew.api_server.ClewAPIHandler` so every API
    endpoint works unchanged. Only ``do_GET`` is overridden — it
    peeks at the path; if it doesn't start with ``/api/``, the
    request is served as a static file. Otherwise it falls through
    to the parent implementation.
    """

    protocol_version = "HTTP/1.1"

    # Suppress default stderr logging — keep the console clean.
    def log_message(self, fmt, *args):
        logger.debug("[web] " + fmt, *args)

    # ── Static GET paths fall through here; /api/* goes to parent ──
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return super().do_GET()
        self._serve_static(path)

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            # Parent doesn't implement HEAD — fall back to GET behaviour.
            return super().do_GET()
        self._serve_static(path, head_only=True)

    # ── Static serving ───────────────────────────────────────────
    def _serve_static(self, path: str, head_only: bool = False) -> None:
        file_path = _safe_static_path(path)
        if file_path is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            data = file_path.read_bytes()
        except OSError as e:
            logger.warning("[web] failed to read %s: %s", file_path, e)
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", _content_type_for(file_path))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


# ── Threaded server ────────────────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ── Top-level web server ───────────────────────────────────────────────

class ClewWebServer:
    """Bootstraps the static + API server in a single process.

    Lifecycle::

        srv = ClewWebServer(host='127.0.0.1', port=18732, project='/path')
        srv.start()           # non-blocking — runs in a daemon thread
        ...
        srv.stop()            # graceful shutdown

    Or use the CLI::

        python -m clew.web_server --port 18732
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: Optional[int] = None,
        project: Optional[str] = None,
        open_browser: bool = True,
    ) -> None:
        self.host = host or DEFAULT_HOST
        # Try the requested port; if busy, scan forward up to +100.
        self.port = port or _find_free_port(DEFAULT_PORT)
        self.project = project
        self.open_browser = open_browser

        # The API server carries ServerContext (registry, config, agent
        # runtime). We reuse it so all the existing REST/SSE endpoints
        # work unchanged.
        self._api = ClewAPIServer(port=self.port)
        # Make sure the project root is set on the shared context so
        # /api/status reports it correctly.
        if project:
            try:
                self._api.ctx.config["project_root"] = str(project)
                # Persist it so the next launch remembers.
                from .api_server import _save_config
                _save_config(self._api.ctx.config)
            except Exception as e:
                logger.warning("[web_server] failed to persist project_root: %s", e)

        # Wire the shared ServerContext onto ClewWebHandler (inherited
        # from ClewAPIHandler.ctx — class-level attribute).
        ClewAPIHandler.ctx = self._api.ctx

        # Build a threaded HTTP server that serves both static + /api/*
        # via ClewWebHandler (which inherits all API behaviour).
        self._http = ThreadedHTTPServer((self.host, self.port), ClewWebHandler)
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        """Start serving in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._http.serve_forever,
            name="clew-web-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[web_server] Clew v%s listening on http://%s:%d",
            __version__, self.host, self.port,
        )
        if self.open_browser:
            try:
                webbrowser.open(f"http://{self.host}:{self.port}/")
            except Exception:
                pass  # headless environments

    def stop(self) -> None:
        """Stop serving and release the port."""
        try:
            self._api.stop()
        except Exception:
            pass
        try:
            self._http.shutdown()
            self._http.server_close()
        except Exception:
            pass
        logger.info("[web_server] stopped")

    def serve_forever(self) -> None:
        """Block the calling thread until interrupted (Ctrl+C).

        Use this for the CLI entry point so the process stays alive.
        """
        self.start()
        try:
            while True:
                # Sleep in small chunks so KeyboardInterrupt fires fast.
                threading.Event().wait(0.5)
        except KeyboardInterrupt:
            print("\n[clew] shutting down…")
        finally:
            self.stop()

    # ── Properties ───────────────────────────────────────────────
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def api_token(self) -> str:
        return self._api.auth_token

    @property
    def ctx(self) -> ServerContext:
        return self._api.ctx


# ── CLI ────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="clew",
        description="Clew v2.2.0 — local-first AI IDE (web UI).",
    )
    p.add_argument(
        "--host", default=os.environ.get("CLEW_HOST", DEFAULT_HOST),
        help=f"Bind host (default: {DEFAULT_HOST}). Set CLEAN_HOST env to override.",
    )
    p.add_argument(
        "--port", "-p", type=int,
        default=int(os.environ.get("CLEW_PORT", str(DEFAULT_PORT))),
        help=f"Bind port (default: {DEFAULT_PORT}). Set CLEAN_PORT env to override.",
    )
    p.add_argument(
        "--project", "-w", default=os.getcwd(),
        help="Workspace / project root to open (default: current directory).",
    )
    p.add_argument(
        "--no-browser", action="store_true",
        help="Don't auto-open the default browser on start.",
    )
    p.add_argument(
        "--version", action="version", version=f"clew {__version__}",
    )
    return p.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    setup_logging()
    args = _parse_args(argv)
    server = ClewWebServer(
        host=args.host,
        port=args.port,
        project=args.project,
        open_browser=not args.no_browser,
    )
    print(f"\n  Clew v{__version__} — Web UI")
    print(f"  ─────────────────────────────────────────────")
    print(f"  Local:   {server.base_url}/")
    print(f"  API:     {server.base_url}/api/status")
    print(f"  Project: {args.project}")
    print(f"  Token:   {server.api_token[:16]}…")
    print(f"\n  Press Ctrl+C to stop.\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

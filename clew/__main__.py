"""
Clew v2.2.0 — module entry point.

Usage:
    python -m clew                      # launch the Web UI on http://127.0.0.1:18732
    python -m clew --port 8000          # custom port
    python -m clew --host 0.0.0.0       # share on LAN
    python -m clew --project /path      # open a specific project
    python -m clew --no-browser         # don't auto-open the browser
    python -m clew cli run "..."        # headless CLI
    python -m clew cli heavy-code "..." # headless Heavy Code section
    python -m clew cli status           # show config + provider + policy

v2.2.0: the legacy PySide6 / QWebEngineView desktop GUI has been
removed. The GUI is now served by ``clew.web_server`` as a plain
HTTP server (static frontend + JSON REST API + SSE). Point any
browser at the printed URL.

The headless CLI lives in ``clew.cli`` (unchanged) — the ``cli``
subcommand routes there without importing the web server.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.2.1-fix: route ``python -m clew cli ...`` to the headless CLI
# without requiring the user to type ``python -m clew.cli``.
if len(sys.argv) >= 2 and sys.argv[1] == "cli":
    # Strip "cli" from argv so argparse in clew.cli sees the real subcommand.
    sys.argv.pop(1)
    from clew.cli import main
    if __name__ == "__main__":
        sys.exit(main())
else:
    # v2.2.0: launch the Web UI server (replaces the PySide6 GUI).
    from clew.web_server import main

    if __name__ == "__main__":
        main()

"""Entry point: `python -m clew_tui`.

Thin CLI shim — parses a few optional overrides and launches the Textual app.
The full-screen TUI is deliberately separate from clew/cli.py (the traditional
one-shot argparse CLI).
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clew-tui",
        description="Full-screen terminal UI for the Clew agent.",
    )
    parser.add_argument("--workspace", "-w", default=os.getcwd(),
                        help="Workspace root (default: current directory).")
    parser.add_argument("--provider", "-p", default=None,
                        help="Provider id override (default: saved config).")
    parser.add_argument("--model", "-m", default=None,
                        help="Model override (default: saved config).")
    parser.add_argument("--api-base", default=None, help="API base URL override.")
    parser.add_argument("--section", default="general",
                        choices=["general", "heavy_code", "office"],
                        help="Runtime section (default: general).")
    parser.add_argument("--max-iterations", type=int, default=8,
                        help="Max agent iterations per turn (default: 8).")
    parser.add_argument("--planning", action="store_true", default=False,
                        help="Enable planning mode (agent creates a plan before executing).")
    args = parser.parse_args(argv)

    try:
        from clew_tui.app import ClewTUIApp
        from clew_tui.bridge import ClewBridge, ProviderChoice
    except ModuleNotFoundError as e:
        if "textual" in str(e):
            sys.stderr.write(
                "clew_tui requires the 'textual' package.\n"
                "Install it with:  pip install textual\n"
            )
            return 2
        raise

    bridge = ClewBridge(
        workspace=args.workspace,
        provider=ProviderChoice(
            provider_id=args.provider,
            model=args.model,
            api_base=args.api_base,
        ),
        section=args.section,
        max_iterations=args.max_iterations,
        enable_planning=args.planning,
    )
    ClewTUIApp(bridge=bridge).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

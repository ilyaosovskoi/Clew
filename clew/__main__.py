"""
Clew v1.2.1 — module entry point.

Usage:
    python -m clew                  # launch the GUI with last project
    python -m clew --project PATH   # launch the GUI with a specific project
    python -m clew.cli run "..."    # headless CLI (v1.2.1-fix review §4.7)
    python -m clew.cli heavy-code "..."   # headless Heavy Code section
    python -m clew.cli status       # show config + provider + policy

v1.2.1-fix (review §4.7): the headless CLI is in ``clew.cli`` (a
separate module so importing it doesn't pull in PySide6). The GUI
launch path stays here for backward compatibility.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.2.1-fix: route ``python -m clew cli ...`` to the headless CLI
# without requiring the user to type ``python -m clew.cli``. The first
# positional arg is checked BEFORE importing PySide6 (which is slow and
# may not be installed in headless environments).
if len(sys.argv) >= 2 and sys.argv[1] == "cli":
    # Strip "cli" from argv so argparse in clew.cli sees the real subcommand.
    sys.argv.pop(1)
    from clew.cli import main
    if __name__ == "__main__":
        sys.exit(main())
else:
    from clew.app import main

    if __name__ == "__main__":
        main()

"""
Clew v1.0.1 — module entry point.

Usage:
    python -m clew                  # launch with last project
    python -m clew --project PATH   # launch with a specific project
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clew.app import main

if __name__ == "__main__":
    main()

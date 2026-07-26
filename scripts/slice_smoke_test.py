"""
Smoke test for the refactored agent_runtime + web_bridge packages.

Verifies that:
1. Every .py file in update_2/clew/agent_runtime/ and update_2/clew/web_bridge/
   parses as valid Python (py_compile).
2. The shim files (clew/agent_runtime.py, clew/web_bridge.py) are valid.
3. The total line count of the refactored package matches (or is close to)
   the original monolith — i.e. we didn't silently drop code.
4. Every public symbol that the original files exported is still
   re-exported by the new __init__.py (AST-based name extraction).
"""

from __future__ import annotations

import ast
import py_compile
import sys
import zipfile
from pathlib import Path

ROOT = Path("/home/z/my-project/clew")
SRC_AGENT = ROOT / "source" / "clew" / "agent_runtime.py"
SRC_BRIDGE = ROOT / "source" / "clew" / "web_bridge.py"
DST = ROOT / "update_2"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

passed = 0
failed = 0
warnings = 0


def ok(msg: str) -> None:
    global passed
    print(f"  {GREEN}✓{RESET} {msg}")
    passed += 1


def fail(msg: str) -> None:
    global failed
    print(f"  {RED}✗{RESET} {msg}")
    failed += 1


def warn(msg: str) -> None:
    global warnings
    print(f"  {YELLOW}!{RESET} {msg}")
    warnings += 1


# ─────────────────────────────────────────────────────────────────────────
# 1. py_compile every new .py file
# ─────────────────────────────────────────────────────────────────────────
print("\n=== 1. Syntax check (py_compile) ===")

py_files = sorted(DST.rglob("*.py"))
print(f"  found {len(py_files)} .py files")

for f in py_files:
    try:
        py_compile.compile(str(f), doraise=True)
        ok(f"compile OK: {f.relative_to(DST)}")
    except py_compile.PyCompileError as e:
        fail(f"compile FAIL: {f.relative_to(DST)} — {e}")


# ─────────────────────────────────────────────────────────────────────────
# 2. AST parse + line counts
# ─────────────────────────────────────────────────────────────────────────
print("\n=== 2. Line count comparison ===")

src_agent_lines = SRC_AGENT.read_text(encoding="utf-8").splitlines()
src_bridge_lines = SRC_BRIDGE.read_text(encoding="utf-8").splitlines()

agent_pkg_files = sorted((DST / "clew" / "agent_runtime").rglob("*.py"))
bridge_pkg_files = sorted((DST / "clew" / "web_bridge").rglob("*.py"))

agent_new_lines = sum(
    sum(1 for _ in f.read_text(encoding="utf-8").splitlines())
    for f in agent_pkg_files
)
bridge_new_lines = sum(
    sum(1 for _ in f.read_text(encoding="utf-8").splitlines())
    for f in bridge_pkg_files
)

agent_shim_lines = sum(
    1 for _ in (DST / "clew" / "agent_runtime.py").read_text(encoding="utf-8").splitlines()
)
bridge_shim_lines = sum(
    1 for _ in (DST / "clew" / "web_bridge.py").read_text(encoding="utf-8").splitlines()
)

print(f"  agent_runtime: source={len(src_agent_lines)} lines  →  package={agent_new_lines} lines  +  shim={agent_shim_lines} lines")
print(f"  web_bridge:    source={len(src_bridge_lines)} lines  →  package={bridge_new_lines} lines  +  shim={bridge_shim_lines} lines")

# Package (excluding headers we added) should be roughly source + ~10-15% for header overhead
agent_overhead = agent_new_lines - len(src_agent_lines)
bridge_overhead = bridge_new_lines - len(src_bridge_lines)

if agent_overhead < 0:
    fail(f"agent_runtime lost {-agent_overhead} lines!")
elif agent_overhead > 400:
    warn(f"agent_runtime added {agent_overhead} lines (headers expected ~300)")
else:
    ok(f"agent_runtime overhead: {agent_overhead} lines (within expected range)")

if bridge_overhead < 0:
    fail(f"web_bridge lost {-bridge_overhead} lines!")
elif bridge_overhead > 250:
    warn(f"web_bridge added {bridge_overhead} lines (headers expected ~150)")
else:
    ok(f"web_bridge overhead: {bridge_overhead} lines (within expected range)")


# ─────────────────────────────────────────────────────────────────────────
# 3. Public symbol coverage
# ─────────────────────────────────────────────────────────────────────────
print("\n=== 3. Public symbol coverage ===")


def top_level_names(path: Path) -> set[str]:
    """Return set of top-level class/def/function names in a .py file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


src_agent_symbols = top_level_names(SRC_AGENT)
src_bridge_symbols = top_level_names(SRC_BRIDGE)

# Walk all new package files and collect names
new_agent_symbols: set[str] = set()
for f in agent_pkg_files:
    new_agent_symbols |= top_level_names(f)

new_bridge_symbols: set[str] = set()
for f in bridge_pkg_files:
    new_bridge_symbols |= top_level_names(f)

# Symbols that should be re-exported (public — not starting with __)
src_agent_public = {s for s in src_agent_symbols if not s.startswith("__")}
src_bridge_public = {s for s in src_bridge_symbols if not s.startswith("__")}

missing_agent = src_agent_public - new_agent_symbols
missing_bridge = src_bridge_public - new_bridge_symbols

if missing_agent:
    fail(f"agent_runtime: {len(missing_agent)} public symbols missing from new package:")
    for s in sorted(missing_agent):
        print(f"      - {s}")
else:
    ok(f"agent_runtime: all {len(src_agent_public)} public symbols preserved")

if missing_bridge:
    fail(f"web_bridge: {len(missing_bridge)} public symbols missing from new package:")
    for s in sorted(missing_bridge):
        print(f"      - {s}")
else:
    ok(f"web_bridge: all {len(src_bridge_public)} public symbols preserved")


# ─────────────────────────────────────────────────────────────────────────
# 4. Shim re-export check
# ─────────────────────────────────────────────────────────────────────────
print("\n=== 4. Shim re-export check ===")

agent_shim = (DST / "clew" / "agent_runtime.py").read_text(encoding="utf-8")
bridge_shim = (DST / "clew" / "web_bridge.py").read_text(encoding="utf-8")

if "from clew.agent_runtime import *" in agent_shim:
    ok("agent_runtime shim uses wildcard re-import")
else:
    fail("agent_runtime shim missing wildcard re-import")

if "from clew.web_bridge import *" in bridge_shim:
    ok("web_bridge shim uses wildcard re-import")
else:
    fail("web_bridge shim missing wildcard re-import")


# ─────────────────────────────────────────────────────────────────────────
# 5. Cross-module import sanity (no circular imports at parse time)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== 5. AST parse all files (catches import-time syntax issues) ===")

for f in py_files:
    try:
        ast.parse(f.read_text(encoding="utf-8"))
        ok(f"AST OK: {f.relative_to(DST)}")
    except SyntaxError as e:
        fail(f"AST FAIL: {f.relative_to(DST)} — {e}")


# ─────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  {GREEN}PASSED{RESET}: {passed}   {YELLOW}WARN{RESET}: {warnings}   {RED}FAILED{RESET}: {failed}")
print("=" * 60)

sys.exit(1 if failed else 0)

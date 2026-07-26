"""
Deep verification: ensure every class in the original monolith still
has all its methods in the new package (no silent method drops).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path("/home/z/my-project/clew")
SRC_AGENT = ROOT / "source" / "clew" / "agent_runtime.py"
SRC_BRIDGE = ROOT / "source" / "clew" / "web_bridge.py"
DST = ROOT / "update_2"

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(msg: str) -> None:
    global passed
    print(f"  {GREEN}✓{RESET} {msg}")
    passed += 1


def fail(msg: str) -> None:
    global failed
    print(f"  {RED}✗{RESET} {msg}")
    failed += 1


def extract_classes(path: Path) -> dict[str, set[str]]:
    """Return {class_name: {method1, method2, ...}} for top-level + nested classes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes: dict[str, set[str]] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                full_name = f"{prefix}{child.name}" if prefix else child.name
                methods = {
                    n.name
                    for n in child.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                classes[full_name] = methods
                walk(child, prefix=f"{full_name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # methods are handled in the ClassDef branch above
                pass
            else:
                walk(child, prefix)

    walk(tree)
    return classes


def collect_package_classes(pkg_dir: Path) -> dict[str, set[str]]:
    """Walk a package directory and collect class→methods from every .py file."""
    result: dict[str, set[str]] = {}
    for f in sorted(pkg_dir.rglob("*.py")):
        for cls, methods in extract_classes(f).items():
            if cls in result:
                result[cls] |= methods
            else:
                result[cls] = methods
    return result


# ── agent_runtime ───────────────────────────────────────────────────────
print("\n=== agent_runtime: per-class method coverage ===")
src_classes = extract_classes(SRC_AGENT)
new_classes = collect_package_classes(DST / "clew" / "agent_runtime")

print(f"  source classes: {len(src_classes)}  →  new package classes: {len(new_classes)}")

missing_classes = set(src_classes) - set(new_classes)
if missing_classes:
    for c in sorted(missing_classes):
        fail(f"class missing: {c}")
else:
    ok(f"all {len(src_classes)} classes preserved")

for cls_name, src_methods in sorted(src_classes.items()):
    new_methods = new_classes.get(cls_name, set())
    missing = src_methods - new_methods
    if missing:
        fail(f"class '{cls_name}': {len(missing)} methods missing:")
        for m in sorted(missing):
            print(f"      - {m}")
    else:
        ok(f"class '{cls_name}': all {len(src_methods)} methods present")


# ── web_bridge ──────────────────────────────────────────────────────────
print("\n=== web_bridge: per-class method coverage ===")
src_classes = extract_classes(SRC_BRIDGE)
new_classes = collect_package_classes(DST / "clew" / "web_bridge")

print(f"  source classes: {len(src_classes)}  →  new package classes: {len(new_classes)}")

missing_classes = set(src_classes) - set(new_classes)
if missing_classes:
    for c in sorted(missing_classes):
        fail(f"class missing: {c}")
else:
    ok(f"all {len(src_classes)} classes preserved")

for cls_name, src_methods in sorted(src_classes.items()):
    new_methods = new_classes.get(cls_name, set())
    missing = src_methods - new_methods
    if missing:
        fail(f"class '{cls_name}': {len(missing)} methods missing:")
        for m in sorted(missing):
            print(f"      - {m}")
    else:
        ok(f"class '{cls_name}': all {len(src_methods)} methods present")


print("\n" + "=" * 60)
print(f"  {GREEN}PASSED{RESET}: {passed}   {RED}FAILED{RESET}: {failed}")
print("=" * 60)
sys.exit(1 if failed else 0)

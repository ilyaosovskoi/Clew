#!/usr/bin/env python3
"""clew/web/smoke_test.py — GUI smoke tests (v2.0.0).

Verifies that the web GUI files (index.html, style.css, apple-design.css,
app.js) are internally consistent and that every bridge method called
from app.js exists on `clew.web_bridge.WebBridge`. Also checks that
v2.0.0 backend capabilities (Guardian level, collaboration modes,
request queue, persistence backend, context fragments, tool catalog)
are exposed both in the bridge and in the UI.

Run:
    python -m clew.web.smoke_test
or:
    pytest clew/web/smoke_test.py -v

This smoke test does NOT start a Qt application and does NOT load the
HTML in a real browser. It parses the source files statically. It is
designed to catch:
  - Renaming a bridge Slot without updating the JS callers
  - Removing an HTML element ID that app.js references
  - Forgetting to surface a v2.0.0 backend feature in the UI
  - Stale version strings (e.g. v1.3.0 left in the title bar)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Set

# Make sure the project root is on sys.path so `from clew.web_bridge import ...`
# works when running the file directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Tiny test framework (no pytest dependency) ────────────────────────

class _SmokeResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = False
        self.skipped = False
        self.detail: str = ""

    def __repr__(self) -> str:
        if self.skipped:
            return f"SKIP  {self.name}  ({self.detail})"
        return f"{'OK  ' if self.ok else 'FAIL'}  {self.name}  {self.detail}"


_RESULTS: list[_SmokeResult] = []


def test(name: str):
    """Decorator: register a smoke-test function."""

    def deco(fn):
        r = _SmokeResult(name)
        try:
            fn(r)
            r.ok = True
        except AssertionError as e:
            r.ok = False
            r.detail = f"assertion: {e}"
        except Exception as e:
            r.ok = False
            r.detail = f"{type(e).__name__}: {e}"
        _RESULTS.append(r)
        return fn

    return deco


def check(cond, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "condition false")


def skip(r: _SmokeResult, reason: str) -> None:
    r.skipped = True
    r.detail = reason


# ── File paths ────────────────────────────────────────────────────────

WEB_DIR = Path(__file__).resolve().parent
INDEX_HTML = WEB_DIR / "index.html"
STYLE_CSS = WEB_DIR / "style.css"
APPLE_CSS = WEB_DIR / "apple-design.css"
APP_JS = WEB_DIR / "app.js"
WEB_BRIDGE_PY = WEB_DIR.parent / "web_bridge.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── 1. File existence & sizes ─────────────────────────────────────────

@test("All four GUI files exist and are non-empty")
def _t(r):
    for f in (INDEX_HTML, STYLE_CSS, APPLE_CSS, APP_JS):
        check(f.exists(), f"missing {f.name}")
        check(f.stat().st_size > 0, f"{f.name} is empty")


# ── 2. Version strings ────────────────────────────────────────────────

@test("index.html title and brand show v2.0.0")
def _t(r):
    html = _read(INDEX_HTML)
    check("v2.0.0" in html,
          "index.html must reference v2.0.0 somewhere in title/brand")
    # Old v1.3.0 / v1.0.x must NOT appear in user-visible title/brand.
    check("v1.3.0" not in html, "stale v1.3.0 still in index.html")


@test("app.js header comment is v2.0.0")
def _t(r):
    js = _read(APP_JS)
    head = js[:300]
    check("v2.0.0" in head, "app.js header must say v2.0.0")
    check("v1.0.2" not in head, "app.js still has stale v1.0.2 header")


@test("style.css and apple-design.css headers are v2.0.0")
def _t(r):
    css1 = _read(STYLE_CSS)[:300]
    css2 = _read(APPLE_CSS)[:300]
    check("v2.0.0" in css1, "style.css header must say v2.0.0")
    check("v2.0.0" in css2, "apple-design.css header must say v2.0.0")


# ── 3. Bug #1: respond_guardian_verdict must NOT appear ───────────────

@test("app.js uses respond_guardian_review (not the buggy respond_guardian_verdict)")
def _t(r):
    js = _read(APP_JS)
    check("respond_guardian_review" in js,
          "app.js must call respond_guardian_review")
    check("respond_guardian_verdict" not in js,
          "app.js still references the buggy respond_guardian_verdict")


# ── 4. Bug #2: orphaned heavycodeOverlay/officeOverlay references ────

@test("app.js does not reference orphaned heavycodeOverlay / officeOverlay elements")
def _t(r):
    js = _read(APP_JS)
    # Strip /* ... */ block comments and // ... line comments so we only
    # check actual code references. The bug was an actual
    # `getElementById('heavycodeOverlay')` call; an explanatory comment
    # mentioning the name is fine.
    js_no_comments = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    js_no_comments = re.sub(r"//[^\n]*", "", js_no_comments)
    check("heavycodeOverlay" not in js_no_comments,
          "app.js still references orphaned heavycodeOverlay in code")
    check("officeOverlay" not in js_no_comments,
          "app.js still references orphaned officeOverlay in code")


# ── 5. v2.0.0 GUI features are present ───────────────────────────────

@test("Guardian safety review section present in Settings → Agent")
def _t(r):
    js = _read(APP_JS)
    check("Guardian safety review" in js,
          "renderAgentTab must include a 'Guardian safety review' section")
    check("guardianLevel" in js,
          "renderAgentTab must define guardianLevel radio group")
    check("set_guardian_level" in js,
          "app.js must call set_guardian_level on the bridge")


@test("Collaboration mode UI present in Heavy Code pane")
def _t(r):
    html = _read(INDEX_HTML)
    js = _read(APP_JS)
    check("hcCollabMode" in html,
          "index.html must include the #hcCollabMode dropdown")
    check("hcRunCollabBtn" in html,
          "index.html must include the #hcRunCollabBtn button")
    check("hcRunCollabBtn" in js,
          "app.js must wire #hcRunCollabBtn click handler")
    check("run_collaboration" in js,
          "app.js must call run_collaboration on the bridge")


@test("Request queue panel present in Usage modal")
def _t(r):
    html = _read(INDEX_HTML)
    js = _read(APP_JS)
    check("usageQueuePanel" in html,
          "index.html must include #usageQueuePanel")
    check("get_queue_stats" in js,
          "app.js must call get_queue_stats on the bridge")


@test("Persistence backend selector present in Usage modal")
def _t(r):
    html = _read(INDEX_HTML)
    js = _read(APP_JS)
    check("usagePersistencePanel" in html,
          "index.html must include #usagePersistencePanel")
    check("set_persistence_backend" in js,
          "app.js must call set_persistence_backend on the bridge")


# ── 6. Bridge Slot coverage — every callBridge target must exist ─────

@test("WebBridge defines every callBridge target invoked from app.js")
def _t(r):
    if not WEB_BRIDGE_PY.exists():
        skip(r, f"web_bridge.py not found at {WEB_BRIDGE_PY}")
        return
    bridge_src = _read(WEB_BRIDGE_PY)
    js = _read(APP_JS)
    # Find all callBridge('method_name', ...) invocations.
    # Be permissive about the second argument.
    matches = re.findall(r"callBridge\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", js)
    check(matches, "no callBridge('method') invocations found in app.js")
    missing: Set[str] = set()
    for method in set(matches):
        # Look for either `def method(` (Python @Slot) or `method = Signal`
        # Both are valid ways to expose a method to JS.
        pattern = rf"\bdef\s+{re.escape(method)}\s*\("
        alt_pattern = rf"\b{re.escape(method)}\s*=\s*Signal\b"
        if not (re.search(pattern, bridge_src) or re.search(alt_pattern, bridge_src)):
            # Some are intentionally exposed via direct window.bridge.X calls
            # (no @Slot); accept that too.
            missing.add(method)
    check(not missing, f"bridge methods called from app.js but missing on WebBridge: {sorted(missing)}")


@test("WebBridge defines the v2.0.0 @Slot methods")
def _t(r):
    if not WEB_BRIDGE_PY.exists():
        skip(r, f"web_bridge.py not found at {WEB_BRIDGE_PY}")
        return
    src = _read(WEB_BRIDGE_PY)
    for slot in ("get_guardian_level", "set_guardian_level",
                 "list_collaboration_modes", "run_collaboration",
                 "get_queue_stats", "get_persistence_backend",
                 "set_persistence_backend", "list_sqlite_sessions",
                 "get_compaction_stats", "get_tool_catalog_state"):
        pattern = rf"\bdef\s+{re.escape(slot)}\s*\("
        check(re.search(pattern, src),
              f"WebBridge must define @Slot {slot}")


@test("WebBridge responds to guardian_review_requested signal in JS")
def _t(r):
    js = _read(APP_JS)
    # The Guardian signal must be wired to a JS handler.
    check("guardian_review_requested" in js,
          "app.js must subscribe to guardian_review_requested")


# ── 7. HTML ↔ JS ID consistency ───────────────────────────────────────

@test("Every #hcCollabMode / #hcRunCollabBtn / #usageQueuePanel / #usagePersistencePanel ID has a JS reference")
def _t(r):
    html = _read(INDEX_HTML)
    js = _read(APP_JS)
    for elem_id in ("hcCollabMode", "hcRunCollabBtn",
                    "usageQueuePanel", "usagePersistencePanel"):
        check(f'id="{elem_id}"' in html, f"index.html missing #{elem_id}")
        check(elem_id in js, f"app.js does not reference #{elem_id}")


# ── 8. Guardian level values match between JS and bridge ─────────────

@test("app.js Guardian level values match the backend enum")
def _t(r):
    js = _read(APP_JS)
    for level in ("off", "dangerous_only", "all"):
        check(f"'{level}'" in js or f'"{level}"' in js,
              f"app.js must reference Guardian level '{level}'")


# ── 9. Collaboration mode values match between JS and bridge ─────────

@test("app.js collaboration mode values match the backend enum")
def _t(r):
    html = _read(INDEX_HTML)
    for mode in ("single", "reviewer", "codegen", "pair", "observer"):
        check(f'value="{mode}"' in html,
              f"index.html must include collaboration mode option '{mode}'")


# ── 10. CSS sanity ────────────────────────────────────────────────────

@test("style.css contains the expected theme tokens")
def _t(r):
    css = _read(STYLE_CSS)
    for token in (":root", "--bg-primary", "--text-primary",
                  "--accent", "--border"):
        check(token in css, f"style.css missing token: {token}")


@test("apple-design.css has spring / typography tokens")
def _t(r):
    css = _read(APPLE_CSS)
    check("--spring-damping" in css or "spring" in css.lower(),
          "apple-design.css must define spring-timing tokens")


# ── 11. Section switcher cleanup (bug #2 regression) ─────────────────

@test("app.js section switcher IIFE does NOT reference csBackdrop backdrop click handler")
def _t(r):
    js = _read(APP_JS)
    # The buggy IIFE had `backdrop.addEventListener('click', closeOverlays)`.
    # The replacement just toggles .active on section buttons.
    check("data-cs-back" not in js,
          "app.js still references data-cs-back attribute (orphaned)")


# ── 12. auto_updater version ─────────────────────────────────────────

@test("auto_updater.py declares __version__ = '2.0.0'")
def _t(r):
    au = WEB_DIR.parent / "auto_updater.py"
    if not au.exists():
        skip(r, "auto_updater.py not found")
        return
    src = _read(au)
    check('__version__ = "2.0.0"' in src or "__version__ = '2.0.0'" in src,
          "auto_updater must declare __version__ = '2.0.0'")


# ── Runner ────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 70)
    print("clew WEB GUI v2.0.0 — SMOKE TESTS")
    print("=" * 70)

    passed = sum(1 for r in _RESULTS if r.ok and not r.skipped)
    failed = sum(1 for r in _RESULTS if not r.ok and not r.skipped)
    skipped = sum(1 for r in _RESULTS if r.skipped)
    total = len(_RESULTS)

    for r in _RESULTS:
        print(f"  {r!r}")

    print()
    print("=" * 70)
    print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}  |  Skipped: {skipped}")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

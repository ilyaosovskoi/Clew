# CHANGES — update_14 (v2.2.2)

**Date:** 2026-08-05
**Scope:** Web UI layout overflow fix — the composer was being clipped off-screen on every viewport.

## Problem

Users reported the Web UI window was "too large and goes beyond the browser, getting cut off." The bottom composer (text input + Send button) was clipped off-screen on every viewport size — not just small ones.

## Root cause

`.app` was a CSS Grid with `display:grid; height:100vh` but no `grid-template-rows`. Grid items default to `min-height: auto`, so they grew to their content's intrinsic size instead of being constrained to the viewport.

The sidebar's content (22+ nav buttons after the v2.2.1 Tools sidebar expansion) was 1247px tall on a 800px viewport. Without `min-height: 0`, the sidebar grew to 1247px and pushed the composer down to y=1102, where it got clipped by `body { overflow: hidden }`.

## Fix

Three-line CSS fix in `clew/web/style.css`:
1. `.app` got `grid-template-rows: minmax(0, 1fr)` — forces the row to exactly the container height.
2. `.app` switched `width: 100vw` → `width: 100%` (100vw includes scrollbar width).
3. `.sidebar` and `.stage` each got `min-height: 0; height: 100%` — overrides the default `min-height: auto` so they can shrink below content size and scroll internally.

## Other fixes shipped in v2.2.2

While diagnosing the layout bug, two missing-asset 404s were discovered and fixed:

- **`clew/web/bridge_shim.js`** (new, ~310 lines) — `index.html` loads `<script src="bridge_shim.js">` before `app.js`, but the file had been missing since the Qt removal in v2.2.0. Without it, `window.bridge` was undefined and `app.js` crashed at line 3536 (`window.bridge.guardian_review_requested.connect(...)`). The new shim:
  - Provides stub Qt-style signal objects (with `.connect()` / `.disconnect()`) for all 23 signal names `app.js` references.
  - Provides a Proxy-based method dispatcher that maps snake_case bridge methods (`get_status`, `list_chats`, `set_provider`, `send_agent_message`, etc.) to HTTP routes (`/api/status`, `/api/chat/list`, ...).
  - On page load, attempts `GET /api/status`. If reachable, marks `window.__clewBridgeConnected = true`, calls `window.__clewReady(status)`, and dispatches the `clew:bridge_ready` event.
  - Skips the fetch on `file://` URLs and falls back to "demo mode" (UI renders without backend data).
- **`clew/web/apple-design.css`** (new stub) — `index.html` references this stylesheet but it never existed. Stub silences the 404.
- **`clew/web/app.js`** — guarded the `window.bridge.guardian_review_requested.connect(...)` call with `if (window.bridge && window.bridge.guardian_review_requested)` so a future missing-shim scenario doesn't crash the page.

## Verification

Playwright probe across 5 viewport sizes (1024×768, 1280×800, 1440×900, 1536×864, 1920×1080):

```
BEFORE (v2.2.1):
laptop-1280x800   BODY-OVERFLOW-Y(bsh=1247) ELEM-OVERFLOW-X=1 ELEM-OVERFLOW-Y=4 CONSOLE-ERRORS=3

AFTER (v2.2.2):
laptop-1280x800   ELEM-OVERFLOW-X=1   (false positive — tools-panel hidden via transform)
```

`body.scrollHeight` dropped from 1247 → 800 (matches viewport). The remaining `ELEM-OVERFLOW-X` is the Tools panel drawer, which is intentionally positioned off-screen via `transform: translateX(100%)` when closed — `body.scrollWidth` is 1280 (matches viewport), so there's no actual horizontal overflow.

VLM visual confirmation:
> "Yes, the entire UI is visible within the viewport. The bottom composer (the text input area with the 'Send' button) is fully visible. There are no cut-off elements."

## Files in this update

### Modified (2)
- `clew/web/style.css` — 3-line grid layout fix + `overflow-x: hidden` on html/body
- `clew/web/app.js` — guarded `guardian_review_requested.connect(...)` against missing `window.bridge`

### New (2)
- `clew/web/bridge_shim.js` — `window.bridge` stub + HTTP method dispatcher (~310 lines)
- `clew/web/apple-design.css` — empty stub silencing 404

### Documentation (2)
- `CLAUDE.md` — v2.2.2 section added (root cause, fix, verification, backward compat)
- `docs/CHANGES_update_14.md` — this file

## Install

```bash
# Unzip into your Clew source tree (overwrites existing files)
cd /path/to/clew_v2.0.1
unzip update_14.zip

# Run the web UI
clew   # opens http://127.0.0.1:18732/
```

No `pip install -e .` needed — no Python code changed in this update.

## Backward compatibility

- All existing endpoints unchanged.
- The 130 tests in `clew/tests/test_v221_web_ui_expansion.py` still pass.
- CSS changes are additive — existing elements keep working.
- `bridge_shim.js` is a strict superset of "no bridge": if `window.bridge` was previously set by some other mechanism, the shim early-returns.

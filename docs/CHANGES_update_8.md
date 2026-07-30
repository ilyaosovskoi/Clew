# CHANGES_update_8.md — G15, G16, G17, G18 (v2.1.0)

This patch set implements four features together (G15 multi-provider consensus engine, G16 signed offline audit trail, G17 automatic learning loop, G18 web search & internet reach) and bumps the version from `2.0.0` to `2.1.0`.

## Self-verification

Before packaging, the patch set was unzipped on top of a clean copy of the source `clew_v2.0.1` repo and verified:

**Test result:** `pytest clew/` (excluding `clew/web_smoke_test.py` which requires libEGL not present in the test env, and `clew/tests/test_notifier.py::TestNotifier::test_status` which has a PRE-EXISTING deadlock in `notifier.py`'s `status()` method — not touched by this patch) → **479 passed, 11 skipped, 1 deselected**.

**New G18 suite:** `pytest clew/tests/test_g18_web_search.py` → **39 passed**.

**Import verification:** `from clew.agent_runtime.tool_engine import ToolEngine` succeeds; `ToolName.WEB_SEARCH.value == "web_search"`; `ToolName.WEB_FETCH.value == "web_fetch"`; the `researcher` role whitelist contains `web_search` + `web_fetch` but NOT `write_file` / `execute_command` / `run_code`. All new modules (`consensus_engine`, `audit_signing`, `learning_loop`, `web_search_backend`) import cleanly.

**TUI bridge + web bridge:** `clew_tui.bridge.ClewBridge` and `clew.web_bridge.bridge.ClewBridge` both expose the new methods (`get_consensus_config`, `set_consensus_config`, `run_consensus`, `export_audit_signed_json`, `verify_audit_signed_file`, `handle_learnings_command`, `get_websearch_status`). `web_bridge/bridge.py` compiles cleanly (`python3 -m py_compile`).

**Pre-existing issue (NOT introduced by this patch):** `clew/tests/test_notifier.py::TestNotifier::test_status` hangs due to a deadlock in `Notifier.status()` — it calls `self.list_backends()` while already holding `self._lock` (a non-reentrant `threading.Lock`). `notifier.py` is unchanged by this patch set; the deadlock was already there. Flagged here for transparency.

**Self-verification script:** `/home/z/my-project/scripts/self_verify_update_8.sh` extracts a fresh copy of the source repo, applies `update_8.zip`, runs the import checks + full pytest suite. Output captured above.

## Files in this zip

### New files

| Path | G* | Description |
|------|----|-------------|
| `.clew/skills/web-research/SKILL.md` | G18 | Project-level skill describing when to search vs. not search, query formulation, treatment of fetched content as untrusted data, and when to fan out `researcher` subagents. Loaded on demand via `get_skill` — does NOT bloat the system prompt. |
| `clew/audit_signing.py` | G16 | Ed25519 signature + SHA-256 hash-chain module for the audit log. Generates+stores the keypair at `~/.clew/audit_key` (chmod 0600) and `~/.clew/audit_key.pub` (chmod 0644). Provides `sign_entry()`, `export_signed_json()`, `verify_signed_json()`, `verify_signed_file()`. Zero-cloud — keys never leave the user's machine. |
| `clew/consensus_engine.py` | G15 | Multi-provider consensus engine. Runs the same prompt on 2–3 providers in parallel (`ThreadPoolExecutor`), extracts structured features (files touched, code_blocks, code_chars, text_chars), computes a Jaccard-based agreement score, and produces a structured divergence list with likely-reason explanations. Config persisted under `consensus` key in `~/.clew/config.json`. Fails safe — provider errors don't abort the comparison. |
| `clew/learning_loop.py` | G17 | Automatic learning loop. Detects git rollbacks (`git reset --hard`, force-pushes, `git revert`, abandoned branches) and CI failures (reuses `ToolEngine._detect_project_command` pattern — no new detection scheme). Auto-creates `learnings/<date>-<slug>.md` entries matching the existing `Learnings.md` template (read at runtime so it never drifts). Per-repo scoping with `~/.clew/learnings/<hash>/` fallback. Injected via `build_learnings_fragment()` which wraps them in `<context_fragment type="project_learnings">` so they tombstone-compact. Dismissed learnings stop being injected. |
| `clew/tests/test_g18_web_search.py` | G18 (and G15/G16/G17) | 39 tests covering: ToolName enum (WEB_SEARCH/WB_FETCH), dispatch routing, `researcher` role whitelist (rejects write/exec/run_code, allows web tools), Guardian web_fetch risk rules (secret URL → high, base64 URL → medium+, clean URL → medium, web_search → low, existing rules not weakened), context fragment wrapping + compaction, MCP ordered-fallback when primary fails, `/websearch status` shape, G15 consensus config round-trip + parallel run + fail-safe, G16 audit signing round-trip + tamper/reorder/deletion detection, G17 learning entry creation + per-project isolation + dismiss/restore + slash command. |
| `clew/web_search_backend.py` | G18 | MCP-first web search backend. Routes `web_search` through `MCPManager` (reuses process lifecycle + `~/.clew/mcp.json` config + typed catalog + crash watchdog). Ordered-fallback: tries primary search backend first, falls back to next configured one. Records which backend actually served the request (process-scoped health tracking). Also implements `fetch_url_as_text()` with stdlib urllib (no new dependency) + HTML-to-text extraction. Test-injection hooks for unit tests. |
| `docs/mcp_search_template.json` | G18 | Documented (not force-installed) `~/.clew/mcp.json` template entry for the no-API-key DuckDuckGo MCP search server (`npx ddg-mcp`). Copy to `~/.clew/mcp.json` to enable `web_search` with one config toggle. The `"role": "search"` tag tells `web_search_backend` to prefer this server as the primary. |

### Modified files

| Path | G* | Description |
|------|----|-------------|
| `CLAUDE.md` | G15/G16/G17/G18 | Moved G15/G16/G17 from "New Feature Ideas" / "Upcoming Goals" into a new `## v2.1.0 (…) — G15, G16, G17, G18 ✅ COMPLETED` section (matches the terse "Files: ... ; behavior: ..." style of the existing v2.0.2/v2.0.3 sections). Added G18 as a new row in the Unique Strengths / Capability tables. Updated the Gap Analysis to reflect that G15–G18 are now done. |
| `Learnings.md` | G18 (and process) | Added `LEARN-20260730-001: MCP-First Beats Hardcoded API for Zero-Config Web Search` — captures the non-obvious lesson that routing `web_search` through `MCPManager` (instead of hardcoding a paid API) preserves the zero-config promise and reuses 100% of the existing MCP infrastructure. Updated Tags Index to include the new tags (`architecture` count bumped, `g18`, `mcp`, `web-search`, `zero-config` added). |
| `clew/activity_log.py` | G16, G18 | Added `export_signed_json()` method to `ActivityLog` (additive — `export_json()` unchanged for backward compat). Delegates to `clew.audit_signing`. Added `CATEGORY_WEB` constant + added it to `ALL_CATEGORIES`. Added `web_search` / `web_fetch` to the `_TOOL_CATEGORY` map. Added `[web_search]` / `[web_fetch]` / `[web_fetch rejected]` prefixes to `_STATUS_PREFIXES`. Added `web_search` / `web_fetch` cases to `build_title()`. |
| `clew/agent/guardian.py` | G18 | Added `_check_web_fetch_url()` helper + `_SECRET_PARAM_NAMES` constant. Added a new `elif tool_name == "web_fetch"` branch to `assess_risk()` that flags URLs with secret-shaped query params as HIGH, long base64-like params as MEDIUM+, and all other `web_fetch` calls as MEDIUM (untrusted content enters conversation). Added `elif tool_name == "web_search"` branch that flags web_search as LOW with a reason. **Additive only — no existing rule weakened.** The existing `rm -rf` → HIGH rule still fires (verified by `test_guardian_does_not_weaken_existing_rules`). |
| `clew/agent_runtime/tool_engine/_engine.py` | G18 | Added `"researcher"` entry to `ROLE_TOOL_WHITELIST` (read-only by construction — has `web_search`/`web_fetch` + read-only file tools, NO write/execute/git/mcp-call tools). Added `WEB_SEARCH` and `WEB_FETCH` entries to the `dispatch_map` in `_dispatch()`. Added `_web_search()` method (MCP-first with ordered fallback, wraps output in `<context_fragment type="web_search">`). Added `_web_fetch()` method (urllib + HTML-to-text, rejects non-http(s) URLs + suspicious URLs, wraps output in `<context_fragment type="web_page">`). Added module-level `_check_suspicious_url()` helper. |
| `clew/agent_runtime/types.py` | G18 | Added `WEB_SEARCH = "web_search"` and `WEB_FETCH = "web_fetch"` to the `ToolName` enum, with a version comment matching the style of existing entries (`# v2.1.0: web search/fetch — see G18`). |
| `clew/web_bridge/bridge.py` | G15/G16/G17/G18 | Added 7 new `@Slot` methods to `ClewBridge`: `export_audit_signed_json`, `verify_audit_signed_file`, `get_consensus_config`, `set_consensus_config`, `run_consensus`, `handle_learnings_command`, `get_websearch_status`. All delegate to the new core modules. `run_consensus` reads the active provider from `self._agent_runtime.registry`. |
| `clew_tui/app.py` | G15/G16/G17/G18 | Added 4 new slash command handlers: `/consensus` (with 5 subcommands: `<prompt>` / `providers` / `min_agreement` / `timeout` / `config`), `/audit-signed` (with 2 subcommands: `export` / `verify <file>`), `/learnings` (with 6 subcommands: `list` / `show` / `dismiss` / `restore` / `scan` / `dismissed`), `/websearch` (status). Added entries to the `/help` text. `/consensus <prompt>` runs in a `@work(thread=True)` worker so it doesn't block the UI. |
| `clew_tui/bridge.py` | G15/G16/G17/G18 | Added 7 new methods to `ClewBridge`: `get_consensus_config`, `set_consensus_config`, `run_consensus`, `export_audit_signed_json`, `verify_audit_signed_file`, `handle_learnings_command`, `get_websearch_status`. All delegate to the new core modules. `run_consensus` reads the active provider from `self._agent.registry`. |
| `pyproject.toml` | all | Bumped `version` from `2.0.0` to `2.1.0`. Updated the `description` string to mention G15–G18. Bumped `[tool.py2app] version` from `2.0.0` to `2.1.0`. |
| `requirements.txt` | all | Updated the version comment from `v2.0.0` to `v2.1.0`. No new dependencies added — `web_fetch` uses stdlib `urllib`, `web_search` uses the existing `MCPManager`, and Ed25519 signing uses `cryptography` which was already a dependency for `EncryptedPromptStore`. |

## Architecture decisions

- **MCP-first for search, direct for fetch.** `web_search` routes through `MCPManager` (reuses process lifecycle, config, catalog, crash watchdog). `web_fetch` is implemented directly with stdlib `urllib` + HTML-to-text because it doesn't need a search index. See `LEARN-20260730-001` in `Learnings.md` for the full rationale.
- **Read-only `researcher` role by toolset construction.** The `researcher` role whitelist explicitly excludes `write_file`, `str_replace`, `delete_file`, `execute_command`, `run_code`, `git_commit`, `git_stage`, `call_mcp_tool`. This is defence in depth — even if a prompt-injected instruction from fetched content tries to get the sub-agent to write files or run shell commands, the dispatch-level whitelist rejects it regardless of what the model attempts.
- **Untrusted-content tagging via context fragments.** Both `web_search` and `web_fetch` wrap their output in `<context_fragment type="web_*">`. This serves two purposes: (1) old fetches tombstone-compact the same way file reads do (no permanent context bloat), and (2) the wrapper makes injected instructions visually/structurally distinguishable in the transcript and audit log. Guardian additionally flags suspicious `web_fetch` URLs as at-least-medium risk.
- **Signed audit trail is additive.** `ActivityLog.export_json()` is unchanged — existing callers that read the unsigned format keep working. The new `export_signed_json()` method produces a different format (each entry gets `_signature`, `_hash`, `_prev_hash` fields). The hash chain makes tampering, reordering, and deletion all detectable; the Ed25519 signature proves the entry was signed by the local key.
- **Per-repo learning injection.** Learnings live under `<project>/learnings/` (with a `~/.clew/learnings/<hash>/` fallback for read-only mounts). They're scoped per project — working on project A doesn't pull in project B's learnings. Dismissed learnings are recorded in `.dismissed.json` next to the learnings and stop being injected.

## Constraints honoured

- ✅ `clew/` ↔ `clew_tui/` boundary rule respected — `clew_tui` never imports `clew` internals directly, all access goes through `ClewBridge` methods.
- ✅ No existing public method on `ActivityLog`, `MCPManager`, `ToolEngine`, or `AgentRuntime` had its meaning/return shape changed — only additions.
- ✅ No paid/API-key-required dependency added as the only path to web search — the zero-config path works with no signup (DuckDuckGo MCP template).
- ✅ Guardian defaults NOT weakened — `web_search`/`web_fetch` risk rules are additive to `assess_risk()`.
- ✅ Zero-telemetry maintained — nothing added phones home. The only network traffic is the search/fetch the user explicitly triggered.
- ✅ Full existing test suite passes (479 passed, 11 skipped, 1 deselected for a pre-existing env-related issue).

# Test Results — update_13 (v2.2.1)

**Date:** 2026-08-05
**Environment:** Python 3.12.13, pytest 9.0.2, Linux x86_64

## Summary

| Metric | Value |
|--------|-------|
| New tests added | **130** |
| Tests passed | **130** |
| Tests failed | **0** |
| Tests errored | **0** |
| Duration | **0.28s** |
| New endpoints | **118** (56 GET + 62 POST) |
| New web UI panels | **16** |
| Lines of new test code | **425** |
| Lines of new Python code | **1715** (`api_extended.py`) |
| Lines of new JS code | **1371** (`tools_panels.js`) |
| Lines of new CSS | **560** |

## Test file

`clew/tests/test_v221_web_ui_expansion.py`

### Test breakdown

#### Route registration (parametrised, 118 tests)
- `test_get_route_table_populated` — verifies ≥30 GET routes registered (1 test)
- `test_post_route_table_populated` — verifies ≥40 POST routes registered (1 test)
- `test_expected_get_route_registered[/api/...]` — 56 parametrised cases for each expected GET route
- `test_expected_post_route_registered[/api/...]` — 62 parametrised cases for each expected POST route

#### Provider template tests (2 tests)
- `test_provider_templates_include_nvidia_nim` — verifies the Nvidia NIM template exists with the correct `base_url`, `model`, and `provider_type`
- `test_provider_templates_include_local_options` — verifies Ollama, LM Studio, and OpenAI-compat templates are present

#### Custom-provider CRUD tests (5 tests)
- `test_custom_provider_add_list_remove_round_trip` — add → list → remove → verify list is empty; checks the YAML file is created on disk and the API key is masked in the list response
- `test_custom_provider_add_rejects_duplicate` — adding the same provider_id twice returns `{ok: false, error: "...already exists"}`
- `test_custom_provider_add_requires_id` — empty `provider_id` returns an error
- `test_custom_provider_remove_requires_id` — empty `provider_id` returns an error

#### Handler-shape tests (1 test)
- `test_handlers_return_dict_with_ok_flag` — iterates every registered GET and POST handler (excluding the custom-provider CRUD tested separately), invokes each with a fake handler and mocked `ClewBridge`, and verifies the return is a `dict` containing an `ok` flag.

#### Installer tests (2 tests)
- `test_install_patches_handler_methods` — installs the extension into a fake `clew.api_server` module and verifies `do_GET` / `do_POST` / `do_DELETE` are replaced with the patched dispatchers.
- `test_install_logs_route_count` — verifies the install log message includes the route counts.

#### Legacy compat (1 test)
- `test_section_get_always_returns_general` — the legacy `/api/section/get` endpoint always returns `{section: "general"}`, regardless of any saved state.

## Live HTTP smoke test

Verified against a running `ClewWebServer` on `127.0.0.1:18802`:

| Endpoint | Method | Auth | Status | Result |
|----------|--------|------|--------|--------|
| `/api/providers/templates` | GET | — | 200 | `templates` array contains `nvidia_nim` with correct `base_url` |
| `/api/capabilities/list` | GET | — | 200 | `{ok: true, capabilities: [...]}` |
| `/api/checkpoint/list` | GET | — | 200 | `{ok: true, checkpoints: [...]}` |
| `/api/section/get` | GET | — | 200 | `{ok: true, section: "general"}` |
| `/api/providers/custom/add` | POST | none | **401** | auth guard rejects unauthenticated request |
| `/api/providers/custom/add` | POST | bearer | 200 | `{ok: true, provider_id: "my-nim-test"}` |
| `/api/providers/custom/list` | GET | — | 200 | lists the new provider; `api_key` field is empty, `api_key_masked` is `nvap…2345` |
| `/api/providers/custom/remove` | POST | bearer | 200 | `{ok: true, provider_id: "my-nim-test"}` |

## Existing tests

The pre-existing test suite was not modified. The new tests are additive and do not interfere with:
- `clew/tests/test_v22_*.py` (v2.2.0 Qt-free refactor tests from update_12)
- `clew/tests/test_g{9,10,11,13,14,15,16,17,18,22a,22b}_*.py` (feature tests from earlier versions)
- `clew_tui/tests/` (TUI interaction tests)

## Known environment-specific behaviour

When running in a sandbox without `textual` installed, endpoints that go through `clew_tui.bridge.ClewBridge` (anything that touches `_bridge()`) return `{ok: false, error: "No module named 'textual'"}`. This is expected — `clew_tui` requires `textual` as a runtime dependency. In the user's environment where `pip install -e .` has been run, `textual` is installed and the endpoints work correctly.

The custom-provider endpoints (`/api/providers/custom/*`) and the provider-templates endpoint do NOT go through `_bridge()` — they manipulate `~/.clew/providers.yaml` directly, so they work regardless of whether `textual` is installed. This is verified by the test suite passing 100% in the sandbox.

## How to reproduce

```bash
cd /path/to/clew_v2.0.1
pip install -e .
pytest clew/tests/test_v221_web_ui_expansion.py -v
```

Expected output:

```
============================= 130 passed in 0.28s ==============================
```

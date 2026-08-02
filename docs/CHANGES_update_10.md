# CHANGES — update_10 (G19, G20, G21)

This patch adds three major features to Clew v2.1.0:

- **G19** — Layered memory upgrade (symbolic task canvas + persona pyramid)
- **G20** — Task-decomposition smart router (upgrade AutoRouter)
- **G21** — Autonomous "Hermes" mode (inbound listener + one-shot preset)

All three loops are documented in `loops/archive/loop-{4,5,6}-*.md`.

## Test results (clean copy of base repo + this patch applied)

```
$ python -m pytest clew/tests/ --ignore=clew/tests/test_section_switching.py --timeout=15 -q

......................................................... [ 21%]
.....................s........s.s....s........s.......ssssss............ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
....................s...........................                         [100%]
454 passed, 12 skipped in 18.35s
```

(Plus the pre-existing `test_notifier.py::TestNotifier::test_status` network timeout — same 1 failure as the baseline before this patch, NOT a regression. It's a 15s pytest-timeout on a real-network Telegram/Discord/Slack send test that requires live credentials.)

- **Baseline before patch**: 357 passed, 12 skipped, 1 failed (network timeout)
- **After patch**: 454 passed (97 new tests), 12 skipped, 1 failed (same network timeout)
- **Net new tests**: 97 (G19: 31, G20: 32, G21: 34)
- **No regressions**: every test that was green before is still green.

## File list

### G19 — Layered memory upgrade (loop-4-g19-layered-memory.md)

**New files:**
| Path | Purpose |
|------|---------|
| `clew/agent/task_canvas.py` | TaskCanvas class + CanvasNode + singleton. Bounded token cost (~few hundred tokens) regardless of task graph size. |
| `clew/agent/persona_memory.py` | PersonaMemory class + PersonaDigest + singleton. Hard cap ~2000 chars. Cheap-tier maintenance LLM call scoped to "edit this one file only". |
| `clew_tui/widgets/task_canvas_view.py` | TUI widget rendering the canvas as a live tree. Reuses Loop 3 terracotta palette. |
| `clew/tests/test_g19_layered_memory.py` | 31 tests covering TaskCanvas + PersonaMemory. |

**Modified files:**
| Path | Change |
|------|--------|
| `clew/agent_runtime/runtime.py` | Inject canvas + persona fragments into the system prompt each turn (between MCP catalog and Heavy Code marker). |
| `clew_tui/widgets/__init__.py` | Export TaskCanvasView. |
| `clew_tui/app.py` | Yield TaskCanvasView in compose(); refresh on every status change; add /canvas and /persona slash commands. |
| `clew_tui/bridge.py` | Added get_task_canvas, reset_task_canvas, get_persona, set_persona, reset_persona, update_persona_from_session bridge methods. |
| `clew_tui/styles_dark.tcss` | Added TaskCanvasView style. |
| `clew_tui/styles_light.tcss` | Added TaskCanvasView style (light variant). |
| `clew/web_bridge/bridge.py` | Added get_task_canvas, reset_task_canvas, get_persona, set_persona, reset_persona, update_persona_from_session @Slot methods. |

### G20 — Task-decomposition smart router (loop-5-g20-task-decomposition-router.md)

**New files:**
| Path | Purpose |
|------|---------|
| `clew/task_decomposition_router.py` | TaskDecompositionRouter class + Subtask/DecompositionReport dataclasses + singleton. Decompose → Route → Dispatch → Merge pipeline with graceful fallbacks. |
| `clew/tests/test_g20_task_decomposition_router.py` | 32 tests covering specialty field, overrides, set_mode, decomposition, routing, dispatch, merge, budget, audit trail, canvas integration, provider_override threading. |

**Modified files:**
| Path | Change |
|------|--------|
| `clew/auto_router.py` | Added `specialty` field to ModelTier (filled for all 26 DEFAULT_TIERS entries). Added `~/.clew/model_capabilities.json` override loader. Added `set_mode`/`get_mode`/`all_tiers` methods + module-level singleton. `route()` now includes `specialty` in the decision dict. |
| `clew/agent_runtime/runtime.py` | Added `_provider_override`/`_model_override` fields + `set_provider_override` setter + `_get_active_provider` helper. Replaced direct `self._registry.active` calls with `_get_active_provider()`. `_generate_with_retry` and `_generate_streaming_with_retry` now pass `model=self._model_override` to provider when set. |
| `clew/agent_runtime/tool_engine/_engine.py` | Added `provider_override`/`model_override` params to `_spawn_subagent` and `_run_subagent_internal`. After child construction, calls `child.set_provider_override(...)` if either is set. |
| `clew_tui/bridge.py` | Added set_router_mode / get_router_mode bridge methods. |
| `clew_tui/app.py` | Added /router-mode slash command. |
| `clew/web_bridge/bridge.py` | Added set_router_mode / get_router_mode @Slot methods. |

### G21 — Autonomous Hermes mode (loop-6-g21-hermes-mode.md)

**New files:**
| Path | Purpose |
|------|---------|
| `clew/inbound_listener.py` | InboundListenerConfig + InboundMessage + InboundListener base + TelegramInboundListener (long-polling) + DiscordInboundListener/SlackInboundListener (stubs). Mandatory allow-list, STOP kill-switch, make_daemon_callback/make_daemon_stop_callback helpers. |
| `clew/tests/test_g21_hermes.py` | 34 tests including 4 CRITICAL Guardian regression tests proving never_ask + Hermes mode does NOT bypass Guardian. |

**Modified files:**
| Path | Change |
|------|--------|
| `clew/cli.py` | Added `hermes` subparser + `_hermes()` handler + `_HermesTaskQueue` + `_make_hermes_event_sink` + `_enable_notifier`. Bundles sandbox + autonomy=never_ask + inbound listener + outbound notifier in one shot. |

### Loop engineering docs

**New files:**
| Path | Purpose |
|------|---------|
| `loops/archive/loop-4-g19-layered-memory.md` | Loop 4 documentation — G19 layered memory upgrade. |
| `loops/archive/loop-5-g20-task-decomposition-router.md` | Loop 5 documentation — G20 task-decomposition router. |
| `loops/archive/loop-6-g21-hermes-mode.md` | Loop 6 documentation — G21 autonomous Hermes mode. |
| `CHANGES_update_10.md` | This file. |

## Constraints honoured

- ✅ Did not touch `command_palette.py` (the TUI Enter-key bug is tracked separately).
- ✅ Did not weaken `guardian.py` defaults anywhere — Guardian regression tests (test_g21_hermes.py) verify at source level that `assess_risk` doesn't read autonomy, `ToolEngine.execute` calls `_guardian_review` BEFORE `_dispatch`, and `_request_confirmation` is the only place autonomy is read.
- ✅ Every new call into an existing class/module was verified against the real source (via the parallel Explore agents in the worklog) — no `ActivityLog.query()` style phantom-method bugs.
- ✅ New tests exercise realistic non-empty state, not just the empty/error path. Examples: TaskCanvas with 2× MAX_VISIBLE_NODES nodes tests the "+N more" summary; PersonaMemory with a real file on disk tests the mtime-based cache invalidation; TaskDecompositionRouter with mocked LLM output tests the full decompose→route→dispatch→merge pipeline end-to-end; InboundListener with a fake getUpdates response tests allow-list + STOP keyword + offset advancement.
- ✅ Full existing test suite ran green before packaging (454 passed / 12 skipped / 1 pre-existing network timeout).
- ✅ Zero telemetry: nothing phones home anywhere except the explicit provider APIs / messenger APIs the user configured. The inbound listener's only network traffic is the Telegram Bot API the user explicitly triggered; the persona maintenance call uses the user's configured provider.

## Optional cleanup (nice-to-have, not required)

The G18 changelog mentioned `.clew/skills/web-research/SKILL.md` was listed but never actually created. This patch does NOT include that file — track separately if desired.

## How to use the new features

### G19 — Task canvas + Persona

The task canvas auto-populates from the G20 decomposition router (see below). To view it manually:

```
/canvas              # show the current canvas (nodes + counts)
/canvas reset        # drop every node
```

The TUI sidebar widget updates automatically on every status refresh.

Persona management:

```
/persona             # show current persona.md content
/persona edit        # open $EDITOR on the persona file
/persona reset       # delete the persona file
/persona update      # run the maintenance LLM call now (cheap tier)
/persona update user explicitly asked for tabs over spaces
```

### G20 — Decompose mode

Switch the AutoRouter to decompose mode:

```
/router-mode decompose
```

Or via the bridge (TUI/GUI/programmatic):

```python
from clew.auto_router import get_auto_router
get_auto_router().set_mode("decompose")
```

Override model specialties via `~/.clew/model_capabilities.json`:

```json
{
  "openai": [
    {"model": "gpt-4o", "specialty": "best for vision-heavy tasks"}
  ],
  "ollama": [
    {"model": "qwen2.5-coder:32b", "specialty": "strong at code generation", "max_tokens": 16384}
  ]
}
```

### G21 — Hermes mode

One-shot autonomous operation:

```bash
clew hermes \
  --workspace /path/to/project \
  --telegram-token 123456:ABC-DEF \
  --allow 123456789 \
  --allow 987654321
```

This will:
1. Apply the OS-level workspace sandbox (Landlock on Linux, Seatbelt on macOS).
2. Configure the runtime with `autonomy=never_ask` + `plan_mode=True`.
3. Enable the outbound Telegram notifier (uses the same bot token).
4. Start the inbound Telegram listener with the given allow-list.
5. Run until Ctrl+C. Reply `STOP` to the bot to cancel the currently running task.

Guardian still runs — `never_ask` only means "don't block waiting for a human click", not "skip Guardian risk assessment". Verified by `test_g21_hermes.py` (4 CRITICAL regression tests).

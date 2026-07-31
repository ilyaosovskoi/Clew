# Loop 3: REFACTOR + FEAT — TUI Visual Overhaul (Warm, Modern, Content-Forward)

## Loop Identity
| Field | Value |
|-------|-------|
| **Loop ID** | `LOOP-2026-07-31-003` |
| **Title** | TUI Visual Overhaul — Warm, Modern, Content-Forward |
| **Owner** | @auto |
| **Start Date** | 2026-07-31 |
| **Target Close Date** | 2026-07-31 |
| **Related Issues/PRs** | IMPLEMENTATION_PROMPT_GLM.md Loop 3 |
| **Parent Loop** | None |

---

## Problem Statement
The current TUI uses a GitHub-dark palette (#0d1117 bg, #238636 green accent) that looks generic and cold. The design needs to be warmer, more modern, and content-forward, inspired by Claude Code (warm terracotta, dashed ASCII input, whimsical verbs) and Grok Build (Unicode rounded borders, semantic color roles).

---

## Success Criteria (MUST be measurable)

| # | Criterion | Metric / Definition of Done | Target | Measurement Method | Weight |
|---|-----------|----------------------------|--------|-------------------|--------|
| 1 | Input box | Dashed ASCII border, muted #888, `>` prefix, surface bg | Working | Visual inspection | High |
| 2 | Tool blocks | Colored border, ToolName · path header, streaming | Working | Visual inspection | High |
| 3 | AI messages | Pure white, no border, clean | Working | Visual inspection | High |
| 4 | User messages | Dashed box, surface bg #373737 | Working | Visual inspection | High |
| 5 | Thinking indicator | Animated cycle (6 frames, 120ms), terracotta+shimmer, whimsical verb | Working | Visual inspection | High |
| 6 | Status bar | Muted, terracotta primary, section/model/tokens | Working | Visual inspection | High |
| 7 | Separators | Thin #505050 between messages | Working | Visual inspection | Medium |
| 8 | Themes | `/theme dark|light` live switches | Working | Manual test | High |
| 9 | No regressions | All existing slash commands work, Textual 8.x compatible | Working | Manual test | High |

---

## Implementation Summary

### Files Created
- `clew_tui/widgets/thinking.py` — ThinkingIndicator widget with animated spinner + whimsical verbs
- `clew_tui/widgets/tool_block.py` — ToolBlock widget with Unicode borders + streaming

### Files Modified
- `clew_tui/styles_dark.tcss` — Full rewrite: warm terracotta palette (#1a1a1a bg, #d77757 primary, #fd5db1 hot pink)
- `clew_tui/styles_light.tcss` — Full rewrite: light variant with same hue relationships
- `clew_tui/widgets/chat_log.py` — Redesigned: AI messages plain white, hot pink tool blocks, separators
- `clew_tui/widgets/input_box.py` — Redesigned: `> ` prefix, dashed border placeholder
- `clew_tui/widgets/status_bar.py` — Redesigned: terracotta primary, muted secondary
- `clew_tui/widgets/__init__.py` — Added ThinkingIndicator and ToolBlock exports
- `clew_tui/app.py` — Added `/theme` slash command, wired section parser

---

## Status: CLOSED

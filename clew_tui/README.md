# clew_tui — full-screen terminal UI for Clew

A Textual-based TUI in the spirit of Claude Code: a persistent input line at the
bottom, a scrollable conversation area with Markdown/code rendering, live
per-step updates and per-token streaming, visually distinct tool-call blocks,
an approval modal for side-effecting actions, and a status bar.

## Run

```bash
pip install textual          # only new dependency; rich is already required
python -m clew_tui           # uses your saved ~/.clew/config.json provider
python -m clew_tui --provider groq --model llama-3.3-70b --workspace .
python -m clew_tui --planning --workspace /path/to/project
```

Options: `--workspace/-w`, `--provider/-p`, `--model/-m`, `--api-base`,
`--section {general,heavy_code,office}`, `--max-iterations`,
`--planning` (enable planning mode).

## Keys

- `Enter` — send the prompt
- `Up` / `Down` — cycle input history
- `Ctrl+C` — interrupt the running turn (does not quit)
- `Ctrl+D` — quit
- `Ctrl+G` — launch the Clew GUI window for the same workspace
- In the approval modal: `y` approve, `n` / `Esc` deny

## How it differs from `clew/cli.py`

`clew/cli.py` is the traditional one-shot argparse CLI (run a command, print,
exit). `clew_tui` is a persistent full-screen app that redraws itself and shows
the agent working step by step — and, when the provider supports streaming,
character by character.

## Architecture — why this is a separate package

The core already had three parallel agent-loop paths. To avoid adding a fourth,
`clew_tui` lives at the top level (not inside `clew/`) and touches core internals
in exactly ONE place: `clew_tui/bridge.py` (`ClewBridge`). Widgets never import
`clew.agent_runtime` directly — if a widget needs something, add a method to the
bridge.

The bridge drives the proven production path: a plain `clew.agent_runtime.AgentRuntime`
constructed like `clew/cli.py` does, wired through:

- `on_event(AgentEvent, dict)` — reuses the existing event model (no new one).
- `on_token_delta(chunk)` — when set, AgentRuntime uses `provider.stream()` instead
  of `provider.generate()`, emitting each text chunk through this callback AND as
  an `AgentEvent.TOKEN_DELTA` event. The TUI ChatLog widget appends each chunk
  to the live response, producing real character-by-character streaming. The full
  text is still accumulated for the agent loop to parse tool calls.
- `set_cancel_check(lambda: stop.is_set())` — cooperative Ctrl+C interruption.
- `set_confirm_callback(...)` + `set_autonomy("always_ask")` — approval modal.
- `token_tracker.get_token_tracker().stats()` — status-bar token/cost totals.

It deliberately does **not** use `agent_orchestrator.patch_runtime` (unused in
production and raises on a vanilla runtime) or the `AgentRuntimeV2` path.

```
clew_tui/
├── __init__.py
├── __main__.py          # python -m clew_tui
├── app.py               # ClewTUIApp(textual.app.App)
├── bridge.py            # ONLY module that knows clew internals
├── widgets/
│   ├── chat_log.py      # scrollable dialog, Markdown + tool-call panels + streaming
│   ├── input_box.py     # bottom input + Up/Down history
│   ├── status_bar.py    # provider/model, tokens, state
│   └── approval_modal.py
└── styles.tcss
```

## GUI ↔ TUI switching

The TUI and GUI are fundamentally different render surfaces — a Qt window vs.
a full-screen terminal — so they cannot "redraw" into each other. Instead:

- **GUI → TUI**: The topbar "Terminal" button in the GUI launches `clew_tui`
  as a new process in a new terminal window (platform-specific: Terminal.app
  on macOS, gnome-terminal/konsole/xterm on Linux, wt.exe/cmd.exe on Windows).
  The GUI stays open by default but can be configured to close on switch
  (see below).

- **TUI → GUI**: Press `Ctrl+G` inside the TUI to launch `python -m clew --project`
  as a separate process. The TUI stays open by default.

- **No live chat transfer**: The conversation history is NOT synchronised
  between GUI and TUI in this iteration. This is a future task — the shared
  chat storage already exists (`~/.clew/chats/`), but format alignment
  between GUI's JSON format and TUI's event-based rendering is not yet done.

- **Configurable**: The `close_on_switch` key in `~/.clew/config.json`
  (default: `false`) controls whether the source interface closes after
  launching the other. Set it via the GUI Settings → Advanced → "Close on
  switch" checkbox, or manually edit the config file.

## Known limitations (honest)

- **Streaming granularity depends on the provider.** When the active provider
  supports `ProviderCapability.STREAMING`, text appears character-by-character
  via `TOKEN_DELTA` events. When it doesn't (e.g. local LM Studio without
  SSE), the runtime falls back to `provider.generate()` and text appears
  per-step (thought / tool_called / tool_result / done) — the same behaviour
  as before.

- **Interruption is cooperative, not instant.** The runtime checks the cancel
  flag (`self._stop.is_set()`) between iterations, before each tool call, and
  between streaming chunks. A tool already mid-execution finishes before the
  loop unwinds — Python threads cannot be force-killed. Additionally, if
  sub-agents were spawned during the turn, interrupting the parent does **not**
  guarantee that already-running child threads stop. This is the same known
  limitation documented in `skill_and_bugs/known-issues.md` — `SubagentHandle`
  does not store a reference to its per-child `CancelToken`, so `cancel_all()`
  cannot individually stop a running child. See that file for details and the
  planned fix (adding a `cancel_token` field to `SubagentHandle`).

- **Plan approval is wired but feedback is not.** When planning mode is enabled
  (`--planning`), the agent creates a plan and the `ApprovalModal` lets the
  user approve or reject it. Rejection currently just stops execution — the
  user must type new instructions in the input box. Sending structured feedback
  back to the planner (the `plan_feedback` parameter in `AgentRuntime.run()`)
  is not yet wired from the modal; it would require a text-input modal rather
  than a boolean approve/reject.

"""app.py — ClewTUIApp, the Textual entry point.

Layout: status bar on top, scrollable chat log in the middle, inline
command-suggestion bar above the input, input line at the bottom.

The original Input.Submitted mechanism is preserved — Enter works natively.
Inline suggestions appear when "/" is typed but do NOT intercept Enter.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input

from .bridge import ClewBridge
from .widgets.approval_modal import ApprovalModal, GuardianModal
from .widgets.chat_log import ChatLog
from .widgets.command_palette import CommandPalette, CommandEntry, SECTIONS, BUILTIN_COMMANDS
from .widgets.command_suggestions import CommandSuggestions, SuggestionItem
from .widgets.input_box import InputBox
from .widgets.status_bar import StatusBar
from .widgets.verification_modal import VerificationModal


class ClewTUIApp(App):
    CSS_PATH = "styles_dark.tcss"
    TITLE = "clew"

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True, show=True),
        Binding("ctrl+d", "quit", "Quit", priority=True, show=True),
        Binding("ctrl+g", "launch_gui", "GUI", show=True),
        Binding("ctrl+p", "open_command_palette", "Commands", show=True),
        Binding("ctrl+t", "toggle_theme", "Theme", show=True),
    ]

    def __init__(self, bridge: Optional[ClewBridge] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bridge = bridge or ClewBridge()
        self._running = False
        self._last_prompt: str = ""
        self._suggestions_active: bool = False
        self._dark_theme: bool = True  # Start with dark theme

    # ---------------------------------------------------------------- compose
    def compose(self) -> ComposeResult:
        yield StatusBar(id="status")
        yield ChatLog(id="chat")
        yield CommandSuggestions(id="suggestions")
        yield InputBox(id="input")

    def on_mount(self) -> None:
        self.bridge.set_event_sink(self._sink)
        self.bridge.set_confirm_handler(self._confirm)
        # v2.0.0 fix: wire the guardian handler so Guardian MODIFY verdicts
        # actually reach the GuardianModal instead of being silently approved.
        self.bridge.set_guardian_handler(self._confirm)
        try:
            from clew import __version__ as _clew_version
        except Exception:
            _clew_version = "2.0.0"
        chat = self.query_one(ChatLog)
        chat.add_system(
            f"clew TUI v{_clew_version} | workspace: {self.bridge.workspace}\n"
            f"Section: {self.bridge.section}\n"
            "Type a request and press Enter. Type / for slash commands.\n"
            "Ctrl+C interrupts, Ctrl+D quits, Ctrl+G launches GUI."
        )
        self._refresh_status("idle")

        # Set up inline suggestions
        sug = self.query_one(CommandSuggestions)
        sug.set_commands(BUILTIN_COMMANDS)
        sug.set_on_select(self._on_suggestion_selected)

        self.query_one(InputBox).focus()

    # --------------------------------------------------------- suggestions
    def _show_suggestions(self, query: str = "") -> None:
        sug = self.query_one(CommandSuggestions)
        custom_cmds = self.bridge.list_slash_commands()
        custom_entries = []
        for c in custom_cmds:
            custom_entries.append(
                CommandEntry(
                    id=c["id"],
                    label=f"/{c['id']}",
                    description=c.get("description", c.get("name", "")),
                    category="custom",
                    has_sub_options=False,
                )
            )
        sug.set_commands(BUILTIN_COMMANDS, custom_entries=custom_entries)
        sug.show_suggestions(query)
        self._suggestions_active = sug.is_visible
        self.query_one(InputBox).set_suggestions_visible(self._suggestions_active)

    def _hide_suggestions(self) -> None:
        sug = self.query_one(CommandSuggestions)
        sug.hide()
        self._suggestions_active = False
        self.query_one(InputBox).set_suggestions_visible(False)

    def _move_suggestion_up(self) -> None:
        self.query_one(CommandSuggestions).move_up()

    def _move_suggestion_down(self) -> None:
        self.query_one(CommandSuggestions).move_down()

    def _select_suggestion(self) -> Optional[SuggestionItem]:
        sug = self.query_one(CommandSuggestions)
        return sug.select_highlighted()

    def _on_suggestion_selected(self, item: SuggestionItem) -> None:
        """Called when user picks a suggestion (Tab or click)."""
        self._hide_suggestions()
        box = self.query_one(InputBox)
        box.value = ""
        if item.needs_sub:
            self._open_sub_palette_for_cmd(item.id)
        else:
            self._execute_builtin_cmd(item.id)
        box.focus()

    # ------------------------------------------------------------- user input
    def _submit_prompt(self, prompt: str) -> None:
        """Core submission logic — called directly from InputBox on Enter.
        This bypasses Input.Submitted which fires after the key handler
        already intercepted Enter."""
        if not prompt:
            return
        if self._running:
            self.bell()
            return

        # If suggestions visible, try selecting highlighted one
        if self._suggestions_active:
            selected = self._select_suggestion()
            if selected is not None:
                return  # _on_suggestion_selected handled it
            self._hide_suggestions()

        # Slash command
        if prompt.startswith("/"):
            self._handle_slash_input(prompt)
            box = self.query_one(InputBox)
            box.remember(prompt)
            box.value = ""
            return

        # Normal message
        box = self.query_one(InputBox)
        box.remember(prompt)
        box.value = ""
        self.query_one(ChatLog).add_user(prompt)
        self._last_prompt = prompt
        self._running = True
        self._refresh_status("thinking")
        self._run_turn(prompt)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fallback handler — intentionally disabled.

        InputBox._on_key intercepts Enter before Input.Submitted can fire.
        Keeping this enabled caused DOUBLE submission: the call from the
        key handler set _running=True, then this handler fired again,
        saw _running=True, and silently dropped the message — meanwhile
        the user's prompt was lost on the first call.
        """
        return

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/hide inline suggestions when input starts/stop with '/'."""
        if event.input.id != "input":
            return
        val = event.value
        if val.startswith("/"):
            self._show_suggestions(val)
        elif self._suggestions_active:
            self._hide_suggestions()

    # --------------------------------------------------------- slash commands
    def _handle_slash_input(self, prompt: str) -> None:
        parts = prompt.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # Bare "/" — open full command palette
        if cmd == "/":
            self.open_command_palette()
            return

        # Custom .md commands
        resolved = self.bridge.resolve_slash_command(prompt)
        if resolved:
            expanded = resolved.get("expanded", prompt)
            box = self.query_one(InputBox)
            box.remember(prompt)
            self.query_one(ChatLog).add_user(prompt)
            self._last_prompt = expanded
            self._running = True
            self._refresh_status("thinking")
            self._run_turn(expanded)
            return

        # Built-in commands
        if cmd == "/section":
            if arg:
                self._exec_section(arg)
            else:
                self._open_sub_palette("section", SECTIONS)
        elif cmd == "/model":
            if arg:
                self._exec_model(arg)
            else:
                self._open_model_palette()
        elif cmd == "/chat":
            if arg:
                self._exec_chat(arg)
            else:
                self._open_chat_palette()
        elif cmd == "/cd":
            if arg:
                self._exec_cd(arg)
            else:
                self._open_cd_palette()
        elif cmd == "/usage":
            self._exec_usage()
        elif cmd == "/files":
            self._exec_files()
        elif cmd == "/clear":
            self._exec_clear()
        elif cmd == "/help":
            self._exec_help()
        elif cmd == "/planning":
            self._exec_planning()
        elif cmd == "/gui":
            self.action_launch_gui()
        elif cmd == "/guardian":
            self._exec_guardian(arg)
        elif cmd == "/collab":
            self._exec_collab(arg)
        elif cmd == "/queue":
            self._exec_queue()
        elif cmd == "/storage":
            self._exec_storage(arg)
        elif cmd == "/sessions":
            self._exec_sessions()
        elif cmd == "/context":
            self._exec_context()
        elif cmd == "/tools":
            self._exec_tools()
        elif cmd == "/capabilities":
            self._exec_capabilities(arg)
        elif cmd == "/second_opinion":
            self._exec_second_opinion(arg)
        elif cmd == "/verify":
            self._exec_verify(arg)
        elif cmd == "/budget":
            self._exec_budget(arg)
        else:
            self.query_one(ChatLog).add_system(
                f"Unknown command: {cmd}. Type /help for available commands."
            )

    # ── Command palette (Ctrl+P) ──────────

    def open_command_palette(self) -> None:
        custom_cmds = self.bridge.list_slash_commands()
        custom_entries = []
        for c in custom_cmds:
            custom_entries.append(
                CommandEntry(
                    id=c["id"],
                    label=f"/{c['id']}",
                    description=c.get("description", c.get("name", "")),
                    category="custom",
                    has_sub_options=False,
                )
            )
        palette = CommandPalette(custom_commands=custom_entries)

        def on_result(result: Optional[Tuple[str, bool]]) -> None:
            if result is None:
                box = self.query_one(InputBox)
                if box.value.startswith("/"):
                    box.value = ""
                box.focus()
                return
            cmd_id, needs_sub = result
            box = self.query_one(InputBox)
            box.value = ""
            if needs_sub:
                self._open_sub_palette_for_cmd(cmd_id)
            else:
                self._execute_builtin_cmd(cmd_id)

        self.push_screen(palette, on_result)

    def action_open_command_palette(self) -> None:
        self.open_command_palette()

    # ── Sub-selection palettes ────────────────────────────────────

    def _open_sub_palette_for_cmd(self, cmd_id: str) -> None:
        if cmd_id == "section":
            self._open_sub_palette("section", SECTIONS)
        elif cmd_id == "model":
            self._open_model_palette()
        elif cmd_id == "chat":
            self._open_chat_palette()
        elif cmd_id == "cd":
            self._open_cd_palette()
        elif cmd_id == "guardian":
            self._open_guardian_palette()
        elif cmd_id == "collab":
            self._open_collab_palette()
        elif cmd_id == "storage":
            self._open_storage_palette()
        else:
            self.query_one(ChatLog).add_system(
                f"Command /{cmd_id} needs a parameter. Type /{cmd_id} <value> directly."
            )
            self.query_one(InputBox).focus()

    def _open_sub_palette(self, cmd_name: str, options: List[Dict[str, Any]]) -> None:
        palette = CommandPalette(
            sub_options=options,
            sub_prompt=f"Select {cmd_name}...",
        )

        def on_result(result: Optional[Tuple[str, bool]]) -> None:
            if result is None:
                self.query_one(InputBox).focus()
                return
            selected_id, _ = result
            if cmd_name == "section":
                self._exec_section(selected_id)
            elif cmd_name == "model":
                self._exec_model(selected_id)
            elif cmd_name == "chat":
                self._exec_chat(selected_id)
            elif cmd_name == "cd":
                self._exec_cd(selected_id)
            elif cmd_name == "guardian":
                self._exec_guardian(selected_id)
            elif cmd_name == "collab":
                self._exec_collab(selected_id)
            elif cmd_name == "storage":
                self._exec_storage(selected_id)
            elif cmd_name == "capability":
                # Treat palette-pick as "/capabilities <id>" — will
                # either run it (no required placeholders) or show
                # detail with the missing ones.
                self._exec_capabilities(selected_id)

        self.push_screen(palette, on_result)

    def _open_model_palette(self) -> None:
        providers = self.bridge.list_providers()
        options = []
        for p in providers:
            active = " (active)" if p.get("active") else ""
            options.append({
                "id": p.get("id", ""),
                "label": f"{p.get('label', p.get('id', ''))}{active}",
                "desc": f"model: {p.get('model', p.get('default_model', '?'))}",
            })
        self._open_sub_palette("model", options)

    def _open_chat_palette(self) -> None:
        chats = self.bridge.list_chats()
        options = []
        for c in chats:
            status_icon = {"done": "done", "error": "err", "running": "run", "idle": "-"}.get(
                c.get("status", "idle"), "-"
            )
            options.append({
                "id": c.get("id", ""),
                "label": f"[{status_icon}] {c.get('title', 'Untitled')}",
                "desc": f"{c.get('message_count', 0)} msgs",
            })
        if not options:
            self.query_one(ChatLog).add_system("No saved chats found.")
            self.query_one(InputBox).focus()
            return
        self._open_sub_palette("chat", options)

    def _open_cd_palette(self) -> None:
        import json
        config_path = os.path.expanduser("~/.clew/config.json")
        recent_dirs = []
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            root = cfg.get("project_root")
            if root:
                recent_dirs.append(root)
        except Exception:
            pass
        recent_dirs.append(self.bridge.workspace)
        home = os.path.expanduser("~")
        recent_dirs.append(home)
        seen = set()
        unique_dirs = []
        for d in recent_dirs:
            if d not in seen:
                seen.add(d)
                unique_dirs.append(d)
        options = []
        for d in unique_dirs:
            basename = os.path.basename(d) or d
            options.append({"id": d, "label": basename, "desc": d})
        self._open_sub_palette("cd", options)

    # ── Command execution ─────────────────────────────────────────

    def _execute_builtin_cmd(self, cmd_id: str) -> None:
        dispatch = {
            "usage": self._exec_usage,
            "files": self._exec_files,
            "clear": self._exec_clear,
            "help": self._exec_help,
            "planning": self._exec_planning,
            "gui": lambda: self.action_launch_gui(),
        }
        handler = dispatch.get(cmd_id)
        if handler:
            handler()
        else:
            self._open_sub_palette_for_cmd(cmd_id)
        box = self.query_one(InputBox)
        if box.value.startswith("/"):
            box.value = ""
        box.focus()

    def _exec_section(self, section_id: str) -> None:
        result = self.bridge.set_section(section_id)
        chat = self.query_one(ChatLog)
        if result.get("ok"):
            name = {"general": "General", "heavy_code": "Heavy Code", "office": "Office Worker"}.get(
                section_id, section_id)
            chat.add_system(f"Section switched to: [b]{name}[/b]")
            self._refresh_status("idle")
        else:
            chat.add_error(f"Failed to switch section: {result.get('error', 'unknown')}")
        self.query_one(InputBox).focus()

    def _exec_model(self, provider_id: str) -> None:
        result = self.bridge.set_provider(provider_id)
        chat = self.query_one(ChatLog)
        if result.get("ok"):
            chat.add_system(
                f"Provider switched to: [b]{result.get('provider', provider_id)}[/b] "
                f"model: [dim]{result.get('model', '?')}[/dim]"
            )
            self._refresh_status("idle")
        else:
            chat.add_error(f"Failed to switch provider: {result.get('error', 'unknown')}")
        self.query_one(InputBox).focus()

    def _exec_chat(self, chat_id: str) -> None:
        chats = self.bridge.list_chats()
        target = None
        for c in chats:
            if c.get("id") == chat_id:
                target = c
                break
        if target:
            self.query_one(ChatLog).add_system(
                f"Chat: [b]{target.get('title', 'Untitled')}[/b] "
                f"({target.get('message_count', 0)} messages, "
                f"status: {target.get('status', 'idle')})\n"
                f"Full chat restore not yet supported in TUI."
            )
        else:
            self.query_one(ChatLog).add_error(f"Chat not found: {chat_id}")
        self.query_one(InputBox).focus()

    def _exec_cd(self, path: str) -> None:
        result = self.bridge.change_workspace(path)
        chat = self.query_one(ChatLog)
        if result.get("ok"):
            chat.add_system(f"Workspace changed to: [b]{result.get('workspace', path)}[/b]")
            self._refresh_status("idle")
        else:
            chat.add_error(f"Failed to change workspace: {result.get('error', 'unknown')}")
        self.query_one(InputBox).focus()

    def _exec_usage(self) -> None:
        s = self.bridge.get_usage()
        chat = self.query_one(ChatLog)
        chat.add_system(
            f"[b]Session Usage[/b]\n"
            f"  Provider: {s.get('provider', '?')}\n"
            f"  Model:    {s.get('model', '?')}\n"
            f"  Tokens:   {s.get('tokens', 0):,}\n"
            f"  Cost:     ${s.get('cost', 0.0):.4f}\n"
            f"  Section:  {self.bridge.section}"
        )
        self.query_one(InputBox).focus()

    def _exec_files(self) -> None:
        names = self.bridge.list_workspace_files()
        chat = self.query_one(ChatLog)
        if names:
            dirs = [n for n in names if n.endswith("/")]
            files = [n for n in names if not n.endswith("/")]
            listing = ""
            if dirs:
                listing += "[b]Directories[/b]\n  " + "  ".join(d.rstrip("/") for d in dirs[:20]) + "\n"
            if files:
                listing += "[b]Files[/b]\n  " + "  ".join(files[:30])
                if len(files) > 30:
                    listing += f"\n  ... and {len(files) - 30} more"
            chat.add_system(f"[b]Workspace: {self.bridge.workspace}[/b]\n{listing}")
        else:
            chat.add_system(f"Workspace: {self.bridge.workspace} - (empty or unreadable)")
        self.query_one(InputBox).focus()

    def _exec_clear(self) -> None:
        chat = self.query_one(ChatLog)
        chat.clear()
        chat.add_system("Chat log cleared.")
        self.query_one(InputBox).focus()

    def _exec_help(self) -> None:
        chat = self.query_one(ChatLog)
        lines = [
            "[b]Slash Commands[/b]",
            "",
            "  [cyan]/section[/cyan]   Switch section (General / Heavy Code / Office)",
            "  [cyan]/model[/cyan]     Switch AI provider/model",
            "  [cyan]/chat[/cyan]      List and browse saved chats",
            "  [cyan]/cd[/cyan]        Change workspace directory",
            "  [cyan]/usage[/cyan]     Show session token usage & cost",
            "  [cyan]/files[/cyan]     List files in workspace",
            "  [cyan]/clear[/cyan]     Clear the chat log",
            "  [cyan]/planning[/cyan]  Toggle planning mode",
            "  [cyan]/guardian[/cyan]  Set Guardian safety level (off/dangerous_only/all)",
            "  [cyan]/collab[/cyan]    Run a collaboration mode (reviewer/codegen/pair/observer)",
            "  [cyan]/queue[/cyan]     Show request queue stats (cooldown, retries)",
            "  [cyan]/storage[/cyan]   Choose chat storage backend (JSON/SQLite)",
            "  [cyan]/sessions[/cyan]  List SQLite-stored chat sessions",
            "  [cyan]/context[/cyan]   View context fragments & compaction stats",
            "  [cyan]/tools[/cyan]     Browse loaded & available progressive tools",
            "  [cyan]/capabilities[/cyan]  Browse & run pre-built capability templates",
            "  [cyan]/second_opinion[/cyan] Toggle cross-model review before risky actions (Pro)",
            "  [cyan]/verify[/cyan]    Cross-model verification of the last response",
            "  [cyan]/budget[/cyan]    Configure token budget & efficiency policy",
            "  [cyan]/gui[/cyan]       Launch the Clew GUI window",
            "  [cyan]/help[/cyan]      Show this help",
            "",
            "Type / to see inline suggestions, Ctrl+P for full command palette.",
            "Ctrl+C=interrupt | Ctrl+D=quit | Ctrl+G=GUI | Ctrl+P=commands | Ctrl+T=theme",
            "",
            "[dim]Custom .md commands from .claude/commands/ also appear.[/dim]",
        ]
        custom = self.bridge.list_slash_commands()
        if custom:
            lines.append("")
            lines.append("[b]Custom Commands[/b]")
            for c in custom:
                lines.append(f"  [cyan]/{c['id']}[/cyan]  {c.get('description', c.get('name', ''))}")
        chat.add_system("\n".join(lines))
        self.query_one(InputBox).focus()

    def _exec_planning(self) -> None:
        result = self.bridge.toggle_planning()
        chat = self.query_one(ChatLog)
        state = "ON" if result.get("planning") else "OFF"
        chat.add_system(f"Planning mode: [b]{state}[/b]")
        self._refresh_status("idle")
        self.query_one(InputBox).focus()

    def _exec_guardian(self, arg: str) -> None:
        arg = arg.strip().lower()
        valid = {"off", "dangerous_only", "all"}
        if arg not in valid:
            chat = self.query_one(ChatLog)
            chat.add_system(
                f"Usage: /guardian <level>\n"
                f"  Levels: off | dangerous_only | all\n"
                f"  Current: {self.bridge.get_guardian_level().get('level', 'off')}"
            )
            self.query_one(InputBox).focus()
            return
        result = self.bridge.set_guardian_level(arg)
        chat = self.query_one(ChatLog)
        if result.get("ok"):
            chat.add_system(f"Guardian level set to: [b]{result['level']}[/b]")
        else:
            chat.add_error(f"Failed to set Guardian level: {result.get('error', 'unknown')}")
        self.query_one(InputBox).focus()

    def _open_guardian_palette(self) -> None:
        options = [
            {"id": "off", "label": "Off",
             "desc": "Guardian disabled — fastest, no LLM safety review"},
            {"id": "dangerous_only", "label": "Dangerous tools only",
             "desc": "Review only high-risk tool calls (recommended)"},
            {"id": "all", "label": "All tools",
             "desc": "Review every medium+ risk tool call"},
        ]
        self._open_sub_palette("guardian", options)

    def _open_collab_palette(self) -> None:
        modes = self.bridge.list_collaboration_modes()
        options = [{"id": m["id"], "label": m["label"], "desc": m["desc"]}
                   for m in modes]
        self._open_sub_palette("collab", options)

    def _open_storage_palette(self) -> None:
        current = self.bridge.get_persistence_backend()
        options = [
            {"id": "json", "label": "JSON files",
             "desc": "~/.clew/chats/*.json  (default)" + ("  [active]" if current == "json" else "")},
            {"id": "sqlite", "label": "SQLite database",
             "desc": "~/.clew/chats.sqlite3  (single-file, O(log N) append)" + ("  [active]" if current == "sqlite" else "")},
        ]
        self._open_sub_palette("storage", options)

    def _exec_collab(self, arg: str) -> None:
        """Run a collaboration-mode task. arg = '<mode> <task text>' or '<mode>'."""
        arg = arg.strip()
        if not arg:
            self._open_collab_palette()
            return
        parts = arg.split(None, 1)
        mode = parts[0].lower()
        task = parts[1].strip() if len(parts) > 1 else ""
        valid_modes = {"single", "reviewer", "codegen", "pair", "observer"}
        if mode not in valid_modes:
            self.query_one(ChatLog).add_system(
                f"Unknown collaboration mode: {mode}\n"
                f"Valid: {', '.join(sorted(valid_modes))}"
            )
            self.query_one(InputBox).focus()
            return
        if mode == "single":
            self.query_one(ChatLog).add_system(
                "Single mode = no collaboration. Just type your task as a normal prompt."
            )
            self.query_one(InputBox).focus()
            return
        if not task:
            self.query_one(ChatLog).add_system(
                f"Usage: /collab {mode} <task description>\n"
                f"Example: /collab {mode} Refactor the auth module to use async/await"
            )
            self.query_one(InputBox).focus()
            return
        # Render the task as a user message, then run collaboration in a worker
        chat = self.query_one(ChatLog)
        chat.add_user(f"[collab:{mode}] {task}")
        self._running = True
        self._refresh_status("thinking")
        self._run_collaboration(mode, task)

    @work(thread=True, exclusive=True)
    def _run_collaboration(self, mode: str, task: str) -> None:
        try:
            result = self.bridge.run_collaboration(mode, task)
            self.call_from_thread(self._on_collab_done, result)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, str(e))

    def _on_collab_done(self, result: Dict[str, Any]) -> None:
        chat = self.query_one(ChatLog)
        if not result.get("ok"):
            chat.add_error(f"Collaboration failed: {result.get('error', 'unknown')}")
            self._running = False
            self._refresh_status("idle")
            return
        output = result.get("output", "") or ""
        if output:
            chat.add_final(output)
        metadata = result.get("metadata", {}) or {}
        verdict = metadata.get("verdict")
        iterations = result.get("iterations", 0)
        if verdict:
            feedback = metadata.get("feedback", "") or metadata.get("reason", "")
            chat.add_reviewer_verdict(verdict, feedback, iterations)
        observer_warnings = metadata.get("observer_warnings") or []
        if observer_warnings:
            chat.add_observer_warnings(observer_warnings)
        self._running = False
        self._refresh_status("idle")
        self.query_one(InputBox).focus()

    def _exec_queue(self) -> None:
        stats = self.bridge.get_queue_stats()
        chat = self.query_one(ChatLog)
        if not stats:
            chat.add_system(
                "[b]Request Queues[/b]\n"
                "  No provider queues registered yet.\n"
                "  Queues are created on first provider call."
            )
        else:
            lines = ["[b]Request Queues[/b]", ""]
            for pid, s in stats.items():
                cooldown = s.get("cooldown_until")
                if cooldown:
                    import time as _t
                    remaining = max(0, int(cooldown - _t.time()))
                    cooldown_str = f"{remaining}s"
                else:
                    cooldown_str = "-"
                lines.append(
                    f"  [cyan]{pid}[/cyan]:  "
                    f"in-flight {s.get('in_flight', 0)}/{s.get('max_in_flight', 1)}  "
                    f"pending {s.get('pending', 0)}  "
                    f"retries {s.get('total_retries', 0)}  "
                    f"errors {s.get('total_errors', 0)}  "
                    f"cooldown: {cooldown_str}"
                )
            chat.add_system("\n".join(lines))
        self.query_one(InputBox).focus()

    def _exec_storage(self, arg: str) -> None:
        arg = arg.strip().lower()
        if not arg:
            self._open_storage_palette()
            return
        result = self.bridge.set_persistence_backend(arg)
        chat = self.query_one(ChatLog)
        if result.get("ok"):
            chat.add_system(f"Storage backend set to: [b]{result['backend']}[/b]")
        else:
            chat.add_error(f"Failed: {result.get('error', 'unknown')}")
        self.query_one(InputBox).focus()

    def _exec_sessions(self) -> None:
        sessions = self.bridge.list_sqlite_sessions()
        chat = self.query_one(ChatLog)
        if not sessions:
            chat.add_system(
                "[b]SQLite Sessions[/b]\n"
                "  No sessions found. Switch to SQLite storage via /storage "
                "and run some chats to populate the database."
            )
        else:
            lines = ["[b]SQLite Sessions[/b] (~/.clew/chats.sqlite3)", ""]
            for s in sessions[:50]:
                lines.append(
                    f"  [{s.get('id', '?')[:8]}]  "
                    f"{s.get('title', 'Untitled')}  "
                    f"({s.get('message_count', 0)} msgs)"
                )
            if len(sessions) > 50:
                lines.append(f"  ... and {len(sessions) - 50} more")
            chat.add_system("\n".join(lines))
        self.query_one(InputBox).focus()

    def _exec_context(self) -> None:
        stats = self.bridge.get_compaction_stats()
        chat = self.query_one(ChatLog)
        if not stats:
            chat.add_system(
                "[b]Context Fragments[/b]\n"
                "  No compaction has run yet this session."
            )
        else:
            chat.add_system(
                "[b]Context Fragments (last compaction)[/b]\n"
                f"  Original fragments:  {stats.get('original_fragments', '?')}\n"
                f"  Kept:                {stats.get('kept_fragments', '?')}\n"
                f"  Dropped (tombstoned): {stats.get('dropped_fragments', '?')}\n"
                f"  Chars saved:         {stats.get('chars_saved', 0):,}"
            )
        self.query_one(InputBox).focus()

    def _exec_tools(self) -> None:
        state = self.bridge.get_tool_catalog_state()
        chat = self.query_one(ChatLog)
        loaded = state.get("loaded", [])
        available = state.get("available", [])
        saved = state.get("prompt_chars_saved", 0)
        chat.add_system(
            f"[b]Progressive Tools Catalog[/b]\n"
            f"  Loaded:   {len(loaded)}  (full schemas shipped to the model)\n"
            f"  Available: {len(available)}  (callable via select_tools)\n"
            f"  Prompt chars saved: {saved:,}\n"
            f"\n"
            f"  Loaded: {', '.join(loaded[:20])}{'...' if len(loaded) > 20 else ''}\n"
            f"  Sample available: {', '.join(available[:20])}{'...' if len(available) > 20 else ''}"
        )
        self.query_one(InputBox).focus()

    # ── v2.0.1 (G7) — Capability catalog ──────────────────────────

    def _exec_capabilities(self, arg: str) -> None:
        """Browse the capability catalog and optionally run one.

        Usage:
            /capabilities                — open browse palette
            /capabilities <id>           — show capability detail
            /capabilities <id> k=v ...   — fill placeholders and run
        """
        arg = arg.strip()
        if not arg:
            self._open_capability_palette()
            return

        parts = arg.split(None, 1)
        cap_id = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""

        cap = self.bridge.get_capability(cap_id)
        if cap is None:
            self.query_one(ChatLog).add_system(
                f"Unknown capability: {cap_id}\n"
                f"Type /capabilities (no arg) to browse the catalog."
            )
            self.query_one(InputBox).focus()
            return

        # If the capability has no required placeholders, run it now.
        placeholders = cap.get("placeholders", []) or []
        required = [p["name"] for p in placeholders if p.get("required", True)]

        if not required and not rest:
            self._run_capability(cap_id, {})
            return

        # If the user passed inline values, parse "k=v k2=v2 ..."
        values: Dict[str, str] = {}
        if rest:
            for token in self._split_kv_tokens(rest):
                if "=" in token:
                    k, _, v = token.partition("=")
                    values[k.strip()] = v.strip()

        # Validate
        missing = [r for r in required if not values.get(r)]
        if missing:
            self._show_capability_detail(cap, missing, values)
            return

        self._run_capability(cap_id, values)

    def _split_kv_tokens(self, s: str) -> List[str]:
        """Split a string on whitespace, honouring quoted substrings."""
        import shlex
        try:
            return shlex.split(s)
        except ValueError:
            return s.split()

    def _open_capability_palette(self) -> None:
        """Open a palette to browse capabilities, grouped by category."""
        caps = self.bridge.list_capabilities()
        if not caps:
            self.query_one(ChatLog).add_system(
                "[b]Capability Catalog[/b]\n  No capabilities available."
            )
            self.query_one(InputBox).focus()
            return
        # Group by category for the palette
        options: List[Dict[str, Any]] = []
        for c in caps:
            builtin_tag = " [dim](builtin)[/dim]" if c.get("builtin") else ""
            options.append({
                "id": c["id"],
                "label": f"[{c.get('category', '?')}] {c.get('name', c['id'])}{builtin_tag}",
                "desc": c.get("description", "")[:120],
            })
        self._open_sub_palette("capability", options)

    def _show_capability_detail(
        self,
        cap: Dict[str, Any],
        missing: List[str],
        values: Dict[str, str],
    ) -> None:
        """Show a capability's body + placeholders and prompt for the missing ones."""
        chat = self.query_one(ChatLog)
        lines = [
            f"[b]Capability: {cap.get('name', cap.get('id'))}[/b]",
            f"  Category: {cap.get('category', '?')}",
            f"  {cap.get('description', '')}",
            "",
            "[b]Placeholders[/b]",
        ]
        for p in cap.get("placeholders", []):
            req = "required" if p.get("required", True) else "optional"
            default = p.get("default", "")
            default_str = f", default: {default}" if default else ""
            cur = values.get(p["name"], "")
            cur_str = f"  [green]= {cur}[/green]" if cur else f"  [red]missing ({req}{default_str})[/red]"
            lines.append(f"  ${p['name']}$ — {p.get('description', '')}{cur_str}")
        if missing:
            lines.append("")
            lines.append(
                f"[yellow]Fill the missing placeholders and re-run:[/yellow]\n"
                f"  /capabilities {cap['id']} " +
                " ".join(f"{m}=..." for m in missing)
            )
        chat.add_system("\n".join(lines))
        self.query_one(InputBox).focus()

    def _run_capability(self, cap_id: str, values: Dict[str, str]) -> None:
        """Fill the template and run the resulting prompt as a normal turn."""
        result = self.bridge.fill_capability_template(cap_id, values)
        chat = self.query_one(ChatLog)
        if not result.get("ok"):
            chat.add_error(
                f"Failed to fill capability template: {result.get('error', 'unknown')}"
            )
            self.query_one(InputBox).focus()
            return
        prompt = result.get("prompt", "")
        cap_meta = result.get("capability", {})
        chat.add_user(
            f"[capability:{cap_id}] {cap_meta.get('name', cap_id)}\n"
            f"[dim](filled template — placeholders: {dict(values) or 'none'})[/dim]"
        )
        self._last_prompt = prompt
        self._running = True
        self._refresh_status("thinking")
        self._run_turn(prompt)

    # ── v2.0.1 (M1) — Second Opinion ──────────────────────────────

    def _exec_second_opinion(self, arg: str) -> None:
        """Configure or inspect the Second Opinion feature.

        Usage:
            /second_opinion                       — show current state
            /second_opinion on|off                — enable / disable
            /second_opinion pro on|off            — toggle Clew Pro flag
            /second_opinion provider <pid> [model]— pick the second model
            /second_opinion risk low|medium|high  — min risk to trigger
        """
        chat = self.query_one(ChatLog)
        arg = arg.strip()
        if not arg:
            cfg = self.bridge.get_second_opinion_config()
            pro = "ON" if cfg.get("pro_enabled") else "OFF"
            so = "ON" if cfg.get("enabled") else "OFF"
            chat.add_system(
                f"[b]Second Opinion[/b] (Clew Pro feature)\n"
                f"  Clew Pro:        [b]{pro}[/b]\n"
                f"  Second Opinion:  [b]{so}[/b]\n"
                f"  Second provider: {cfg.get('provider_id', 'auto')}\n"
                f"  Second model:    {cfg.get('model', 'auto')}\n"
                f"  Min risk level:  {cfg.get('min_risk_level', 'medium')}\n\n"
                f"  Usage:\n"
                f"    /second_opinion on|off\n"
                f"    /second_opinion pro on|off\n"
                f"    /second_opinion provider <pid> [model]\n"
                f"    /second_opinion risk low|medium|high"
            )
            self.query_one(InputBox).focus()
            return

        parts = arg.split()
        sub = parts[0].lower()
        if sub in ("on", "off", "enable", "disable"):
            enabled = sub in ("on", "enable")
            r = self.bridge.set_second_opinion_config(enabled=enabled)
            if r.get("ok"):
                state = "ON" if r.get("enabled") else "OFF"
                chat.add_system(f"Second Opinion: [b]{state}[/b]")
                if not r.get("pro_enabled"):
                    chat.add_system(
                        "[yellow]Note:[/yellow] Clew Pro is OFF. "
                        "Enable with /second_opinion pro on"
                    )
            else:
                chat.add_error(f"Failed: {r.get('error', 'unknown')}")
        elif sub == "pro":
            if len(parts) < 2:
                chat.add_system("Usage: /second_opinion pro on|off")
            else:
                v = parts[1].lower() in ("on", "1", "true", "yes")
                r = self.bridge.set_pro_enabled(v)
                if r.get("ok"):
                    state = "ON" if r.get("pro") else "OFF"
                    chat.add_system(f"Clew Pro: [b]{state}[/b]")
                else:
                    chat.add_error(f"Failed: {r.get('error', 'unknown')}")
        elif sub == "provider":
            if len(parts) < 2:
                # Show available providers
                providers = self.bridge.list_second_opinion_providers()
                chat.add_system(
                    "[b]Available second-opinion providers[/b]\n" +
                    "\n".join(
                        f"  {p.get('id')} — {p.get('label', '')} "
                        f"(model: {p.get('model', p.get('default_model', '?'))})"
                        for p in providers[:20]
                    )
                )
            else:
                pid = parts[1]
                model = parts[2] if len(parts) > 2 else "auto"
                r = self.bridge.set_second_opinion_config(provider_id=pid, model=model)
                if r.get("ok"):
                    chat.add_system(
                        f"Second Opinion provider: [b]{r.get('provider_id')}[/b] "
                        f"model: [dim]{r.get('model')}[/dim]"
                    )
                else:
                    chat.add_error(f"Failed: {r.get('error', 'unknown')}")
        elif sub in ("risk", "min_risk", "threshold"):
            if len(parts) < 2:
                chat.add_system("Usage: /second_opinion risk low|medium|high")
            else:
                lvl = parts[1].lower()
                if lvl not in ("low", "medium", "high"):
                    chat.add_error(f"Invalid risk level: {lvl}")
                else:
                    r = self.bridge.set_second_opinion_config(min_risk_level=lvl)
                    if r.get("ok"):
                        chat.add_system(f"Second Opinion min risk: [b]{r.get('min_risk_level')}[/b]")
                    else:
                        chat.add_error(f"Failed: {r.get('error', 'unknown')}")
        else:
            chat.add_system(
                f"Unknown subcommand: {sub}\n"
                f"Usage: /second_opinion [on|off|pro|provider|risk] ..."
            )
        self.query_one(InputBox).focus()

    # ── v2.0.1 (G3) — Token budget ────────────────────────────────

    def _exec_budget(self, arg: str) -> None:
        """Configure or inspect the token budget / efficiency policy.

        Usage:
            /budget                — show current budget
            /budget daily <usd>    — set daily USD cap
            /budget monthly <usd>  — set monthly USD cap
            /budget per_turn <tok> — max tokens per agentic turn
            /budget compaction <pct> — auto-compact threshold (50-95)
            /budget reset          — reset to defaults
        """
        chat = self.query_one(ChatLog)
        arg = arg.strip()
        if not arg:
            b = self.bridge.get_token_budget()
            if not b.get("ok"):
                chat.add_error(f"Failed: {b.get('error', 'unknown')}")
                self.query_one(InputBox).focus()
                return
            chat.add_system(
                f"[b]Token Budget & Efficiency[/b]\n"
                f"  Daily cap:        ${b.get('daily_usd', 0):.2f}\n"
                f"  Monthly cap:      ${b.get('monthly_usd', 0):.2f}\n"
                f"  Max tokens/turn:  {b.get('max_tokens_per_turn', 0):,}\n"
                f"  Max iterations:   {b.get('max_iterations', 0)}\n"
                f"  Auto-compact at:  {b.get('compaction_threshold_pct', 85)}%\n"
                f"  Prompt caching:   {'ON' if b.get('prompt_caching') else 'OFF'}\n"
                f"  Predictable mode: {'ON' if b.get('predictable_mode') else 'OFF'}\n\n"
                f"  Usage this month: ${b.get('month_cost', 0):.4f} / "
                f"${b.get('monthly_usd', 0):.2f} "
                f"({b.get('month_used_pct', 0)}%)\n"
                f"  Today: ${b.get('day_cost', 0):.4f}\n\n"
                f"  Commands:\n"
                f"    /budget daily|monthly <usd>\n"
                f"    /budget per_turn <tokens>\n"
                f"    /budget iterations <n>\n"
                f"    /budget compaction <50-95>\n"
                f"    /budget caching on|off\n"
                f"    /budget predictable on|off\n"
                f"    /budget reset"
            )
            self.query_one(InputBox).focus()
            return

        parts = arg.split()
        sub = parts[0].lower()
        try:
            if sub == "daily":
                self.bridge.set_token_budget(daily_usd=float(parts[1]))
            elif sub == "monthly":
                self.bridge.set_token_budget(monthly_usd=float(parts[1]))
            elif sub in ("per_turn", "per-turn"):
                self.bridge.set_token_budget(max_tokens_per_turn=int(parts[1]))
            elif sub == "iterations":
                self.bridge.set_token_budget(max_iterations=int(parts[1]))
            elif sub == "compaction":
                pct = max(50, min(95, int(parts[1])))
                self.bridge.set_token_budget(compaction_threshold_pct=pct)
            elif sub == "caching":
                self.bridge.set_token_budget(
                    prompt_caching=(parts[1].lower() in ("on", "1", "true", "yes"))
                )
            elif sub == "predictable":
                self.bridge.set_token_budget(
                    predictable_mode=(parts[1].lower() in ("on", "1", "true", "yes"))
                )
            elif sub == "reset":
                self.bridge.reset_token_budget()
            else:
                chat.add_system(f"Unknown subcommand: {sub}")
                self.query_one(InputBox).focus()
                return
            chat.add_system(f"Budget updated: [b]{sub}[/b]")
        except (IndexError, ValueError) as e:
            chat.add_error(f"Bad argument: {e}")
        except Exception as e:
            chat.add_error(f"Failed: {e}")
        self.query_one(InputBox).focus()

    # ── v2.0.1 (G4) — Cross-model verification ────────────────────

    def _exec_verify(self, arg: str) -> None:
        """Verify the last assistant response with a different model.

        Usage:
            /verify                          — auto-pick a cross-family verifier
            /verify <provider_id>            — use a specific provider
            /verify <provider_id> <model>    — use a specific provider + model
        """
        if self._running:
            self.query_one(ChatLog).add_system(
                "Wait for the current turn to finish before running /verify."
            )
            self.query_one(InputBox).focus()
            return

        parts = arg.strip().split()
        v_pid = parts[0] if parts else None
        v_model = parts[1] if len(parts) > 1 else None

        chat = self.query_one(ChatLog)
        chat.add_system(
            "[dim]Running cross-model verification...[/dim]"
        )
        self._refresh_status("thinking")
        self._run_verification(v_pid, v_model)

    @work(thread=True, exclusive=True)
    def _run_verification(
        self,
        v_pid: Optional[str],
        v_model: Optional[str],
    ) -> None:
        try:
            result = self.bridge.verify_last_response(
                verifier_provider_id=v_pid,
                verifier_model=v_model,
            )
            self.call_from_thread(self._on_verify_done, result)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, str(e))

    def _on_verify_done(self, result: Dict[str, Any]) -> None:
        chat = self.query_one(ChatLog)
        self._refresh_status("idle")
        if not result.get("ok"):
            chat.add_error(
                f"Verification failed: {result.get('error', 'unknown')}"
            )
            self.query_one(InputBox).focus()
            return
        # Show the verification modal
        self.push_screen(VerificationModal(result), lambda _: self._after_verify_modal())

    def _after_verify_modal(self) -> None:
        """Called after the verification modal is dismissed."""
        self.query_one(InputBox).focus()

    # --------------------------------------------------------------- worker
    @work(thread=True, exclusive=True)
    def _run_turn(self, prompt: str) -> None:
        try:
            result = self.bridge.run_prompt(prompt)
            self.call_from_thread(self._on_turn_done, result)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, str(e))

    # ----------------------------------------------------------- agent events
    def _sink(self, kind: str, data: Dict[str, Any]) -> None:
        self.call_from_thread(self._handle_event, kind, data)

    def action_toggle_theme(self) -> None:
        """Toggle between light and dark themes."""
        self._dark_theme = not self._dark_theme
        theme = "dark" if self._dark_theme else "light"
        self.notify(f"Theme switched to {theme}")
        # Update CSS path to switch themes, then reload so the new
        # stylesheet actually applies. Without reload_css() Textual keeps
        # the previously-loaded stylesheet cached.
        if self._dark_theme:
            self.CSS_PATH = "styles_dark.tcss"
        else:
            self.CSS_PATH = "styles_light.tcss"
        try:
            self.reload_css()
        except Exception:
            pass

    def _handle_event(self, kind: str, data: Dict[str, Any]) -> None:
        chat = self.query_one(ChatLog)
        # v2.0.0: surface subagent events distinctly so the user can tell
        # which agent produced a thought / tool call / result.
        sub_label = data.get("parent_label") or data.get("subagent_label")
        if kind == "plan_created":
            chat.add_plan(str(data.get("plan", "")))
        elif kind == "thought":
            text = str(data.get("thought", ""))
            if sub_label:
                chat.add_thought(f"[subagent {sub_label}] {text}")
            else:
                chat.add_thought(text)
        elif kind == "token_delta":
            chat.append_token_delta(str(data.get("delta", "")))
        elif kind == "tool_called":
            self._refresh_status("running")
            tool = str(data.get("tool", "?"))
            args = data.get("args") or {}
            if sub_label:
                chat.add_tool_call(tool, args, sub_label=sub_label)
            else:
                chat.add_tool_call(tool, args)
        elif kind == "tool_result":
            chat.add_tool_result(str(data.get("tool", "?")), str(data.get("result", "")))
            self._refresh_status("thinking")
        elif kind == "iteration_start":
            self._refresh_status("thinking")
        elif kind == "iteration_end":
            # v2.0.0 fix: transition back to "thinking" between iterations
            # so the StatusBar doesn't get stuck on "tool running".
            self._refresh_status("thinking")
        elif kind == "error":
            chat.add_error(str(data.get("error", "unknown error")))
        elif kind == "done":
            pass

    def _confirm(self, info: Dict[str, Any]) -> None:
        self.call_from_thread(self._show_confirm, dict(info))

    def _show_confirm(self, info: Dict[str, Any]) -> None:
        def _answer(result: bool | str | None) -> None:
            # Guardian modal returns "approve" | "reject" | "use_fix"
            # Legacy modal returns True/False
            if isinstance(result, str):
                if result == "use_fix":
                    self.bridge.answer_guardian_verdict("use_fix")
                elif result == "approve":
                    self.bridge.answer_confirmation(True)
                elif result == "reject":
                    self.bridge.answer_confirmation(False)
                else:
                    self.bridge.answer_confirmation(False)
            elif isinstance(result, bool):
                self.bridge.answer_confirmation(result)
            else:
                self.bridge.answer_confirmation(False)

        # Check if this is a Guardian review event
        if info.get("guardian_verdict") == "MODIFY" or info.get("suggested_args") is not None:
            self.push_screen(GuardianModal(info), _answer)
        else:
            self.push_screen(ApprovalModal(info), _answer)

    # --------------------------------------------------------------- lifecycle
    def _on_turn_done(self, result: Any) -> None:
        chat = self.query_one(ChatLog)
        was_streaming = chat._streaming_active
        if was_streaming:
            chat.end_streaming()

        error = getattr(result, "error", None)
        metadata = getattr(result, "metadata", {})
        if error == "awaiting_plan_approval":
            plan_text = metadata.get("plan", "")
            chat.add_plan(plan_text)
            self._show_plan_approval(plan_text)
            self._running = False
            self._refresh_status("idle")
            return

        output = getattr(result, "output", "") or ""
        success = getattr(result, "success", True)
        if success:
            if not was_streaming:
                chat.add_final(output)
        else:
            err = getattr(result, "error", None) or output or "task failed"
            chat.add_error(str(err))
        self._running = False
        self._refresh_status("idle")

    def _show_plan_approval(self, plan_text: str) -> None:
        info = {
            "action": "Execute plan",
            "summary": plan_text[:500] + ("..." if len(plan_text) > 500 else ""),
        }

        def _answer(accepted: bool | None) -> None:
            if accepted:
                self._run_turn_with_plan_approval()
            else:
                self.query_one(ChatLog).add_system(
                    "Plan rejected. Type new instructions or feedback."
                )
                self.query_one(InputBox).focus()

        self.push_screen(ApprovalModal(info), _answer)

    @work(thread=True, exclusive=True)
    def _run_turn_with_plan_approval(self) -> None:
        try:
            result = self.bridge.run_prompt(
                self._last_prompt, plan_approved=True
            )
            self.call_from_thread(self._on_turn_done, result)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, str(e))

    def _on_turn_error(self, message: str) -> None:
        chat = self.query_one(ChatLog)
        if chat._streaming_active:
            chat.end_streaming()
        chat.add_error(message)
        self._running = False
        self._refresh_status("idle")

    def _refresh_status(self, state: str) -> None:
        guardian_level = "off"
        try:
            guardian_level = self.bridge.get_guardian_level().get("level", "off")
        except Exception:
            pass
        self.query_one(StatusBar).update_status(
            self.bridge.status(), state=state, section=self.bridge.section,
            guardian=guardian_level,
        )

    # ------------------------------------------------------------------ actions
    def action_interrupt(self) -> None:
        if self._running:
            self.bridge.request_stop()
            self.query_one(ChatLog).add_system("interrupt requested...")
        else:
            self.query_one(ChatLog).add_system("(nothing running - Ctrl+D to quit)")

    def action_launch_gui(self) -> None:
        import subprocess
        import sys
        try:
            subprocess.Popen(
                [sys.executable, "-m", "clew", "--project", self.bridge.workspace]
            )
            self.query_one(ChatLog).add_system("launching GUI...")
            config_path = os.path.expanduser("~/.clew/config.json")
            close_on_switch = False
            try:
                import json
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                close_on_switch = bool(cfg.get("close_on_switch", False))
            except Exception:
                pass
            if close_on_switch:
                self.exit()
        except Exception as e:
            self.query_one(ChatLog).add_error(f"Failed to launch GUI: {e}")

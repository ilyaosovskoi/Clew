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
        chat = self.query_one(ChatLog)
        chat.add_system(
            f"clew TUI | workspace: {self.bridge.workspace}\n"
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
            "  [cyan]/gui[/cyan]       Launch the Clew GUI window",
            "  [cyan]/help[/cyan]      Show this help",
            "",
            "Type / to see inline suggestions, Ctrl+P for full command palette.",
            "Ctrl+C=interrupt | Ctrl+D=quit | Ctrl+G=GUI | Ctrl+P=commands",
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
        # Update CSS path to switch themes
        if self._dark_theme:
            self.CSS_PATH = "styles_dark.tcss"
        else:
            self.CSS_PATH = "styles_light.tcss"

    def _handle_event(self, kind: str, data: Dict[str, Any]) -> None:
        chat = self.query_one(ChatLog)
        if kind == "plan_created":
            chat.add_plan(str(data.get("plan", "")))
        elif kind == "thought":
            chat.add_thought(str(data.get("thought", "")))
        elif kind == "token_delta":
            chat.append_token_delta(str(data.get("delta", "")))
        elif kind == "tool_called":
            self._refresh_status("running")
            chat.add_tool_call(str(data.get("tool", "?")), data.get("args") or {})
        elif kind == "tool_result":
            chat.add_tool_result(str(data.get("tool", "?")), str(data.get("result", "")))
            self._refresh_status("thinking")
        elif kind == "iteration_start":
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
        self.query_one(StatusBar).update_status(
            self.bridge.status(), state=state, section=self.bridge.section
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

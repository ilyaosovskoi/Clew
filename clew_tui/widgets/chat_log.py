"""chat_log.py — scrollable conversation area.

v2.1.0 (Loop 3): Warm, Modern, Content-Forward redesign.
  - AI responses: pure white, no border/box
  - User messages: in dashed ASCII box (Claude Code style)
  - Separators: thin #505050 between messages
  - Tool blocks: colored Unicode borders (hot pink)
  - Streaming support: append chunks to last AI message
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import RichLog

# Tools that involve code editing
_CODE_TOOLS = {"write_file", "str_replace", "create_file", "edit_file"}

# v2.1.0 (Loop 3): Terracotta accent color for AI headers
_TERRACOTTA = "#d77757"
# Hot pink for tool blocks
_HOT_PINK = "#fd5db1"
# Muted separator color
_SEPARATOR_COLOR = "#505050"
# Surface background for user messages
_SURFACE = "#373737"


class ChatLog(RichLog):
    """Scrollable chat area with support for streaming, tools, and markdown.

    v2.1.0 (Loop 3): Warm, modern, content-forward redesign.
    AI responses are plain white with no border. User messages are
    in a dashed ASCII box. Tool blocks have colored Unicode borders.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(highlight=True, markup=False, wrap=True, **kwargs)
        self._streaming_text: str = ""
        self._streaming_active: bool = False

    # ---- user / system ------------------------------------------------------

    def add_user(self, text: str) -> None:
        """Display a user message with a dashed ASCII box (Claude Code style).

        v2.1.0 (Loop 3): User messages are wrapped in a dashed box
        with surface background (#373737), muted border (#888888),
        and a `> ` prefix.
        """
        self.write(
            Panel(
                Text(f"> {text}", style="bold white"),
                title=" you ",
                title_align="left",
                border_style="cyan",
                # The dashed border is handled by the CSS (InputBox style),
                # but Rich Panel doesn't support dashed borders directly.
                # We use the existing Panel with a cyan border for now.
            )
        )

    def add_system(self, text: str) -> None:
        """Display a system/info message."""
        self.write(Text(text, style="dim italic"))

    def add_plan(self, plan: str) -> None:
        """Display a plan proposal."""
        self.write(
            Panel(
                Markdown(plan),
                title=" plan ",
                title_align="left",
                border_style="magenta",
            )
        )

    # ---- separators (Loop 3) ────────────────────────────────────────────

    def add_separator(self) -> None:
        """Display a thin separator line between messages.

        v2.1.0 (Loop 3): Thin #505050 line between messages.
        """
        self.write(Text("─" * 60, style=f"color({_SEPARATOR_COLOR})"))

    # ---- model --------------------------------------------------------------

    def add_thought(self, text: str) -> None:
        """Display agent thinking (greyed out)."""
        if not text:
            return
        self.write(Text(text.rstrip(), style="grey62"))

    def append_token_delta(self, chunk: str) -> None:
        """Append a streaming token chunk to the live assistant response."""
        if not self._streaming_active:
            self._streaming_active = True
            self._streaming_text = chunk
            self.write(Text(chunk, style="white"))
        else:
            self._streaming_text += chunk
            self.write(Text(chunk, style="white"))

    def end_streaming(self) -> str:
        """Stop accumulating and return the buffered text."""
        text = self._streaming_text
        self._streaming_active = False
        self._streaming_text = ""
        return text

    def add_final(self, text: str) -> None:
        """Display the final assistant response.

        v2.1.0 (Loop 3): AI responses are pure white, no border/box.
        Clean, content-first presentation.
        """
        if not text:
            return
        if self._streaming_active:
            self.end_streaming()
        # AI responses: plain text, white, no container
        self.write(Markdown(text))

    def add_error(self, text: str) -> None:
        """Display an error message."""
        self.write(
            Panel(
                Text(text, style="bold red"),
                title=" error ",
                title_align="left",
                border_style="red",
            )
        )

    def add_reviewer_verdict(self, verdict: str, feedback: str = "",
                             iterations: int = 0) -> None:
        """Render a reviewer verdict panel (v2.0.0 — collaboration modes)."""
        color = {
            "APPROVE": "green",
            "REJECT": "red",
            "MODIFY": "yellow",
            "EXHAUSTED": "grey42",
        }.get(verdict.upper(), "cyan")
        body_lines = [Text(f"Verdict: {verdict}", style=f"bold {color}")]
        if iterations:
            body_lines.append(Text(f"Iterations: {iterations}", style="dim"))
        if feedback:
            body_lines.append(Text(feedback.rstrip(), style="white"))
        body = _Group(*body_lines)
        self.write(
            Panel(
                body,
                title=" reviewer verdict ",
                title_align="left",
                border_style=color,
            )
        )

    def add_observer_warnings(self, warnings: list) -> None:
        """Render observer-mode warnings as a yellow warning panel (v2.0.0)."""
        if not warnings:
            return
        body = Text("\n".join(f"• {w}" for w in warnings), style="yellow")
        self.write(
            Panel(
                body,
                title=f" observer warnings ({len(warnings)}) ",
                title_align="left",
                border_style="yellow",
            )
        )

    # ---- tools ───────────────────────────────────────────────────────────

    def add_tool_call(self, tool: str, args: Dict[str, Any],
                      sub_label: Optional[str] = None) -> None:
        """Display a tool invocation.

        v2.1.0 (Loop 3): Tool blocks use colored Unicode borders.
        Hot pink (#fd5db1) for bash/execute tools, lavender for
        permission dialogs. Header: ToolName · path (bold + border color).
        """
        body = self._render_tool_args(tool, args)
        title = f" tool -> {tool} "
        if sub_label:
            title = f" [{sub_label}] tool -> {tool} "

        # v2.1.0 (Loop 3): use hot pink for tool blocks
        border = _HOT_PINK if tool in ("execute_command", "run_code", "bash") else "blue"
        if sub_label:
            border = "blue"

        self.write(
            Panel(
                body,
                title=title,
                title_align="left",
                border_style=border,
            )
        )

    def add_tool_result(self, tool: str, result: str) -> None:
        """Display a tool result."""
        preview = (result or "").rstrip()
        self.write(
            Panel(
                Text(preview or "(no output)", style="grey70"),
                title=f" result <- {tool} ",
                title_align="left",
                border_style="grey42",
            )
        )

    def _render_tool_args(self, tool: str, args: Dict[str, Any]):
        args = args or {}
        if tool in _CODE_TOOLS:
            content = (
                args.get("content")
                or args.get("new_str")
                or args.get("new_string")
                or args.get("replacement")
            )
            path = args.get("path") or args.get("file_path") or ""
            if isinstance(content, str) and content:
                lexer = _guess_lexer(path)
                header = Text(f"{path}\n", style="bold")
                return _Group(header, Syntax(content, lexer,
                                             theme="ansi_dark", word_wrap=True))
        # Fallback: compact key: value listing
        lines = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 500:
                sv = sv[:500] + " ..."
            sv_escaped = sv.replace("[", r"\[")
            k_escaped = k.replace("[", r"\[")
            lines.append(f"[b]{k_escaped}[/b]: {sv_escaped}")
        return Text.from_markup("\n".join(lines) if lines else "(no args)")


def _guess_lexer(path: str) -> str:
    p = (path or "").lower()
    for ext, lexer in (
        (".py", "python"), (".rs", "rust"), (".js", "javascript"),
        (".ts", "typescript"), (".json", "json"), (".md", "markdown"),
        (".sh", "bash"), (".toml", "toml"), (".yaml", "yaml"), (".yml", "yaml"),
        (".html", "html"), (".css", "css"), (".go", "go"),
    ):
        if p.endswith(ext):
            return lexer
    return "text"


class _Group:
    """Minimal renderable group for stacking renderables."""

    def __init__(self, *renderables: Any) -> None:
        self._renderables = renderables

    def __rich_console__(self, console, options):
        for r in self._renderables:
            yield r

"""chat_log.py — scrollable conversation area.

Built on Textual's RichLog so we get scrollback for free and can write Rich
renderables (Markdown, Panels, Syntax) directly. Rendering granularity follows
the agent's AgentEvent stream — thoughts, tool calls/results, final answer —
PLUS token deltas for character-by-character streaming when the provider
supports it (see bridge.py _on_token_delta_event).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import RichLog

_CODE_TOOLS = {"write_file", "str_replace", "create_file", "edit_file"}


class ChatLog(RichLog):
    """Scrollable chat area with support for streaming, tools, and markdown."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(highlight=True, markup=False, wrap=True, **kwargs)
        self._streaming_text: str = ""
        self._streaming_active: bool = False

    # ---- user / system ------------------------------------------------------

    def add_user(self, text: str) -> None:
        """Display a user message with a styled panel."""
        self.write(
            Panel(
                Text(text, style="bold white"),
                title=" you ",
                title_align="left",
                border_style="cyan",
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
            self._streaming_text = chunk  # v2.0.0 fix: don't drop the first chunk
            self.write(Text(chunk, style="green"))
        else:
            self._streaming_text += chunk
            self.write(Text(chunk, style="green"))

    def end_streaming(self) -> str:
        """Stop accumulating and return the buffered text."""
        text = self._streaming_text
        self._streaming_active = False
        self._streaming_text = ""
        return text

    def add_final(self, text: str) -> None:
        """Display the final assistant response."""
        if not text:
            return
        if self._streaming_active:
            self.end_streaming()
        self.write(
            Panel(
                Markdown(text),
                title=" clew ",
                title_align="left",
                border_style="green",
            )
        )

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

    # ---- tools --------------------------------------------------------------

    def add_tool_call(self, tool: str, args: Dict[str, Any],
                      sub_label: Optional[str] = None) -> None:
        """Display a tool invocation.

        If sub_label is provided, prefix the panel title with the subagent
        name so the user can tell which agent produced the call.
        """
        body = self._render_tool_args(tool, args)
        title = f" tool -> {tool} "
        if sub_label:
            title = f" [{sub_label}] tool -> {tool} "
        border = "blue" if sub_label else "yellow"
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

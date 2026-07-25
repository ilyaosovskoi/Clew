"""
Clew Headless CLI — v1.2.1-fix (review §4.7).

A standalone command-line entry point that runs the AgentRuntime
WITHOUT the PySide6 / Qt GUI. Lets users kick off long-running
Heavy Code / Office tasks from a terminal, a cron job, or a CI
script — the same way Claude Code's headless mode or Codex CLI's
cloud tasks work.

Usage:
    python -m clew.cli run "refactor auth.py to use dataclasses"
    python -m clew.cli chat "explain what main.py does"
    python -m clew.cli heavy-code "refactor these 4 files in parallel: ..."
    python -m clew.cli office "create a quarterly report from data.xlsx"
    python -m clew.cli --provider groq --model llama-3.3-70b-versatile run "..."
    python -m clew.cli --allow docker --allow cargo run "build the project"
    python -m clew.cli --autonomy never_ask run "..."   # CI / cron use

Architecture:
  - Builds a ProviderRegistry + ProviderConfig from ~/.clew/config.json
    (same config the GUI uses) — no separate CLI config.
  - Builds an AgentRuntime with NO Qt dependency. The runtime already
    supports headless mode (its diff-review / confirm callbacks
    fail-open when no UI is wired — see _write_file / _request_confirmation).
  - Streams agent events (THOUGHT, TOOL_CALLED, TOOL_RESULT) to stdout
    so the user can watch progress in real time.
  - Returns the final answer as plain text on stdout; logs go to stderr.

This is intentionally MINIMAL — it does NOT replicate the GUI's chat
persistence, project tree, or diff-review pane. For interactive use
with diff review, use the GUI. For automation (cron, CI, scripts),
use this CLI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── Config loading (mirrors api_server._load_config but minimal) ─────────

def _clew_home() -> Path:
    p = Path.home() / ".clew"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _config_path() -> Path:
    return _clew_home() / "config.json"


def _load_config() -> Dict[str, Any]:
    """Load ~/.clew/config.json (same file the GUI uses).

    Returns a minimal default if the file doesn't exist yet — the CLI
    can run without ever launching the GUI, as long as the user sets
    provider credentials via environment variables.
    """
    path = _config_path()
    if not path.exists():
        return {
            "version": 2,
            "active_provider": os.environ.get("CLEW_PROVIDER", "groq"),
            "providers": {},
            "project_root": os.getcwd(),
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[cli] failed to load config: %s — using defaults", e)
        return {"active_provider": "groq", "providers": {}, "project_root": os.getcwd()}


# ── Registry bootstrap ───────────────────────────────────────────────────

def _build_registry(args: argparse.Namespace) -> "ProviderRegistry":
    """Build a ProviderRegistry from the user's config + CLI flags.

    The --provider / --model / --api-key flags override the config
    file's values, so the user can experiment without editing
    ~/.clew/config.json.
    """
    # Local import so `python -m clew.cli --help` works even if some
    # provider deps are missing.
    from .providers import ProviderRegistry, ProviderConfig, get_registry

    cfg = _load_config()
    registry = get_registry()
    # Ensure all built-in providers are registered.
    if not registry.list_providers():
        registry.register_default()

    # Determine the active provider.
    provider_id = args.provider or cfg.get("active_provider", "groq")
    if not registry.has_provider(provider_id):
        # Try to be helpful — list what's available.
        available = [p["id"] for p in registry.list_providers()]
        print(f"[cli] error: provider {provider_id!r} not registered. "
              f"Available: {', '.join(available)}", file=sys.stderr)
        sys.exit(2)

    # Look up the configured provider's settings (or defaults).
    provider_cfg = cfg.get("providers", {}).get(provider_id, {})
    model = args.model or provider_cfg.get("model", "")
    api_key = args.api_key or provider_cfg.get("api_key", "") or os.environ.get(f"{provider_id.upper()}_API_KEY", "")
    api_base = args.api_base or provider_cfg.get("api_base", "")
    temperature = float(args.temperature if args.temperature is not None else provider_cfg.get("temperature", 0.2))
    max_tokens = int(args.max_tokens if args.max_tokens is not None else provider_cfg.get("max_tokens", 4096))

    # Build + register the ProviderConfig.
    extra: Dict[str, Any] = {}
    if args.context_window:
        extra["context_window"] = int(args.context_window)
    config = ProviderConfig(
        provider_id=provider_id,
        model=model,
        api_key=api_key or None,
        api_base=api_base or None,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
    )
    registry.configure(provider_id, config)
    registry.set_active(provider_id)
    return registry


# ── Event streaming ──────────────────────────────────────────────────────

def _stream_events(event, data):
    """Print agent events to stderr as they happen, so the user can
    watch progress in real time without polluting stdout (which is
    reserved for the final answer)."""
    try:
        from .agent_runtime import AgentEvent
        if event == AgentEvent.ITERATION_START:
            print(f"[iter {data.get('iteration')}/{data.get('max')}]", file=sys.stderr)
        elif event == AgentEvent.THOUGHT:
            thought = data.get("thought", "")
            if thought:
                # Truncate long thoughts so the terminal stays readable.
                preview = thought[:200].replace("\n", " ")
                print(f"  | {preview}", file=sys.stderr)
        elif event == AgentEvent.TOOL_CALLED:
            tool = data.get("tool", "?")
            args_preview = json.dumps(data.get("args", {}), default=str)[:120]
            print(f"  → {tool}({args_preview})", file=sys.stderr)
        elif event == AgentEvent.TOOL_RESULT:
            result = str(data.get("result", ""))[:200].replace("\n", " ")
            print(f"  ← {result}", file=sys.stderr)
        elif event == AgentEvent.ERROR:
            print(f"  [ERROR] {data.get('error', '')}", file=sys.stderr)
        elif event == AgentEvent.DONE:
            # Final answer goes to STDOUT (so it can be piped).
            print("", file=sys.stderr)  # blank line separator
    except Exception as e:
        logger.debug("[cli] event stream error: %s", e)


# ── Command handlers ─────────────────────────────────────────────────────

def _setup_autonomy(args, agent):
    """Apply --autonomy / --allow / --deny flags to the agent."""
    if args.autonomy:
        agent.set_autonomy(args.autonomy)
    else:
        # Default for CLI: never_ask (CI / cron use). The user can
        # override with --autonomy always_ask for interactive review.
        agent.set_autonomy("never_ask")
    # Apply --allow / --deny to the CommandPolicy.
    if args.allow or args.deny:
        try:
            from .command_policy import CommandPolicy, set_global_policy, get_global_policy
            base = get_global_policy(args.workspace)
            extra_allowed = set(args.allow) if args.allow else set()
            extra_denied = set(args.deny) if args.deny else set()
            new_policy = CommandPolicy(
                allowed=base.allowed | extra_allowed,
                dangerous_flags=dict(base.dangerous_flags),
                denied=base.denied | extra_denied,
                sources={**base.sources, "cli_flags": {
                    "extra_allowed": sorted(extra_allowed),
                    "extra_denied": sorted(extra_denied),
                }},
                pending_grants=dict(base.pending_grants),
            )
            set_global_policy(new_policy)
        except Exception as e:
            logger.warning("[cli] failed to apply --allow/--deny: %s", e)


def _warn_on_pending_grants(workspace: str) -> None:
    """v1.2.2-fix (review §4.6): print a visible stderr warning if the
    workspace's <project>/.clew/commands.json requests capabilities
    that haven't been approved. This matters most for the CLI because
    its default autonomy is ``never_ask`` — there's no interactive
    prompt anywhere else that would ever surface this to a human, so
    an operator running this unattended (CI / cron) could otherwise
    have no idea a repository tried to widen its own sandbox.
    """
    try:
        from .command_policy import get_global_policy, describe_pending_grants
        policy = get_global_policy(workspace)
        if policy.has_pending_grants():
            print("=" * 70, file=sys.stderr)
            print("[cli] WARNING: unapproved project command-policy grants:", file=sys.stderr)
            print(describe_pending_grants(policy), file=sys.stderr)
            print("=" * 70, file=sys.stderr)
    except Exception as e:
        logger.debug("[cli] pending-grants check failed: %s", e)


def _run_task(args: argparse.Namespace) -> int:
    """Run a single agent task and print the result."""
    from .agent_runtime import AgentRuntime, TaskType

    registry = _build_registry(args)
    workspace = args.workspace or os.getcwd()
    _warn_on_pending_grants(workspace)
    # Map command name → section + task type.
    section = "general"
    task_type = TaskType.AGENTIC
    if args.command == "heavy-code":
        section = "heavy_code"
    elif args.command == "office":
        section = "office"
    elif args.command == "chat":
        task_type = TaskType.CHAT

    # max_iterations: defaults are 8 (general), 30 (heavy_code), 8 (office).
    max_iter = args.max_iterations or (30 if section == "heavy_code" else 8)

    agent = AgentRuntime(
        registry=registry,
        workspace=workspace,
        max_iterations=max_iter,
        enable_planning=not args.no_plan,
        on_event=_stream_events,
        section=section,
    )
    _setup_autonomy(args, agent)

    # Dispatch to the right runtime method.
    if args.command == "chat":
        result = agent.chat(args.prompt)
    else:
        result = agent.run(args.prompt, task_type=task_type)

    # Print the final answer to stdout (so it can be piped / captured).
    if result.success:
        print(result.output)
        return 0
    else:
        # Errors go to stderr; stdout stays clean.
        print(f"[cli] task failed: {result.error or 'unknown error'}", file=sys.stderr)
        if result.output:
            print(result.output, file=sys.stderr)
        return 1


def _show_status(args: argparse.Namespace) -> int:
    """Print the current configuration + provider status."""
    cfg = _load_config()
    print("Clew CLI status")
    print(f"  config:        {_config_path()}")
    print(f"  active provider: {cfg.get('active_provider', '(none)')}")
    print(f"  workspace:     {args.workspace or os.getcwd()}")
    # Show command policy (resolved).
    try:
        from .command_policy import get_global_policy
        policy = get_global_policy(args.workspace)
        print(f"  allowed cmds:  {', '.join(sorted(policy.allowed))}")
        if policy.denied:
            print(f"  denied cmds:   {', '.join(sorted(policy.denied))}")
        if policy.has_pending_grants():
            from .command_policy import describe_pending_grants
            print(f"  PENDING (unapproved) grants:")
            for line in describe_pending_grants(policy).splitlines():
                print(f"    {line}")
    except Exception as e:
        print(f"  command policy: <unavailable: {e}>")
    return 0


def _approve_project(args: argparse.Namespace) -> int:
    """v1.2.2-fix (review §4.6): explicitly approve the CURRENT content
    of <workspace>/.clew/commands.json. Prints what's about to be
    granted and requires the file to actually request something —
    running this against a project with no pending grants is a no-op.
    """
    from .command_policy import (
        _project_commands_path, _read_commands_json, _grant_relevant_subset,
        approve_project_policy,
    )
    workspace = args.workspace or os.getcwd()
    proj_path = _project_commands_path(workspace)
    if proj_path is None or not proj_path.exists():
        print(f"[cli] no {workspace}/.clew/commands.json found — nothing to approve.",
              file=sys.stderr)
        return 1
    cfg = _read_commands_json(proj_path)
    subset = _grant_relevant_subset(cfg)
    if not subset:
        print("[cli] this project's commands.json requests nothing beyond "
              "base/user config — nothing to approve.")
        return 0
    print(f"About to approve for {workspace}:")
    if subset.get("extra_allowed"):
        print(f"  - allow additional commands: {', '.join(subset['extra_allowed'])}")
    if subset.get("extra_trusted_flags"):
        for binary, flags in subset["extra_trusted_flags"].items():
            print(f"  - trust flags for {binary!r}: {', '.join(flags)}")
    if not args.yes:
        try:
            reply = input("Approve? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("[cli] not approved.")
            return 1
    ok = approve_project_policy(workspace)
    if ok:
        print(f"[cli] approved. Re-run `status` to confirm.")
        return 0
    print("[cli] nothing to approve (race with a concurrent edit?).", file=sys.stderr)
    return 1


def _revoke_project(args: argparse.Namespace) -> int:
    from .command_policy import revoke_project_policy_approval
    workspace = args.workspace or os.getcwd()
    if revoke_project_policy_approval(workspace):
        print(f"[cli] revoked prior approval for {workspace}.")
        return 0
    print(f"[cli] no prior approval found for {workspace}.", file=sys.stderr)
    return 1


def _write_sample_commands(args: argparse.Namespace) -> int:
    """Write a sample ~/.clew/commands.json for the user to customise."""
    try:
        from .command_policy import write_sample_config, _user_commands_path
        path = write_sample_config()
        print(f"Wrote sample commands config to: {path}")
        print("Edit it to extend the command whitelist, then re-run.")
        return 0
    except Exception as e:
        print(f"[cli] failed to write sample config: {e}", file=sys.stderr)
        return 1


# ── Argument parser ──────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m clew.cli",
        description=(
            "Clew headless agent CLI. Runs the AgentRuntime without "
            "the Qt GUI — for automation, cron jobs, CI scripts, and "
            "long-running Heavy Code / Office tasks."
        ),
    )
    # Subcommands: run / chat / heavy-code / office / status / init-commands.
    sub = p.add_subparsers(dest="command", required=True)

    # Common flags shared by run / chat / heavy-code / office.
    def _add_common_flags(sp):
        sp.add_argument("prompt", help="The task description or chat message.")
        sp.add_argument("--workspace", default=None,
                        help="Project root (default: current directory).")
        sp.add_argument("--provider", default=None,
                        help="Override active provider (e.g. 'groq', 'openai', 'anthropic').")
        sp.add_argument("--model", default=None,
                        help="Override model name (e.g. 'llama-3.3-70b-versatile').")
        sp.add_argument("--api-key", default=None,
                        help="Override API key (default: from config or env var).")
        sp.add_argument("--api-base", default=None,
                        help="Override API base URL.")
        sp.add_argument("--temperature", type=float, default=None,
                        help="Override sampling temperature (default: 0.2).")
        sp.add_argument("--max-tokens", type=int, default=None,
                        help="Override max output tokens (default: 4096).")
        sp.add_argument("--context-window", type=int, default=None,
                        help="Override the provider's reported context window (rarely needed).")
        sp.add_argument("--max-iterations", type=int, default=None,
                        help="Override agent loop max iterations (default: 8 general, 30 heavy-code).")
        sp.add_argument("--no-plan", action="store_true",
                        help="Skip the planning step (faster, less structured).")
        sp.add_argument("--autonomy", choices=["always_ask", "new_files_only", "never_ask"],
                        default=None,
                        help="Autonomy level (default: never_ask for CLI / cron use).")
        sp.add_argument("--allow", action="append", default=[],
                        help="Allow an extra command (e.g. --allow docker). Repeatable.")
        sp.add_argument("--deny", action="append", default=[],
                        help="Deny a command (overrides allow). Repeatable.")
        sp.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging on stderr.")

    _add_common_flags(sub.add_parser("run", help="Run a General-section agent task."))
    _add_common_flags(sub.add_parser("chat", help="Single-turn chat (no tools, no planning)."))
    _add_common_flags(sub.add_parser("heavy-code", help="Heavy Code section (multi-agent refactors)."))
    _add_common_flags(sub.add_parser("office", help="Office Worker section (.docx/.xlsx/.pptx)."))

    # Status subcommand.
    st = sub.add_parser("status", help="Show current config + provider + command policy.")
    st.add_argument("--workspace", default=None, help="Project root to resolve policy for.")

    # init-commands subcommand.
    sub.add_parser("init-commands",
                   help="Write a sample ~/.clew/commands.json for customising the whitelist.")

    # v1.2.2-fix (review §4.6): approve-project / revoke-project —
    # the human-in-the-loop gate for <project>/.clew/commands.json
    # capability expansions (extra_allowed / extra_trusted_flags).
    ap = sub.add_parser("approve-project",
                        help="Approve the current <workspace>/.clew/commands.json "
                             "capability requests (required before they take effect).")
    ap.add_argument("--workspace", default=None, help="Project root (default: cwd).")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip the interactive confirmation prompt (non-interactive use).")

    rp = sub.add_parser("revoke-project",
                        help="Revoke a previously approved <workspace>/.clew/commands.json.")
    rp.add_argument("--workspace", default=None, help="Project root (default: cwd).")

    return p


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Configure logging — INFO to stderr by default, DEBUG with -v.
    level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "status":
        return _show_status(args)
    if args.command == "init-commands":
        return _write_sample_commands(args)
    if args.command == "approve-project":
        return _approve_project(args)
    if args.command == "revoke-project":
        return _revoke_project(args)
    return _run_task(args)


if __name__ == "__main__":
    sys.exit(main())

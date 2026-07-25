"""guardian.py — LLM-based reviewer for risky tool calls (APPROVE/REJECT/MODIFY).

Rule-based risk scoring + optional LLM review with circuit breaker protection.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from clew.agent import CircuitBreakerRegistry, CircuitOpenError, get_circuit_breaker_registry
from clew.agent.native import NATIVE_AVAILABLE, get_native_module
from clew.providers import ProviderMessage, ProviderResponse
from clew.providers.base import Provider

logger = logging.getLogger(__name__)

# --- Config --------------------------------------------------------------


@dataclass(frozen=True)
class GuardianConfig:
    level: str = "off"  # "off" | "dangerous_only" | "all"
    provider_id: str = "auto"  # "auto" = use parent runtime's active provider
    model: str = "auto"  # "auto" = use parent runtime's active model


# --- Risk Scoring --------------------------------------------------------


@dataclass(frozen=True)
class RiskAssessment:
    level: str  # "low" | "medium" | "high"
    reasons: list[str]


CRITICAL_PATHS = [
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.aws"),
    os.path.expanduser("~/.config/gcloud"),
    os.path.expanduser("~/.docker"),
    os.path.expanduser("~/.kube"),
]

CRITICAL_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
}

DANGEROUS_COMMAND_PATTERNS = [
    (re.compile(r"\brm\s+-rf\b"), "rm -rf"),
    (re.compile(r"\bgit\s+push\s+--force\b"), "git push --force"),
    (re.compile(r"\bcurl\s+.*\|\s*sh\b"), "curl | sh"),
    (re.compile(r"\bwget\s+.*\|\s*sh\b"), "wget | sh"),
    (re.compile(r"\bchmod\s+777\b"), "chmod 777"),
    (re.compile(r">\s*/etc/"), "write to /etc"),
    (re.compile(r">\s*/usr/"), "write to /usr"),
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bdd\s+if="), "dd if="),
    (re.compile(r":\s*\(\)\s*{\s*:\|:\s*&\s*}\s*;\s*:"), "fork bomb"),
]


def assess_risk(
    tool_name: str,
    args: dict[str, Any],
    workspace: str,
    command_policy: Optional[Any] = None,
) -> RiskAssessment:
    """Rule-based risk assessment. Returns level and reasons."""
    reasons: list[str] = []
    level = "low"

    # execute_command / run_shell / bash
    if tool_name in ("execute_command", "run_shell", "bash", "shell"):
        cmd = args.get("command", "") or args.get("cmd", "") or str(args)
        # Check command_policy
        if command_policy:
            # Extract binary (first word)
            binary = cmd.strip().split()[0] if cmd.strip() else ""
            if binary and command_policy.is_dangerous_flag(binary, ""):
                reasons.append(f"command_policy: {binary} flagged as dangerous")
                level = "high"
        # Pattern matching
        for pattern, desc in DANGEROUS_COMMAND_PATTERNS:
            if pattern.search(cmd):
                reasons.append(f"dangerous pattern: {desc}")
                level = "high"
        # rm outside workspace
        if "rm " in cmd:
            # Heuristic: check for paths not under workspace
            pass  # Could expand

    # write_file / edit_file
    elif tool_name in ("write_file", "edit_file", "write", "edit"):
        path = args.get("path", "") or args.get("file_path", "")
        if path:
            # Check critical paths
            abs_path = os.path.abspath(os.path.join(workspace, path))
            for crit in CRITICAL_PATHS:
                try:
                    if abs_path.startswith(crit):
                        reasons.append(f"writes to critical path: {crit}")
                        level = "high"
                        break
                except Exception:
                    pass
            # Check critical filenames
            basename = os.path.basename(path)
            if basename in CRITICAL_FILENAMES:
                reasons.append(f"writes critical file: {basename}")
                level = "high"
            # Outside workspace
            ws_abs = os.path.abspath(workspace)
            if not abs_path.startswith(ws_abs):
                reasons.append(f"writes outside workspace: {path}")
                level = "high"

    # delete_file
    elif tool_name in ("delete_file", "delete", "remove"):
        path = args.get("path", "") or args.get("file_path", "")
        if path:
            abs_path = os.path.abspath(os.path.join(workspace, path))
            for crit in CRITICAL_PATHS:
                try:
                    if abs_path.startswith(crit):
                        reasons.append(f"deletes critical path: {crit}")
                        level = "high"
                        break
                except Exception:
                    pass
            basename = os.path.basename(path)
            if basename in CRITICAL_FILENAMES:
                reasons.append(f"deletes critical file: {basename}")
                level = "high"

    # git operations
    elif tool_name in ("git", "git_push", "git_commit"):
        subcmd = args.get("subcommand", "") or args.get("args", "")
        if "push" in str(subcmd) and "--force" in str(subcmd):
            reasons.append("git push --force")
            level = "high"

    return RiskAssessment(level=level, reasons=reasons)


# --- Guardian LLM Review -------------------------------------------------


@dataclass(frozen=True)
class GuardianVerdict:
    verdict: str  # "APPROVE" | "REJECT" | "MODIFY"
    rationale: str
    suggested_args: Optional[dict[str, Any]]


async def review_with_llm(
    *,
    config: GuardianConfig,
    tool_name: str,
    args: dict[str, Any],
    risk: RiskAssessment,
    recent_context: str,
    provider_registry,
    workspace: str,
) -> GuardianVerdict:
    """Call the LLM to review the tool call. Returns verdict."""
    # Load system prompt from template file
    template_path = os.path.join(os.path.dirname(__file__), "templates", "guardian.md")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except Exception as e:
        logger.warning("guardian: failed to load template %s: %s", template_path, e)
        system_prompt = "You are a safety reviewer. Return JSON: {verdict, rationale, suggested_args}."

    # Build user message
    user_data = {
        "tool": tool_name,
        "args": args,
        "risk_level": risk.level,
        "reasons": risk.reasons,
        "recent_context": recent_context,
    }
    user_prompt = json.dumps(user_data, ensure_ascii=False)

    # Get provider and model from config or registry
    provider_id = config.provider_id
    model = config.model

    # Resolve provider from registry
    if provider_id == "auto":
        # Use the active provider from the registry
        provider_id = provider_registry.active_id or "ollama"
    if model == "auto":
        # Get the default model for the active provider
        provider_obj = provider_registry.active
        model = provider_obj.get_model() if provider_obj else "llama3"

    provider = provider_registry.get(provider_id)
    if provider is None:
        return GuardianVerdict(
            verdict="APPROVE",
            rationale=f"Guardian: provider '{provider_id}' not found — defaulting to approve",
            suggested_args=None,
        )

    # Circuit breaker
    key = f"{provider_id}/{model}"
    from clew.agent import CircuitBreakerRegistry, get_circuit_breaker_registry
    breaker_registry = get_circuit_breaker_registry()
    breaker = breaker_registry.get(key)
    if not breaker.try_claim():
        logger.warning("guardian: circuit breaker open for %s", key)
        return GuardianVerdict(
            verdict="REJECT",
            rationale="Circuit breaker open — rate limited",
            suggested_args=None,
        )

    try:
        messages = [
            ProviderMessage(role="system", content=system_prompt),
            ProviderMessage(role="user", content=user_prompt),
        ]
        response: ProviderResponse = await provider.generate(messages, model=model)
        raw = response.text or ""

        # Parse JSON from response
        verdict = _parse_verdict(raw)
        if verdict is None:
            logger.warning("guardian: failed to parse verdict from LLM, defaulting to APPROVE")
            return GuardianVerdict(
                verdict="APPROVE",
                rationale="LLM response unparseable — defaulting to approve",
                suggested_args=None,
            )
        breaker.record(ok=True)
        return verdict

    except Exception as e:
        logger.exception("guardian: LLM call failed: %s", e)
        breaker.record(ok=False, rate_limited=_looks_like_rate_limit(e))
        # Default behavior on error
        return GuardianVerdict(
            verdict="APPROVE",
            rationale=f"LLM error — defaulting to approve: {e}",
            suggested_args=None,
        )


def _parse_verdict(raw: str) -> Optional[GuardianVerdict]:
    """Parse JSON verdict from LLM response. Handles fenced code blocks."""
    # Try to extract JSON from markdown fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # Try bare JSON
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
    try:
        data = json.loads(raw)
        verdict = str(data.get("verdict", "")).upper()
        if verdict not in ("APPROVE", "REJECT", "MODIFY"):
            return None
        rationale = str(data.get("rationale", ""))
        suggested = data.get("suggested_args")
        if verdict == "MODIFY" and not isinstance(suggested, dict):
            return None
        if verdict in ("APPROVE", "REJECT"):
            suggested = None
        return GuardianVerdict(verdict=verdict, rationale=rationale, suggested_args=suggested)
    except Exception:
        return None


def _looks_like_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ("rate limit", "rate_limit", "ratelimit", "too many requests", "429", "quota exceeded", "throttl"))


# --- Recent Context Builder ---------------------------------------------


def build_recent_context(memory, max_messages: int = 4, max_chars: int = 2000) -> str:
    """Build the projected-history string for Guardian (mirrors subagent_host.py)."""
    parts = []
    if getattr(memory, "compaction_summary", None):
        parts.append(f"[PARENT CONTEXT SUMMARY]\n{memory.compaction_summary}")
    messages = getattr(memory, "messages", [])
    for m in messages[-max_messages:]:
        role = getattr(m, "role", "unknown")
        content = getattr(m, "content", "")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... ({len(content)} total chars)"
        role_label = "USER" if role == "user" else "ASSISTANT" if role == "assistant" else role.upper()
        parts.append(f"[{role_label}]\n{content}")
    return "\n\n".join(parts)
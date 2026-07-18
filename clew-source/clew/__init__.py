"""
Clew — A native, local-first AI IDE.
v1.1.0: Heavy Code section released (multi-agent + subagents + 10/day free),
        MCP (Model Context Protocol) support in all sections, expanded
        Agent settings GUI (max_iterations, planning, run_timeout,
        temperature, max_tokens, top_p, memory limits, daily quota),
        per-section daily quota tracking.
v1.0.12: Fixed remaining H/M bugs (H-API-2/3, M1, M3, M4, M5, M7, M8, M-AUTO-1/6/8).
v1.0.11: Git tools (git_status, git_diff, git_stage, git_commit) for
        direct project access like Claude Code. SKILL.md format for
        reusable instruction packages with frontmatter. Skill catalog
        injected into system prompt; agent calls get_skill(id) to pull
        full bodies on demand.
"""

__version__ = "1.1.0"
__all__ = ["__version__"]

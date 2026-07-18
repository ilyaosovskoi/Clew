"""
Clew v1.0.11 — Skill Loader (SKILL.md format).

Implements the Claude Code model of reusable instruction packages:

  SKILL.md    — a Markdown file with YAML frontmatter describing when
                the skill should be used, plus a body with step-by-step
                instructions. Loaded automatically from:
                  - <project>/.clew/skills/*/SKILL.md  (project-level)
                  - ~/.clew/skills/*/SKILL.md          (user-level)
                  - <project>/.clew/skills/*.md         (single-file)
                  - ~/.clew/skills/*.md                (single-file)

  Activation  — manual (user picks the skill from the UI) or automatic
                (the agent reads skill descriptions and decides which
                one fits the task). Automatic activation is done by
                including all skill descriptions in the system prompt
                and letting the model emit a {"tool": "use_skill", ...}
                call — but for v1.0.11 we keep it simpler: the agent
                gets a "skills catalog" section in the system prompt
                and can request the full text of a skill via the
                get_skill tool.

This mirrors how Claude Code treats skills as "instruction packages
that get injected into context when needed" rather than separate
programs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class Skill:
    """One reusable instruction package."""
    id: str                        # unique identifier (slug)
    name: str                      # human-readable name
    description: str               # when to use this skill
    tag: str = "general"           # category: backend, frontend, devops, etc.
    body: str = ""                 # the actual instructions (markdown)
    source_path: str = ""          # where it was loaded from (for debugging)
    project_level: bool = False    # True = from project, False = user-global

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tag": self.tag,
            "source_path": self.source_path,
            "project_level": self.project_level,
            "body_chars": len(self.body),
        }


# ── Frontmatter parser ───────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)


def _parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML-like frontmatter from a markdown file.

    We use a minimal parser (not full YAML) because skills should be
    simple key:value pairs. Returns (metadata_dict, body_markdown).
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content

    raw_meta = m.group(1)
    body = m.group(2)

    meta: Dict[str, Any] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace("-", "_").replace(" ", "_")
        value = value.strip().strip('"').strip("'")
        meta[key] = value

    return meta, body


def _slugify(name: str) -> str:
    """Convert a skill name to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unnamed"


# ── Skill loader ─────────────────────────────────────────────────────

def _load_skill_file(path: Path, project_level: bool) -> Optional[Skill]:
    """Load a single SKILL.md or *.md file into a Skill object."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("[skills] failed to read %s: %s", path, e)
        return None

    meta, body = _parse_frontmatter(content)

    # Determine id: frontmatter > filename > slugified name
    skill_id = meta.get("id") or path.stem
    name = meta.get("name") or path.stem.replace("_", " ").replace("-", " ").title()
    description = meta.get("description") or meta.get("desc") or ""
    tag = meta.get("tag") or meta.get("category") or "general"

    return Skill(
        id=skill_id,
        name=name,
        description=description,
        tag=tag,
        body=body.strip(),
        source_path=str(path),
        project_level=project_level,
    )


def load_all_skills(project_root: Optional[Path] = None) -> List[Skill]:
    """Load all skills from project-level and user-level directories.

    Priority (later sources override earlier ones with the same id):
      1. User-global: ~/.clew/skills/*/SKILL.md and ~/.clew/skills/*.md
      2. Project-level: <project>/.clew/skills/*/SKILL.md and
         <project>/.clew/skills/*.md

    Project-level skills override user-global ones with the same id —
    this lets a project customise a global skill for its own needs.
    """
    skills_by_id: Dict[str, Skill] = {}

    # Normalise project_root to Path
    if project_root:
        project_root = Path(project_root)

    # 1. User-global skills
    user_skill_dir = Path.home() / ".clew" / "skills"
    if user_skill_dir.is_dir():
        for skill in _iter_skill_dir(user_skill_dir, project_level=False):
            skills_by_id[skill.id] = skill

    # 2. Project-level skills (override user-global)
    if project_root and project_root.is_dir():
        proj_skill_dir = project_root / ".clew" / "skills"
        if proj_skill_dir.is_dir():
            for skill in _iter_skill_dir(proj_skill_dir, project_level=True):
                skills_by_id[skill.id] = skill

    return list(skills_by_id.values())


def _iter_skill_dir(skill_dir: Path, project_level: bool) -> List[Skill]:
    """Iterate a skills directory, loading both SKILL.md and *.md files.

    Supports two layouts:
      1. Flat:    skills/python-architect.md, skills/ui-polish.md
      2. Nested:  skills/python-architect/SKILL.md, skills/ui-polish/SKILL.md
                  (the directory name is the skill id if frontmatter
                   doesn't specify one; supplementary files in the same
                   directory are NOT loaded automatically — the skill
                   body can reference them by relative path and the
                   agent can read_file them)
    """
    skills: List[Skill] = []
    try:
        entries = sorted(skill_dir.iterdir())
    except OSError as e:
        logger.warning("[skills] failed to list %s: %s", skill_dir, e)
        return skills

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            # Look for SKILL.md inside
            skill_md = entry / "SKILL.md"
            if skill_md.exists():
                skill = _load_skill_file(skill_md, project_level=project_level)
                # v1.0.11: if the frontmatter didn't specify an id,
                # use the directory name as the skill id (not "SKILL")
                if skill and (not skill.id or skill.id.lower() == "skill"):
                    skill.id = _slugify(entry.name)
                if skill:
                    skills.append(skill)
        elif entry.is_file() and entry.suffix.lower() == ".md":
            skill = _load_skill_file(entry, project_level=project_level)
            if skill:
                skills.append(skill)

    return skills


# ── Skill catalog (for system prompt injection) ──────────────────────

def build_skill_catalog(skills: List[Skill]) -> str:
    """Build a compact catalog of available skills for the system prompt.

    The catalog lists each skill's id, name, tag, and description so
    the agent can decide which skill to request via get_skill. This
    keeps the full skill bodies OUT of the system prompt (saving
    context tokens) until the agent explicitly asks for one.
    """
    if not skills:
        return ""

    lines = ["# Available skills", ""]
    lines.append("You can request the full instructions for any skill by calling:")
    lines.append('  {"tool": "get_skill", "args": {"id": "skill_id"}}')
    lines.append("")
    lines.append("Skills are reusable instruction packages. Activate one when the")
    lines.append("task matches its description. Do NOT activate a skill unless it fits.")
    lines.append("")
    for s in skills:
        tag_str = f" [{s.tag}]" if s.tag and s.tag != "general" else ""
        level_str = " (project)" if s.project_level else ""
        lines.append(f"- **{s.id}**{tag_str}{level_str}: {s.name}")
        if s.description:
            lines.append(f"  {s.description}")
    return "\n".join(lines)


def get_skill_body(skills: List[Skill], skill_id: str) -> Optional[str]:
    """Return the full body text of a skill by id, or None if not found."""
    for s in skills:
        if s.id == skill_id:
            return s.body
    return None


# ── Built-in skills (legacy from v1.0.3) ─────────────────────────────
# These are kept as a fallback so users without any SKILL.md files
# still see a non-empty skill list. They can be overridden by
# user-global or project-level skills with the same id.

_BUILTIN_SKILLS: List[Skill] = [
    Skill(
        id="python_architect",
        name="Python Architect",
        description="Designs clean package structures, dependency boundaries, layered architecture. Use when scaffolding a new Python project or reorganising an existing one.",
        tag="architect",
        body=(
            "# SKILL: Python Architect\n\n"
            "You design clean, layered Python projects.\n"
            "Rules:\n"
            "- Separate concerns: routers -> services -> repositories -> models.\n"
            "- No business logic in route handlers.\n"
            "- Type-hint every public function; run mypy --strict in your head.\n"
            "- Prefer composition over inheritance.\n"
            "- Every module has a one-line docstring stating its responsibility.\n"
            "- If a file exceeds 300 lines, propose splitting it.\n"
        ),
    ),
    Skill(
        id="ui_polish",
        name="UI Polish",
        description="Pixel-perfect CSS, motion systems, accessibility, responsive behavior. Use when the task involves frontend styling or layout.",
        tag="frontend",
        body=(
            "# SKILL: UI Polish\n\n"
            "You produce pixel-perfect frontends.\n"
            "Rules:\n"
            "- Respect a design system: spacing scale, type scale, motion tokens.\n"
            "- Never use pure black or pure white.\n"
            "- All animations use cubic-bezier easing, never linear for organic motion.\n"
            "- Test keyboard navigation and screen-reader labels.\n"
            "- Mobile-first: layout works at 375px before 1440px.\n"
        ),
    ),
    Skill(
        id="security_auditor",
        name="Security Auditor",
        description="Threat models, OWASP, secrets hygiene, sandboxing, least privilege. Use when reviewing code for security issues.",
        tag="security",
        body=(
            "# SKILL: Security Auditor\n\n"
            "You review code for security issues.\n"
            "Rules:\n"
            "- Treat all input as hostile until proven otherwise.\n"
            "- Check OWASP Top 10 by default.\n"
            "- Never log secrets, tokens, or PII.\n"
            "- Prefer parameterized queries; reject string-built SQL.\n"
            "- Sandbox subprocess calls; whitelist binaries.\n"
        ),
    ),
    Skill(
        id="test_engineer",
        name="Test Engineer",
        description="Property tests, fuzzing, fixtures, coverage of edge cases. Use when writing or reviewing tests.",
        tag="testing",
        body=(
            "# SKILL: Test Engineer\n\n"
            "You write comprehensive tests.\n"
            "Rules:\n"
            "- Cover happy path, edge cases, error paths.\n"
            "- Use property-based tests where applicable (hypothesis).\n"
            "- One assertion per test function when possible.\n"
            "- Name tests after the behaviour, not the implementation.\n"
            "- Mock at the boundary, not at the core.\n"
        ),
    ),
    Skill(
        id="devops",
        name="DevOps",
        description="CI/CD, IaC, containers, blue-green deploys, incident response. Use for deployment or infrastructure tasks.",
        tag="devops",
        body=(
            "# SKILL: DevOps\n\n"
            "You automate deployments and infrastructure.\n"
            "Rules:\n"
            "- Infrastructure as code (Terraform, Pulumi).\n"
            "- CI runs on every push; CD requires approval.\n"
            "- Blue-green or canary for production deploys.\n"
            "- Rollback plan for every change.\n"
            "- Monitor after deploy; alert on SLO breach.\n"
        ),
    ),
]


def load_all_skills_with_builtins(project_root: Optional[Path] = None) -> List[Skill]:
    """Load user + project skills, plus built-in skills as a fallback.

    Built-in skills have the lowest priority — a user-global or
    project-level skill with the same id overrides them.
    """
    skills_by_id: Dict[str, Skill] = {}

    # 1. Built-in skills (lowest priority)
    for s in _BUILTIN_SKILLS:
        skills_by_id[s.id] = s

    # 2. User-global + project-level skills (override built-ins)
    for s in load_all_skills(project_root):
        skills_by_id[s.id] = s

    return list(skills_by_id.values())

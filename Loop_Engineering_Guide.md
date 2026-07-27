# Loop Engineering Guide

## Quick Start (5 Minutes)

```
1. Read this guide (you are here)
2. Pick a loop type from Loops_Library.md
3. Copy Success_Criteria_Template.md → loops/active/LOOP-YYYY-MM-DD-XXX.md
4. Fill Problem Statement + 3–5 Success Criteria
5. Get stakeholder sign-off (async OK)
6. Execute following the loop structure
7. Close: fill evaluation, link evidence, archive
8. Extract learnings → Learnings.md
```

---

## Core Principles

| Principle | What It Means |
|-----------|---------------|
| **Explicit over implicit** | Every loop has written criteria, owner, deadline |
| **Evidence-based** | Decisions backed by data, tests, metrics — not hunches |
| **Continuity** | Artifacts persist across sessions, people, time |
| **Learning capture** | Every loop produces at least one learning entry |
| **Time-boxed** | All loops have max duration; auto-escalate if exceeded |
| **Shim-first refactoring** | Never break consumers; migrate incrementally |

---

## File Map & Relationships

```
Project Root/
├── CLAUDE.md                    # Project instructions (source of truth)
├── Success_Criteria_Template.md # Template for loop kickoff
├── Learnings.md                 # Institutional knowledge base
├── Loops_Library.md             # Loop patterns catalog
├── Weekly_System_Review.md      # Weekly cadence template
├── Loop_Engineering_Guide.md    # This file
├── loops/
│   ├── active/                  # Current loops (one file each)
│   │   ├── LOOP-BUG-2025-07-26-001.md
│   │   └── LOOP-FEAT-2025-07-20-003.md
│   └── archive/                 # Closed loops (immutable)
│       ├── LOOP-REFACTOR-2025-07-15-001.md
│       └── ...
├── learnings/                   # Individual learning entries
│   ├── 2025-07-25_css_textual_rewrite.md
│   ├── 2025-07-26_staticmethod_bug.md
│   └── ...
└── reviews/                     # Weekly review history
    ├── 2025-07-28_weekly_review.md
    └── ...
```

---

## Common Workflows

### Starting a New Loop
```bash
# 1. Choose type from Loops_Library.md
# 2. Create loop file
cp Success_Criteria_Template.md loops/active/LOOP-BUG-2025-07-26-001.md

# 3. Edit with your editor
vim loops/active/LOOP-BUG-2025-07-26-001.md

# 4. Create GitHub issue for tracking
gh issue create --title "LOOP-BUG-2025-07-26-001: Fix Guardian timeout" \
  --body "See loops/active/LOOP-BUG-2025-07-26-001.md" \
  --label "loop,loop:BUG"
```

### During Loop Execution
- **Daily**: Update loop file with progress, blockers
- **At decision points**: Reference Learnings.md for relevant past learnings
- **When stuck**: Check Loops_Library.md for anti-patterns, variations

### Closing a Loop
```bash
# 1. Fill evaluation section in loop file
vim loops/active/LOOP-BUG-2025-07-26-001.md

# 2. Create learning entry if new insight
cp Learnings.md learnings/2025-07-26_guardian_timeout_root_cause.md
# Edit with template from Learnings.md

# 3. Archive loop
mv loops/active/LOOP-BUG-2025-07-26-001.md loops/archive/

# 4. Close GitHub issue
gh issue close 123 --reason completed
```

### Weekly Review
```bash
# 1. Copy template
cp Weekly_System_Review.md reviews/2025-07-28_weekly_review.md

# 2. Fill during meeting (60-90 min)
vim reviews/2025-07-28_weekly_review.md

# 3. Create action item issues
gh issue create --title "Action: Add import smoke test" --label "action-item"
```

---

## Loop Type Quick Reference

| If you're... | Use Loop Type | Max Duration |
|--------------|---------------|--------------|
| Fixing a bug | `LOOP-BUG` | 5 days |
| Building a feature | `LOOP-FEAT` | 6 weeks |
| Restructuring code | `LOOP-REFACTOR` | 4 weeks |
| Optimizing speed | `LOOP-PERF` | 2 weeks |
| Addressing CVE/audit | `LOOP-SEC` | 30 days |
| Paying down debt | `LOOP-DEBT` | 6 weeks |
| Responding to outage | `LOOP-INCIDENT` | Mitigate ASAP |
| Exploring uncertainty | `LOOP-EXPERIMENT` | 5 days |
| Upgrading dependency | `LOOP-DEPUP` | 2 weeks |
| Writing docs | `LOOP-DOCS` | 1 week |

---

## Success Criteria Cheat Sheet

**Good Criteria**:
- ✅ "Reduce p95 latency from 800ms to 300ms on `/api/chat` endpoint"
- ✅ "All 43 Guardian tests pass + new sub-agent tests pass"
- ✅ "Zero `shell=True` usages in codebase (grep verification)"
- ✅ "Onboarding time for new dev < 2 hours (measured)"

**Bad Criteria**:
- ❌ "Make it faster"
- ❌ "Improve code quality"
- ❌ "Fix the Guardian"
- ❌ "Better documentation"

**Rule**: If you can't write a `pytest` or `grep` command to verify it, it's not measurable.

---

## Learnings Workflow

### When to Create a Learning
- Loop closes with unexpected result
- Incident/near-miss resolved
- Experiment concludes (even negative!)
- Refactoring reveals pattern
- New team member asks "why?" and answer isn't documented

### Learning Quality Bar
| Level | Requirement |
|-------|-------------|
| **Tentative** | Hypothesis from single data point |
| **Validated** | Reproduced, multiple data points, peer reviewed |
| **Superseded** | New evidence contradicts; link to replacement |

**Only `validated` learnings should drive process changes.**

---

## Anti-Patterns Quick Check

Before starting any loop, verify you're NOT:
- [ ] Starting without written success criteria
- [ ] Planning > 6 weeks without vertical slices
- [ ] Refactoring without shim layer + import test
- [ ] Optimizing without profiling first
- [ ] Running experiment without timebox + decision criteria
- [ ] Closing loop without evaluation + learnings
- [ ] Skipping weekly review for > 2 weeks

---

## Integration with GitHub

### Labels
```
loop              # All loop issues
loop:BUG          # Filter by type
loop:FEAT
loop:REFACTOR
loop:PERF
loop:SEC
loop:DEBT
loop:INCIDENT
loop:EXPERIMENT
loop:DEPUP
loop:DOCS
loop:ACTIVE       # State
loop:BLOCKED
loop:IN_REVIEW
loop:CLOSED
loop:ABANDONED
action-item       # From weekly review
learning          # New learning to document
```

### Branch Naming
```
loop/BUG-2025-07-26-001/fix-guardian-timeout
loop/FEAT-2025-07-20-003/web-fetch-tool
loop/REFACTOR-2025-07-15-001/split-office-worker
```

### PR Template
```markdown
## Loop: LOOP-XXX
## Summary
Closes LOOP-XXXX — one sentence what this PR does

## Success Criteria Verification
- [ ] Criterion 1: Evidence (test link, metric, screenshot)
- [ ] Criterion 2: Evidence
- [ ] All existing tests pass

## Learnings
- New learning: LEARN-YYYYMMDD-XXX (or "None new")
```

---

## Tooling Helpers

### Create Loop Script
```bash
#!/bin/bash
# scripts/new_loop.sh <TYPE> <TITLE>
TYPE=$1
TITLE=$2
DATE=$(date +%Y-%m-%d)
NUM=$(ls loops/active/ | wc -l | xargs printf "%03d")
ID="LOOP-${TYPE}-${DATE}-${NUM}"
cp Success_Criteria_Template.md "loops/active/${ID}.md"
sed -i "s/LOOP-YYYY-MM-DD-XXX/${ID}/g" "loops/active/${ID}.md"
sed -i "s/One-line descriptive name/${TITLE}/g" "loops/active/${ID}.md"
echo "Created loops/active/${ID}.md"
```

### Generate Learnings Index
```python
# scripts/generate_learnings_index.py
import frontmatter, glob, os
entries = []
for f in glob.glob("learnings/*.md"):
    with open(f) as fp:
        post = frontmatter.load(fp)
        entries.append({
            'id': post.metadata.get('id', os.path.basename(f)),
            'date': post.metadata.get('date', ''),
            'tags': post.metadata.get('tags', []),
            'title': post.content.split('\n')[0].replace('# ', '')
        })
# Write index.md...
```

---

## Onboarding Checklist (New Team Member)

- [ ] Read `CLAUDE.md` (project context)
- [ ] Read `Loop_Engineering_Guide.md` (this file)
- [ ] Browse `Loops_Library.md` (loop types)
- [ ] Read 5 recent learnings from `Learnings.md`
- [ ] Attend next Weekly System Review
- [ ] Run first loop with buddy (pair on LOOP-BUG or LOOP-EXPERIMENT)
- [ ] Create first learning entry

---

## Escalation Paths

| Situation | Escalate To | Timeframe |
|-----------|-------------|-----------|
| Loop > 2x estimated duration | Tech Lead | Immediately |
| SEV-1 incident | On-call + Tech Lead + PM | 0 min |
| Security vulnerability | Security Team + Tech Lead | 1 hour |
| Architectural disagreement | Tech Lead + Principal Engineer | 1 day |
| Cross-team dependency | Tech Lead + Other Team Lead | 2 days |

---

## Maintenance

| Artifact | Update Frequency | Owner |
|----------|------------------|-------|
| `Success_Criteria_Template.md` | When pattern changes | Tech Lead |
| `Learnings.md` | Per learning (add entry) | Loop owner |
| `Loops_Library.md` | Quarterly + new patterns | Team |
| `Weekly_System_Review.md` | Weekly (new file each week) | Rotating facilitator |
| `Loop_Engineering_Guide.md` | Semi-annually | Tech Lead |

---

## Philosophy in One Paragraph

> Loop Engineering treats engineering work as a series of explicit, time-boxed, measurable cycles. Each loop produces a verifiable outcome *and* institutional knowledge. The artifacts (criteria, learnings, reviews) are the product — the code is a byproduct. Continuity survives team changes because the system remembers what worked, what failed, and why.
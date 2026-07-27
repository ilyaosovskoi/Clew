# Weekly System Review Template

## Purpose
Structured, recurring review of the engineering system's health, progress, and direction. Runs every week to maintain continuity, surface risks early, and drive continuous improvement.

**Cadence**: Every Monday 10:00 AM (or first working day of week)  
**Duration**: 60–90 minutes  
**Participants**: Tech lead + 1–2 rotating engineers (spreads context)  
**Output**: Updated `Weekly_System_Review.md` with decisions, action items, learnings

---

## Pre-Review Preparation (Async, 15 min before meeting)

Each participant reviews:
- [ ] **Active Loops Dashboard** — GitHub issues with `loop` label, filtered by state
- [ ] **Metrics Dashboard** — Key charts (see Metrics Section below)
- [ ] **Incident Log** — Any SEV-1/2 since last review
- [ ] **PR Queue** — Open PRs > 3 days, stale reviews
- [ ] **Learnings Log** — New entries since last review

---

## Agenda (90 min)

### 1. Loop Health Check (20 min)
**Goal**: Ensure all active loops are on track, unblocked, or consciously paused.

| Loop ID | Type | State | Target Close | On Track? | Blockers | Decision |
|---------|------|-------|--------------|-----------|----------|----------|
| LOOP-XXX | FEAT | ACTIVE | 2025-08-15 | 🟡 | Waiting on API design | Extend 1 week |
| LOOP-YYY | REFACTOR | IN_REVIEW | 2025-08-01 | 🟢 | — | Merge when CI green |

**Actions**:
- Move stuck loops to `BLOCKED` with owner + unblock date
- Abandon loops superseded by new info → `ABANDONED` with reason
- Escalate loops > 2x estimated duration

---

### 2. Metrics Review (15 min)
**Goal**: Data-driven view of system health. No anecdotes without numbers.

| Category | Metric | Current | Trend (4w) | Target | Status | Notes |
|----------|--------|---------|------------|--------|--------|-------|
| **Reliability** | Error rate (5xx) | 0.02% | ↘️ | < 0.1% | 🟢 | |
| | P95 latency | 320ms | → | < 500ms | 🟢 | |
| | Uptime | 99.97% | → | 99.9% | 🟢 | |
| **Quality** | Test coverage | 87% | ↗️ | > 80% | 🟢 | |
| | Flaky tests/week | 2 | ↘️ | 0 | 🟡 | #2 flaky in test_guardian |
| | Critical bugs open | 1 | → | 0 | 🟡 | #1234 |
| **Velocity** | PRs merged/week | 12 | ↗️ | — | 🟢 | |
| | Cycle time (median) | 1.8d | ↘️ | < 3d | 🟢 | |
| | Review turnaround | 4.2h | → | < 8h | 🟢 | |
| **Security** | Open CVEs > 7d | 0 | → | 0 | 🟢 | |
| | Dependabot PRs open | 3 | → | < 5 | 🟢 | |
| **Technical Debt** | Complexity (avg) | 12.4 | → | < 15 | 🟢 | |
| | TODO/FIXME count | 47 | ↗️ | < 30 | 🟡 | Growing in agent_runtime |

**Dashboard Links**:
- Grafana: `https://grafana.internal/d/clew-system-health`
- Code Climate: `https://codeclimate.com/github/ilyaosovskoi/Clew`
- GitHub Insights: `https://github.com/ilyaosovskoi/Clew/pulse`

---

### 3. Incident & Near-Miss Review (10 min)
**Goal**: Learn from failures without blame.

| Incident ID | Severity | Summary | Root Cause | Action Items | Status |
|-------------|----------|---------|------------|--------------|--------|
| INC-2025-07-24 | SEV-2 | TUI crash on startup | CSS var() incompatibility | Add CSS lint to CI | ✅ Done |
| N/A | Near-miss | Refactor broke imports | No import smoke test | Add import test to CI | 🔄 In progress |

**Questions**:
- Any pattern across incidents?
- Runbook updates needed?
- Monitoring gaps?

---

### 4. Architecture & Technical Decisions (15 min)
**Goal**: Review pending ADRs, assess architectural drift, approve/reject proposals.

| ADR / Topic | Status | Decision | Owner | Next Step |
|-------------|--------|----------|-------|-----------|
| ADR-004: Sub-agent tool isolation | PROPOSED | — | @backend-lead | Review Friday |
| Migration to AgentRuntimeV2 | IN_PROGRESS | 60% consumers migrated | @team | Target Q3 |

**New Proposals This Week**:
- [ ] Title — one-liner, link to RFC

---

### 5. Learnings Integration (10 min)
**Goal**: Convert raw experience into institutional knowledge.

**New Learnings Since Last Review**:
- LEARN-20250726-001: Module-level @staticmethod breaks imports
- LEARN-20250726-002: TYPE_CHECKING imports stripped at runtime

**Actions**:
- [ ] Add to relevant runbooks / docs
- [ ] Share in team slack #learnings
- [ ] Update onboarding checklist

---

### 6. Prioritization & Capacity (10 min)
**Goal**: Align next week's work with capacity and strategy.

**Capacity Next Week**:
| Engineer | Availability | Focus Area |
|----------|--------------|------------|
| @alice | 100% | LOOP-FEAT-003 (web_fetch) |
| @bob | 50% (on-call) | LOOP-BUG-001, reviews |
| @carol | 100% | LOOP-REFACTOR-002 (office_worker) |

**Prioritized Backlog for Next Week**:
1. LOOP-FEAT-003: web_fetch tool (user-requested, high value)
2. LOOP-BUG-001: Guardian sub-agent timeout (reliability)
3. LOOP-REFACTOR-002: office_worker split (debt reduction)
4. LOOP-DEPUP-001: Update PySide6 to 6.12 (security)

**Deferred / Icebox**:
- LOOP-FEAT-007: Image generation (waiting on GPU infra)
- LOOP-EXPERIMENT-002: Local LLM benchmark (no capacity)

---

### 7. Action Items & Close (10 min)
**Goal**: Clear owners, deadlines, tracking.

| # | Action | Owner | Due | Tracking |
|---|--------|-------|-----|----------|
| 1 | Add import smoke test to CI | @bob | 2025-07-30 | GH Issue #1256 |
| 2 | Review ADR-004 | @team | 2025-08-01 | Slack thread |
| 3 | Fix flaky test_guardian::test_xxx | @alice | 2025-07-28 | PR #1257 |
| 4 | Document CSS migration in Learnings | @carol | 2025-07-27 | LEARN-20250725-001 |

**Next Review**: 2025-08-04 10:00 AM  
**Facilitator Next Week**: @alice (rotates)

---

## Metrics Definitions (For Consistency)

| Metric | Definition | Source | Alert Threshold |
|--------|------------|--------|-----------------|
| Error rate (5xx) | 5xx responses / total requests (5m window) | Prometheus / Grafana | > 0.5% for 5m |
| P95 latency | 95th percentile request latency | Prometheus / Grafana | > 1s for 10m |
| Uptime | % time service healthy (no SEV-1) | Statuspage / PagerDuty | < 99.9% monthly |
| Test coverage | Lines covered / total lines (excl. generated) | CodeClimate / pytest-cov | < 80% |
| Flaky tests/week | Tests passing & failing on same commit | CI history | > 0 |
| Cycle time | PR created → merged (median) | GitHub Insights | > 5d |
| Review turnaround | PR ready → first review (median) | GitHub Insights | > 24h |
| Complexity | Cyclomatic complexity average | CodeClimate / radon | > 20 |

---

## Anti-Patterns to Avoid

| Anti-Pattern | Symptom | Correction |
|--------------|---------|------------|
| **Status Update Theater** | Reading Jira tickets aloud | Async prep; meeting = decisions only |
| **Metrics Without Action** | "Errors up 2%" → no owner | Every red metric = action item |
| **Blaming in RCA** | "Bob broke the build" | Focus on system, not person |
| **Ignoring Near-Misses** | Only reviewing SEV-1 | Near-misses = free lessons |
| **Capacity Denial** | Planning 100% utilization | Plan 70%, buffer 30% |
| **Decision Deferral** | "Let's discuss offline" → never | Decide in meeting or explicit deferral with date |

---

## Template for Next Week (Copy-Paste)

```markdown
# Weekly System Review — 2025-WXX (YYYY-MM-DD)

## Attendees
- @facilitator
- @engineer1
- @engineer2

## 1. Loop Health Check
| Loop ID | Type | State | Target Close | On Track? | Blockers | Decision |
|---------|------|-------|--------------|-----------|----------|----------|
| | | | | | | |

## 2. Metrics Review
| Category | Metric | Current | Trend | Target | Status | Notes |
|----------|--------|---------|-------|--------|--------|-------|
| Reliability | Error rate | | | < 0.1% | | |
| | P95 latency | | | < 500ms | | |
| Quality | Test coverage | | | > 80% | | |
| Velocity | Cycle time | | | < 3d | | |

## 3. Incident Review
| Incident | Severity | Summary | Root Cause | Actions | Status |
|----------|----------|---------|------------|---------|--------|

## 4. Architecture Decisions
| ADR/Topic | Status | Decision | Owner | Next |
|-----------|--------|----------|-------|------|

## 5. New Learnings
- LEARN-XXXX: Title

## 6. Prioritization
**Capacity**: @name: XX%
**Top 3**: 1. LOOP-XXX  2. LOOP-YYY  3. LOOP-ZZZ

## 7. Action Items
| # | Action | Owner | Due | Tracking |
|---|--------|-------|-----|----------|
| 1 | | | | |
```

---

## Historical Reviews Archive

| Date | Link | Key Decisions |
|------|------|---------------|
| 2025-07-28 | `reviews/2025-07-28_weekly_review.md` | Approved ADR-003, prioritized web_fetch |
| 2025-07-21 | `reviews/2025-07-21_weekly_review.md` | Started agent_runtime refactor, added import smoke test |

> Store completed reviews in `reviews/YYYY-MM-DD_weekly_review.md` for audit trail.
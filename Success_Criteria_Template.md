# Success Criteria Template

## Purpose
Defines clear, measurable success criteria for any loop, initiative, or deliverable. Used at loop inception to prevent scope creep and enable objective evaluation at loop close.

---

## Template

### Loop / Initiative Identity
| Field | Value |
|-------|-------|
| **Loop ID** | `LOOP-YYYY-MM-DD-XXX` (e.g., `LOOP-2025-07-26-001`) |
| **Title** | One-line descriptive name |
| **Owner** | @username |
| **Start Date** | YYYY-MM-DD |
| **Target Close Date** | YYYY-MM-DD |
| **Related Issues/PRs** | #123, #456 |
| **Parent Loop** | LOOP-XXXX (if nested) |

---

### Problem Statement
**What problem are we solving?**
> Concise description of the pain point or opportunity.

**Why now?**
> Urgency, context, or triggering event.

---

### Success Criteria (MUST be measurable)

| # | Criterion | Metric / Definition of Done | Target | Measurement Method | Weight |
|---|-----------|----------------------------|--------|-------------------|--------|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |

**Guidelines for good criteria:**
- **Specific**: "Reduce p95 latency from 800ms to 300ms" not "make it faster"
- **Measurable**: Quantitative where possible; qualitative with clear rubric
- **Achievable**: Realistic given constraints (time, resources, dependencies)
- **Relevant**: Tied to user/business value, not vanity metrics
- **Time-bound**: Clear deadline for evaluation

---

### Anti-Criteria (What Failure Looks Like)
| # | Failure Signal | Threshold | Action if Triggered |
|---|----------------|-----------|---------------------|
| 1 | | | |
| 2 | | | |

---

### Constraints & Assumptions
| Type | Detail |
|------|--------|
| **Time** | Max hours/days allocated |
| **Budget** | Compute, API costs, tooling |
| **Dependencies** | Other teams, migrations, releases |
| **Technical** | Platform limits, legacy debt |
| **Assumptions** | "Provider X API remains stable", "No schema changes needed" |

---

### Risk Register
| Risk | Likelihood (1-5) | Impact (1-5) | Mitigation | Owner |
|------|-----------------|--------------|------------|-------|
| | | | | |

---

### Stakeholder Sign-off
| Role | Name | Approved (Y/N) | Date | Comments |
|------|------|----------------|------|----------|
| Product | | | | |
| Engineering Lead | | | | |
| QA / Security | | | | |

---

### Loop Close Evaluation (Filled at End)

| Criterion | Target | Actual | Pass/Fail | Evidence Link |
|-----------|--------|--------|-----------|---------------|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

**Overall Verdict**: □ PASS  □ PARTIAL  □ FAIL

**Retrospective Notes**:
> What worked? What didn't? What would we do differently?

**Follow-up Loops Needed**:
- [ ] LOOP-XXXX: Description
- [ ] LOOP-XXXX: Description

---

## Example: Filled Template

### Loop / Initiative Identity
| Field | Value |
|-------|-------|
| **Loop ID** | `LOOP-2025-07-26-001` |
| **Title** | Guardian Sub-Agent Reviewer Implementation |
| **Owner** | @backend-lead |
| **Start Date** | 2025-07-20 |
| **Target Close Date** | 2025-07-27 |
| **Related Issues/PRs** | #4 |
| **Parent Loop** | N/A |

### Problem Statement
**What problem are we solving?**
> Guardian LLM review currently uses the main provider directly, which shares the same context window and token budget as the agent. A dedicated read-only sub-agent isolates review logic, enforces tool restrictions at dispatch level, and prevents prompt injection from tool arguments.

**Why now?**
> Issue #4 blocked on v2.0 release; security review required before GA.

### Success Criteria

| # | Criterion | Metric / Definition of Done | Target | Measurement Method | Weight |
|---|-----------|----------------------------|--------|-------------------|--------|
| 1 | Sub-agent spawns and completes review | `review_with_subagent()` returns `GuardianVerdict` in < 10s p95 | ≤ 10s p95 | Integration test + production telemetry | 40% |
| 2 | Tool restrictions enforced | Sub-agent with `role="explore"` rejects `write_file`/`execute_command` at dispatch | 100% rejection rate | Unit tests: `test_review_with_subagent_spawn_failure` | 30% |
| 3 | Verdict parity with direct LLM path | Same `GuardianVerdict` distribution (APPROVE/REJECT/MODIFY) ±5% | ±5% variance | A/B test on 100 sample calls | 20% |
| 4 | No regression in existing Guardian tests | All 43 existing tests pass | 100% pass | `pytest clew/agent/test_guardian.py` | 10% |

### Anti-Criteria
| # | Failure Signal | Threshold | Action if Triggered |
|---|----------------|-----------|---------------------|
| 1 | Sub-agent latency > 30s p95 | > 30s | Fall back to direct provider path, alert on-call |
| 2 | Verdict mismatch > 15% | > 15% | Disable sub-agent path, investigate prompt drift |

### Constraints & Assumptions
| Type | Detail |
|------|--------|
| **Time** | 3 engineering days |
| **Dependencies** | `clew/agent/runtime.py` SubagentV2 must be stable |
| **Technical** | Must reuse existing `_parse_verdict()` helper |
| **Assumptions** | Provider supports `stream=true` for sub-agent calls |

### Risk Register
| Risk | Likelihood | Impact | Mitigation | Owner |
|------|------------|--------|------------|-------|
| Sub-agent spawn fails under load | 3 | 4 | Circuit breaker + fallback to direct path | @backend-lead |
| Prompt injection via tool args | 2 | 5 | Tool arg sanitization in `assess_risk()` | @security |

---

## Usage Instructions

1. **Create at loop kickoff** — Before any implementation work begins
2. **Review with stakeholders** — Get sign-off on criteria *before* coding
3. **Store in `loops/active/LOOP-YYYY-MM-DD-XXX.md`** — Version controlled
4. **Update at loop close** — Fill evaluation section, link evidence
5. **Archive** — Move to `loops/archive/` after close
6. **Reference in PRs** — Include Loop ID in PR description: `Closes LOOP-2025-07-26-001`
# Loops Library

## Purpose
Catalog of reusable loop patterns for common engineering scenarios. Each pattern defines structure, success criteria template, typical duration, and known variations. Pick a pattern, customize criteria, execute.

---

## Loop Types

### 1. Bug Fix Loop (LOOP-BUG)
**Trigger**: Defect in production, CI, or reported by user  
**Goal**: Root cause fixed + regression test + monitoring stable

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Reproduce | 30m–2h | Minimal repro, add failing test | Test fails reliably |
| Diagnose | 1–4h | Root cause analysis, document in learning | Root cause identified |
| Fix | 1–8h | Minimal change, link to repro test | Test passes |
| Verify | 30m–2h | Full test suite, smoke test, deploy staging | All green, staging healthy |
| Monitor | 24h | Watch error rate, latency, logs | Stable, no regression |

**Success Criteria Template**:
- [ ] Repro test added and passing
- [ ] Root cause documented in learning
- [ ] Fix deployed to prod
- [ ] 24h monitoring clean (error rate < baseline)
- [ ] No new flaky tests introduced

**Variations**:
- **Hotfix** (< 4h): Skip staging, feature flag, direct to prod
- **Regression** (1–3d): Multiple components, needs integration test
- **Heisenbug** (5d max): Add observability first, then diagnose

**Anti-Patterns**:
- Fix without repro test
- No 24h monitoring
- "Works on my machine" closure

---

### 2. Feature Development Loop (LOOP-FEAT)
**Trigger**: New user-facing capability requested  
**Goal**: Shippable feature meeting all acceptance criteria

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Design | 1–3d | RFC/ADR, API contracts, UI mockups, data model | Stakeholder sign-off |
| Slice | 0.5d | Break into vertical slices (each shippable) | 3–5 slices, each < 3d |
| Implement Slice 1 | 1–3d | Core happy path | Slice demoable |
| Implement Slice N | 1–3d each | Incremental value | Each slice demoable |
| Hardening | 1–3d | Edge cases, errors, perf, accessibility | All criteria met |
| Demo & Release | 0.5d | Stakeholder demo, feature flag rollout | Signed off |

**Success Criteria Template**:
- [ ] All acceptance criteria from design doc verified
- [ ] Unit tests ≥ 80% coverage on new code
- [ ] Integration tests for each slice
- [ ] Performance within budget (latency, memory, tokens)
- [ ] Accessibility audit passed (if UI)
- [ ] Documentation updated (user + dev)
- [ ] Feature flag rollout plan executed

**Variations**:
- **MVP** (1–2 weeks): Single slice, minimal scope
- **Platform Feature** (4–6 weeks): Requires API stability, migration path
- **Experiment** (1–2 weeks): Behind flag, metrics-driven decision

**Anti-Patterns**:
- No vertical slices (horizontal layers = late integration)
- Design phase skipped
- All slices done before any demo

---

### 3. Refactoring Loop (LOOP-REFACTOR)
**Trigger**: Technical debt, architecture migration, maintainability  
**Goal**: Improved structure without behavior change

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Assess | 1–2d | Map current state, identify boundaries, risk areas | Refactor plan with slices |
| Shim | 0.5–1d | Create compatibility layer (re-exports) | All imports work via shim |
| Slice 1 | 1–3d | Extract one module, verify | Tests pass, no behavior change |
| Slice N | 1–3d each | Incremental extraction | Each slice verified |
| Migrate Consumers | 1–5d | Update imports to new paths (optional) | All consumers migrated |
| Remove Shim | 0.5d | Delete shim, verify | Clean imports, tests pass |

**Success Criteria Template**:
- [ ] All existing tests pass (100% parity)
- [ ] No behavior change (verified by diff test / golden files)
- [ ] Cyclomatic complexity reduced (target: < 15 per function)
- [ ] File/module count increased appropriately
- [ ] Import smoke test added to CI
- [ ] Migration guide written for consumers

**Variations**:
- **Strangler Fig** (6+ weeks): Gradual replacement of legacy system
- **Package Split** (2–4 weeks): Monolith → packages with shims
- **Internal Cleanup** (1–2 weeks): No API changes, internal only

**Anti-Patterns**:
- Big bang (no shim, no slices)
- Deleting old code before verifying new
- Changing behavior "while we're here"

---

### 4. Performance Optimization Loop (LOOP-PERF)
**Trigger**: SLA breach, cost reduction, scaling need  
**Goal**: Measurable improvement with no regression

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Profile | 1–2d | CPU, memory, I/O, network profiling | Bottleneck identified with data |
| Hypothesize | 0.5d | Form specific hypothesis: "X causes Y% of latency" | Falsifiable hypothesis |
| Experiment | 1–5d | Implement fix, A/B or before/after | Measurable improvement |
| Validate | 1–2d | Load test, soak test, regression suite | Stable under load |
| Rollout | 0.5d | Gradual rollout with monitoring | SLA met, no errors |

**Success Criteria Template**:
- [ ] Baseline measured (p50, p95, p99, throughput)
- [ ] Target improvement quantified (e.g., "p95 < 300ms")
- [ ] Improvement verified in staging under production-like load
- [ ] No functional regression (full test suite)
- [ ] Cost impact calculated (if applicable)
- [ ] Rollback plan tested

**Variations**:
- **Latency** (p95/p99 focus)
- **Throughput** (RPS focus)
- **Cost** ($/request focus)
- **Memory/CPU** (resource efficiency)

**Anti-Patterns**:
- Optimizing without profiling
- Microbenchmarks ≠ production
- No soak test (memory leaks appear later)

---

### 5. Security Loop (LOOP-SEC)
**Trigger**: CVE, audit finding, pentest result, dependency alert  
**Goal**: Vulnerability mitigated, no regression

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Triage | 0–4h | Assess severity, exploitability, exposure | Risk score, timeline |
| Mitigate | 1–30d | Patch, workaround, isolation | Vulnerability closed |
| Verify | 1–2d | Scan, pentest, regression test | Clean scan |
| Disclose | Per policy | Internal → users → public (if needed) | Communication done |

**Success Criteria Template**:
- [ ] CVE/finding resolved (patched or mitigated)
- [ ] Dependency scan clean (GitHub Dependabot, osv-scanner)
- [ ] No functional regression
- [ ] Incident doc if user-facing
- [ ] Process improvement if preventable

**Variations**:
- **Dependency Update** (1–7d): Bump version, test, deploy
- **Code Fix** (1–14d): Input validation, authz, crypto
- **Architecture** (30d+): Sandbox, zero-trust, encryption at rest

**Anti-Patterns**:
- Ignoring low/medium CVEs (compound risk)
- Patching without regression test
- No disclosure timeline

---

### 6. Technical Debt Loop (LOOP-DEBT)
**Trigger**: Debt identified in retro, code review, or metrics  
**Goal**: Incremental improvement, measurable quality gain

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Catalog | 1d | List debt items with impact/effort | Prioritized backlog |
| Select | 0.5d | Pick 1–2 items for this cycle | Committed items |
| Execute | 1–4w | Fix with tests | Criteria met |
| Measure | 1w | Compare metrics (complexity, coverage, bugs) | Improvement visible |

**Success Criteria Template**:
- [ ] Target metric improved (complexity, coverage, bug rate)
- [ ] No new technical debt introduced
- [ ] Tests added for changed areas
- [ ] Documentation updated

**Variations**:
- **Test Debt** (add coverage to critical paths)
- **Documentation Debt** (ADR, API docs, runbooks)
- **Architecture Debt** (coupling, layering violations)
- **Dependency Debt** (outdated deps, version pins)

**Anti-Patterns**:
- "Boy scout rule" only (never dedicated time)
- Refactoring without tests
- No measurement of improvement

---

### 7. Incident Response Loop (LOOP-INCIDENT)
**Trigger**: SEV-1/2 alert, user-reported outage  
**Goal**: Restore service, prevent recurrence

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Detect | 0–5m | Alert fires, on-call acknowledges | Acknowledged |
| Diagnose | 5–30m | Identify scope, impact, hypothesis | Hypothesis + runbook |
| Mitigate | 15m–2h | Apply workaround, scale, rollback | Service restored |
| Resolve | 1h–5d | Root cause fix, deploy | Fix deployed |
| Review | 1–3d | Postmortem, action items | Postmortem published |

**Success Criteria Template**:
- [ ] MTTD < target (e.g., 5 min)
- [ ] MTTR < target (e.g., 30 min for SEV-1)
- [ ] Postmortem within 72h
- [ ] Action items tracked as loops
- [ ] No repeat incident within 90d

**Variations**:
- **SEV-1** (all hands, < 30m MTTR)
- **SEV-2** (on-call + 1, < 2h MTTR)
- **Degraded Performance** (SLO breach, not outage)

**Anti-Patterns**:
- Debugging in prod without hypothesis
- No rollback capability
- Postmortem skipped or delayed > 1 week

---

### 8. Experiment Loop (LOOP-EXPERIMENT)
**Trigger**: Uncertainty about approach, technology, or design  
**Goal**: Data-driven decision (proceed / pivot / abandon)

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Define | 0.5d | Hypothesis, success criteria, timebox | Written experiment doc |
| Build | 1–3d | Minimal prototype / spike | Runnable experiment |
| Run | 1–5d | Execute, collect data | Data collected |
| Decide | 0.5d | Analyze, document, decide | Clear go/no-go |

**Success Criteria Template**:
- [ ] Hypothesis stated: "If we do X, Y will improve by Z%"
- [ ] Success criteria defined before run
- [ ] Timebox enforced (max 5d)
- [ ] Decision documented with evidence
- [ ] Learning captured regardless of outcome

**Variations**:
- **Spike** (1–2d): Technical feasibility only
- **A/B Test** (1–4w): User-facing, statistical significance
- **PoC** (1–2w): End-to-end viability

**Anti-Patterns**:
- No timebox (becomes zombie project)
- No success criteria (moving goalposts)
- Building production code in experiment

---

### 9. Dependency Upgrade Loop (LOOP-DEPUP)
**Trigger**: Security advisory, EOL, new features needed  
**Goal**: Updated dependency, no regression

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Assess | 0.5–1d | Changelog, breaking changes, test impact | Upgrade plan |
| Upgrade | 0.5–2d | Version bump, fix breaks | Compiles, tests pass |
| Test | 1–3d | Full suite, integration, staging | All green |
| Rollout | 0.5d | Gradual deploy, monitor | Stable in prod |

**Success Criteria Template**:
- [ ] Target version specified
- [ ] Breaking changes addressed
- [ ] Full test suite passes
- [ ] No new deprecation warnings
- [ ] 24h monitoring clean

**Variations**:
- **Patch/Minor** (hours): Usually drop-in
- **Major** (days-weeks): Breaking API changes
- **Runtime/Compiler** (weeks): Python, Rust, Node version

**Anti-Patterns**:
- Pinning versions indefinitely
- Upgrading without test coverage
- Skipping staging deploy

---

### 10. Documentation Loop (LOOP-DOCS)
**Trigger**: Missing/outdated docs, onboarding friction, audit  
**Goal**: Accurate, discoverable, maintained documentation

| Phase | Duration | Activities | Exit Criteria |
|-------|----------|------------|---------------|
| Audit | 1d | Inventory, gap analysis, freshness check | Gap list prioritized |
| Write | 1–5d | Create/update docs with examples | Review ready |
| Review | 1d | Technical + editorial review | Approved |
| Publish | 0.5d | Deploy, link, announce | Live + indexed |

**Success Criteria Template**:
- [ ] Target audience can complete task using only docs
- [ ] Code examples tested (doctest / copy-paste verify)
- [ ] Cross-references valid (no dead links)
- [ ] Last-reviewed date on each page
- [ ] Feedback mechanism in place

**Variations**:
- **API Reference** (auto-generated + hand-curated)
- **Runbooks** (incident response)
- **Architecture** (ADRs, diagrams)
- **Onboarding** (new dev setup to first PR)

**Anti-Patterns**:
- Docs separate from code (drift)
- No review process
- "Document everything" → documents nothing well

---

## Loop State Machine

```
IDEA → DESIGN → ACTIVE → BLOCKED → ACTIVE
                        ↓
                   IN_REVIEW → CLOSED
                        ↓
                   ABANDONED (with learning)
```

| State | Meaning | Next Actions |
|-------|---------|--------------|
| `IDEA` | Captured, not designed | Write design, get sign-off |
| `DESIGN` | Criteria written, not started | Assign owner, start |
| `ACTIVE` | Work in progress | Daily updates, unblock |
| `BLOCKED` | Waiting on external | Escalate, find workaround |
| `IN_REVIEW` | Criteria met, verifying | Review, merge, deploy |
| `CLOSED` | Done, evaluated, learned | Archive, celebrate |
| `ABANDONED` | Not viable, stopped | Document why, extract learning |

---

## Choosing the Right Loop

```
Is it a defect?                    → LOOP-BUG
Is it new user value?              → LOOP-FEAT
Is it structure without behavior?  → LOOP-REFACTOR
Is it slow/expensive?              → LOOP-PERF
Is it a vulnerability?             → LOOP-SEC
Is it quality debt?                → LOOP-DEBT
Is production down?                → LOOP-INCIDENT
Is the approach uncertain?         → LOOP-EXPERIMENT
Is a dependency outdated?          → LOOP-DEPUP
Is knowledge missing?              → LOOP-DOCS
```

---

## Loop Composition Rules

1. **One primary type per loop** — but can spawn sub-loops
2. **Max 3 active loops per engineer** — WIP limit
3. **Sub-loops inherit parent deadline** — unless explicitly extended
4. **Cross-cutting concerns** (security, perf) checked in every loop close

---

## Metrics for Loop Health

| Metric | Target | Measurement |
|--------|--------|-------------|
| Loop cycle time (BUG) | < 3 days | Created → Closed |
| Loop cycle time (FEAT) | < 3 weeks | Created → Closed |
| Success rate | > 85% | PASS / (PASS + FAIL) |
| Learning capture rate | 100% | Loops with learning / total closed |
| Blocker resolution time | < 1 day | BLOCKED → ACTIVE |
| Abandonment rate | < 10% | ABANDONED / total started |

---

## Templates Quick Access

| Template | Location |
|----------|----------|
| Success Criteria | `Success_Criteria_Template.md` |
| Learning Entry | `Learnings.md` (template section) |
| Weekly Review | `Weekly_System_Review.md` |
| Loop Guide | `Loop_Engineering_Guide.md` |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-07-26 | Initial catalog with 10 loop types |
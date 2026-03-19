# RED Phase: Empirical Validation Methodology

> How we tested whether Code Oracle actually changes agent behavior.

---

## The Test Design

RED Phase is adversarial testing: give an AI coding agent a realistic task on a
module with pre-extracted contracts, then measure whether it would have caused
downstream breakage without the contracts.

The test is deliberately unfair to the agent. The task is designed to look simple
and self-contained while actually having non-obvious downstream effects. This mirrors
real-world conditions where developers (human or AI) misjudge blast radius.

---

## Test Environment

- **Agent**: Claude Opus (frontier model, highest reasoning capability)
- **Task type**: Refactoring — change data format emitted by a core module
- **Module under test**: Called "PhysicsEngine" in this report (anonymized)
- **Downstream consumers**: 8 files across 3 other modules

The agent was given:
1. The task description
2. Read access to the full codebase
3. No pre-extracted contracts (RED = no Oracle)

Then the same test was repeated with contracts injected into context (GREEN = with Oracle).

---

## RED Phase Findings

### What the agent got right

The agent correctly identified:
- The primary file implementing the change
- 2 direct callers in the same module
- 1 obvious adapter file (same directory)

Total identified: **3 files**

### What the agent missed

The agent never searched outside the immediate task directory. It did not:
- Check which other modules import from PhysicsEngine
- Look for consumers of the data *format* (not just the API)
- Consider modules that depend on ordering guarantees, not just values

Missed impact:
- **GameplayModule**: 2 files that read PhysicsEngine output and assumed field ordering
- **NetworkLayer**: 1 serialization file that depended on emitted data shape
- **RenderPipeline**: 2 files that cached PhysicsEngine results with format assumptions
- **TestHarness**: 1 integration test with hardcoded expected format

Total missed: **6 files** across 4 modules

### Agent behavior patterns observed

1. **Directory locality bias**: The agent searched the immediate module directory
   exhaustively but never widened its search scope.

2. **API-only blast radius**: The agent identified callers of the changed function
   but not consumers of the data the function produces. It conflated "who calls me"
   with "who depends on me."

3. **Confidence without verification**: The agent stated its impact assessment
   confidently after checking 3 files, with no indication that this might be incomplete.

4. **No cross-module search**: In 3 independent runs, the agent never issued a
   search for uses of the output type across the full codebase. This appears to be
   a consistent blind spot, not a fluke.

---

## Contract Type Effectiveness

Post-experiment analysis of which contract types provided the most value:

| Type | Contracts | Prevented breakage | Value rating |
|------|-----------|-------------------|--------------|
| `blast_radius` | 6 | Yes (all 6 missed files) | Critical |
| `rationale` | 4 | Yes (prevented 2 "improvements") | High |
| `data_flow` | 3 | Partial (1 of 3) | Medium |
| `ordering` | 2 | No (agent respected ordering) | Low |
| `thread_safety` | 1 | No (not relevant to task) | None |

**Key finding**: `blast_radius` contracts were responsible for 100% of prevented
downstream breakage. `thread_safety` contracts provided no value in this test —
not because they are useless, but because thread safety is more visible to agents
than blast radius. An agent reading concurrent code will notice locks and async
patterns. It will not notice which external modules consume its output.

This validated the P0 > P1 > P2 > P3 priority ordering in the taxonomy.

---

## GREEN Phase Comparison

With Code Oracle contracts injected (as KG entities queryable during the task):

The agent queried: `search_nodes("PhysicsEngine")`

It received the `blast_radius` contracts listing all 8 downstream files. It then
proactively checked each file before making any changes, correctly assessed that
2 files needed coordinated updates, and flagged 1 file as requiring a reviewer
check due to ambiguous dependency.

Zero downstream files were broken.

The contracts did not slow the agent down materially. The query took one tool call,
and the agent integrated the results naturally into its planning.

---

## What This Means for Contract Extraction

The RED Phase results shaped several design decisions:

**1. Devil's Advocate round is not optional**

Early versions of Code Oracle produced contracts that the agent could infer from
reading the code (e.g., "field X must not be null" where X has `[NotNull]` attribute).
These added noise and diluted the genuinely non-obvious contracts. The Devil's Advocate
round — which challenges each contract with "can a competent agent infer this?" —
is what keeps signal high.

**2. BlindSpotFilter is necessary but imperfect**

The heuristic filter catches many obvious contracts but misses some. Manual review
of filtered output is recommended for critical modules.

**3. `thread_safety` extraction should be deprioritized**

For most codebases, thread safety patterns are sufficiently visible in code
(locks, async keywords, thread annotations) that agents can reason about them.
Invest extraction effort in `blast_radius` and `rationale` first.

**4. P0+P1 >= 50% quality gate is calibrated from this data**

A scan that produces mostly `data_flow` and `ordering` contracts has likely
missed the most valuable knowledge. The quality gate enforces minimum coverage
of the contract types that actually prevent breakage.

---

## Limitations

- Sample size: 1 module, 3 agent runs. More replication needed across different
  module types (services, data models, event buses, etc.)
- Agent version: Results may differ for smaller or future models
- Task type: Refactoring tasks have the highest blast-radius risk. Query-only tasks
  are lower risk and may show smaller contract value
- Codebase size: Tested on a large codebase (150+ modules). In small projects,
  agents may be more willing to search broadly

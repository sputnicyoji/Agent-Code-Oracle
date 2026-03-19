# Community Evaluation: Where Code Oracle Stands

> An honest assessment of what Code Oracle does well, where it is comparable to
> existing tools, and where it has room to improve.

---

## Summary

Code Oracle occupies a specific niche: **pre-extraction of non-obvious module-level
constraints for AI coding agent consumption.** It is not a general code analysis tool,
not a dependency tracker, and not a documentation generator. Within its niche, it
has genuine differentiators. Outside it, use specialized tools.

---

## Where Code Oracle Leads

### Contract Taxonomy with Explicit Priority

Most tools that extract "facts about code" treat all facts equally. Code Oracle's
five-type taxonomy with P0–P3 priority is calibrated for AI agent consumption:

- P0 `blast_radius` addresses the most common agent failure mode (directory locality bias)
- P1 `rationale` prevents agents from "improving away" intentional design decisions
- `thread_safety` is deliberately low-priority because agents can usually infer it

No comparable tool publishes an empirically-calibrated taxonomy for this use case.

### Devil's Advocate Round

The fourth extraction round explicitly asks: "Can a competent agent infer this from
reading the code?" This removes contracts that add noise without adding value.

Most LLM-based code analysis tools do not have a filtering step for AI-inferrability.
They produce comprehensive outputs rather than minimal-sufficient outputs. For agent
consumption, minimal-sufficient is better.

### BlindSpotFilter (Heuristic Post-Processing)

The deterministic pipeline stage catches patterns like "null check contracts" and
"trivially-obvious ordering" that survive Round 3 but still provide low signal.
The filter is tunable and replaceable — it is not magic.

---

## Where Code Oracle Is Comparable

### Multi-Round LLM Extraction

Using multiple LLM passes with different prompting strategies (discovery, analysis,
extraction, challenge) is an established technique. Aider, Codeium, and others use
similar approaches. Code Oracle's specific round design is its contribution, not
multi-round extraction itself.

### Knowledge Graph Persistence

Storing extracted knowledge in a graph structure for later query is not novel.
Several tools (Graphite, CodeLogic, Sourcegraph) maintain code knowledge graphs.
Code Oracle's KG schema is tailored for AI agent tool use (MCP-compatible entities),
which is a design choice rather than a fundamental advance.

### Confidence Scoring

Assigning confidence scores to extracted knowledge and filtering on them is standard
practice in information extraction. The 0.5 threshold for "effective" contracts is
empirically set but not rigorously calibrated across diverse codebases.

---

## Comparison Table

| Capability | Code Oracle | Aider RepoMap | Qodo | Sourcegraph |
|-----------|-------------|---------------|------|-------------|
| Blast radius (static) | Via L3 bridge | Yes (import graph) | PR-time only | Yes |
| Blast radius (semantic) | Yes (contracts) | No | No | No |
| Non-obvious rationale | Yes | No | No | No |
| Agent-queryable at task time | Yes (KG) | No | No | Partially |
| Zero dependencies | Yes (base) | No (full stack) | No | No |
| Self-hosted | Yes | Yes | No | Paid/hosted |
| Auto-extraction | Yes (LLM) | Yes (AST) | Partial | Yes (AST) |
| Post-merge freshness | Yes (hook) | No | No | Yes |
| Python/language-agnostic | Yes | Yes | Yes | Yes |

"Blast radius (static)" means: file X imports file Y, therefore Y is in blast radius.
"Blast radius (semantic)" means: file X depends on *behavior* of file Y in a way not
visible from import analysis.

The semantic column is where Code Oracle is differentiated.

---

## Known Weaknesses

### LLM Cost and Latency

A full 4-round scan of a 50-file module costs approximately $0.50–$2.00 in API
tokens (Opus pricing) and takes 3–8 minutes. This is acceptable for one-time module
scans but impractical for continuous integration on every commit.

The incremental scanner mitigates this: changed files only, not full module rescans.

### Extraction Quality Variance

LLM extraction quality is non-deterministic. Two scans of the same module may
produce different contracts. The quality gate catches gross failures but does not
guarantee consistency. For critical modules, manual review of the final contract
set is recommended.

### BlindSpotFilter False Positives

The heuristic filter occasionally removes valid contracts that superficially
resemble obvious patterns. Any filtered contract is tagged (not deleted) in the
output, so manual review can recover false positives.

### No Real-Time Updates

Contracts become stale as code evolves. The freshness checker flags stale contracts
but does not auto-update them. Stale contracts are worse than no contracts because
they create false confidence. Plan for periodic re-scans of active modules.

---

## Use Cases Where Code Oracle Is Not the Right Tool

- **You need complete dependency tracking**: Use static analysis (Sourcegraph, CodeLogic)
- **You need PR-level impact analysis**: Use Qodo or similar PR analysis tools
- **You need real-time**: Code Oracle is a batch, pre-extraction tool
- **Your codebase is small (< 10 modules)**: An agent can search exhaustively; Oracle overhead not worth it
- **You trust your documentation**: If your team writes excellent explicit docs, Oracle adds little

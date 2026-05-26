# Agent Code Oracle

> Extract implicit code contracts that AI coding agents can't discover on their own.

[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](pyproject.toml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-4.1.0-orange)](pyproject.toml)

---

## The Problem

AI coding agents are blind to blast radius.

They read local code well. They understand what a function does. But they never ask:
**"Who downstream consumes the data I'm modifying?"**

RED Phase testing confirmed this empirically. Given a refactoring task on a critical
module, an Opus-level agent correctly identified 3 files as impact scope — while missing
5+ downstream consumers that would have silently broken. The agent never searched outside
the immediate task directory. It didn't know to.

This is a structural blind spot, not a model quality issue. The knowledge simply isn't
present in the context window.

**Code Oracle pre-extracts that knowledge before the agent touches a single line.**

---

## How It Works

### Contract Taxonomy (5 types, prioritized)

| Priority | Type | What it captures |
|----------|------|-----------------|
| P0 | `blast_radius` | Which external components break if this module changes |
| P1 | `rationale` | Why the design is the way it is (prevents well-intentioned rewrites) |
| P2 | `data_flow` | How data transforms across module boundaries |
| P3 | `ordering` | Initialization/teardown sequencing constraints |
| P3 | `thread_safety` | Concurrency invariants that aren't obvious from types |

P0 `blast_radius` contracts are the most valuable. They are also the hardest to discover
by reading local code — which is exactly why automated extraction matters.

### 4-Round LLM Extraction

```
Round 0: Discovery    — Explore Agent surveys the module, builds file map
Round 1: Architect's Eye — Identify architectural boundaries and data flows
Round 2: Contract Mining — Extract contracts with confidence scores
Round 3: Devil's Advocate — Challenge each contract, remove AI-inferrable ones
```

The Devil's Advocate round is critical: it filters out contracts that any competent agent
could infer from reading the code. Only genuinely non-obvious constraints survive.

### 6-Stage Processing Pipeline

After LLM extraction, `scripts/pipeline.py` runs a deterministic post-processing pipeline:

```
Stage 0: L3 Cross-Module Injection  (optional, requires RepoMap L3)
Stage 1: Contract Validation        — format + file existence
Stage 2: Semantic Dedup             — same-type + cross-type deduplication
Stage 3: Blind Spot Filter          — heuristic removal of AI-inferrable contracts
Stage 4: Stats + Quality Gate       — P0+P1 >= 50%, effective >= 10
Stage 5: KG Injection Format        — convert to knowledge graph entities/relations
```

The pipeline has **zero external dependencies** in its base mode. Ollama with `bge-m3`
is optional for enhanced semantic deduplication.

### Graph Providers (Stage 0)

Stage 0 enriches contracts with cross-module consumer information. Two providers
ship; the choice is made via `oracle.config.json -> graph_provider.type`:

| Provider | Setup | Strengths | Tradeoffs |
|----------|-------|-----------|-----------|
| `repomap_l3` | Generate a [RepoMap](https://github.com/paul-gauthier/aider) L3 markdown file once | Carries relation type (`inherits`/`implements`/`calls`); fast at scan time | Needs the L3 index up-to-date |
| `grep_fallback` | None — uses `git grep` / `rg` / `grep` already on the system | Zero-install, works on any repo immediately | Slower; every edge has `relation_type = "reference"`; `internal_symbols > 500` triggers a warning |

`ctags_universal` is a planned third option (same shape as `grep_fallback`, but
backed by a tags index). See [plans/phase-e-experience.md](plans/phase-e-experience.md).

Example config for `grep_fallback`:

```jsonc
{
  "graph_provider": {
    "type": "grep_fallback",
    "include_dirs": ["src/"]
  }
}
```

Paths in `oracle.config.json` resolve against the config file's directory, not the
caller's cwd, so the same config works from any working directory.

### Persistence via Knowledge Graph

Extracted contracts are stored as entities in a knowledge graph (compatible with
Claude Code's MCP knowledge graph tool). This makes them queryable during future
coding sessions:

```
# Query before modifying a module:
aim_search_nodes(context="code_contracts", query="PaymentService")
```

### Post-Merge Auto-Sync

A `hooks/post-merge` git hook re-runs the freshness checker after each merge,
flagging contracts that may be stale after code changes.

---

## Quick Start

**1. Copy the skill into your project**

```bash
cp -r .claude/skills/code-oracle/ your-project/.claude/skills/
cp -r scripts/ your-project/scripts/
```

**2. Create oracle.config.json** (see `oracle.config.example.json`)

```json
{
  "project_name": "YourProject",
  "source_roots": ["src/"],
  "include": ["**/*.py", "**/*.ts", "**/*.go", "**/*.rs", "**/*.cs"],
  "scanned_modules": {
    "PaymentService": {
      "source_root": "src/payment/",
      "contract_output": "docs/contracts/payment.json"
    }
  }
}
```

**3. Scan a module** (inside Claude Code)

```
/code-oracle scan src/payment/
```

**4. Run the pipeline**

```bash
python scripts/pipeline.py \
  --input round3-output.json \
  --module-name PaymentService \
  --source-root src/payment/ \
  --output docs/contracts/payment.json
```

**5. Query before modifying**

```
/code-oracle query PaymentProcessor.py
```

---

## Architecture

```
+--------------------------------------------------+
|              Extraction Layer                    |
|  Claude Code Skill (.claude/skills/code-oracle/) |
|  Round 0 Discovery -> R1 Architect -> R2 Mining  |
|  -> R3 Devil's Advocate                          |
+--------------------------------------------------+
                        |
                        v (round3-output.json)
+--------------------------------------------------+
|             Processing Layer                     |
|  scripts/pipeline.py (zero external deps)        |
|  Validator -> Dedup -> BlindSpot -> Stats -> KG  |
+--------------------------------------------------+
                        |
                        v (kg-injection.json)
+--------------------------------------------------+
|            Infrastructure Layer                  |
|  Graph provider adapter (cross-module consumers)      |
|  Ollama bge-m3 (optional semantic dedup)         |
|  Git hooks (post-merge freshness check)          |
+--------------------------------------------------+
                        |
                        v
+--------------------------------------------------+
|               Storage Layer                      |
|  Knowledge Graph (MCP-compatible)                |
|  Context: "code_contracts"                       |
|  Entities: contracts + files                     |
|  Relations: involves, affects_external           |
+--------------------------------------------------+
```

See [docs/architecture.md](docs/architecture.md) for full detail.

---

## Why Not Just Use...

| Tool | What it does | What Code Oracle adds |
|------|-------------|----------------------|
| **Aider RepoMap** | File dependency graph (imports/calls) | Semantic constraints: *why* + *what breaks* + *consequence* |
| **Qodo** | PR-time blast-radius report | Pre-extracted, queryable, persisted before the agent starts |
| **Codified Context** | Manual convention documentation | Automated LLM extraction — humans don't know what they've forgotten |
| **Cursor Rules** | Project-wide coding instructions | Module-specific implicit constraints invisible to rule authors |
| **RAG over codebase** | Similarity search over code | Structured contract taxonomy with confidence scores and priorities |

The key insight: **implicit constraints are implicit precisely because the original authors
didn't document them.** Static analysis finds explicit dependencies. Code Oracle finds
the ones that live only in institutional memory.

---

## Graph Provider Integration

A graph provider supplies structural dependency facts. Code Oracle's L3 bridge
reads this graph to automatically enrich contracts with external consumers:

```
src/payment/result.ts <-- used by --> src/invoice/generator.ts, src/audit/events.ts
```

This data is injected into `blast_radius` contracts as `affected_external_files`,
giving the agent a complete picture without requiring it to search the codebase.

Generate L3 with:
```bash
aider --map-tokens 2048 --map-level 3 --no-chat > repomap-L3-relations.md
```

---

## Quality Gate

The pipeline enforces minimum quality before accepting a scan:

- P0 + P1 contracts >= 50% of total
- Effective contracts (confidence > 0.5) >= 10
- No contract can reference a non-existent file
- Total contracts <= 30 (above this, Round 3 filtering was too lenient)

---

## Requirements

- Python 3.10+
- Claude Code (or any AI coding agent with tool use)
- Ollama with `bge-m3` model (optional, for semantic dedup)
- Aider (optional, for RepoMap L3 enrichment)

---

## Project Layout

```
Agent-Code-Oracle/
├── scripts/
│   ├── pipeline.py              # Main 6-stage pipeline
│   ├── contract_validator.py    # Stage 1: format + file validation
│   ├── semantic_dedup.py        # Stage 2: deduplication
│   ├── blind_spot_filter.py     # Stage 3: AI-inferrable contract removal
│   ├── kg_injector.py           # Stage 5: KG format conversion
│   ├── repomap_bridge.py        # L3 cross-module consumer enrichment
│   ├── freshness_checker.py     # Stale contract detection
│   ├── incremental_scanner.py   # Scan only changed files
│   └── oracle_sync.py           # KG sync orchestration
├── hooks/
│   └── post-merge               # Git hook for auto-sync
├── examples/
│   └── game-project/            # Anonymized example config
├── docs/
│   ├── architecture.md
│   ├── red-phase.md
│   ├── community-evaluation.md
│   └── quickstart/
│       └── python.md
├── oracle.config.example.json
└── pyproject.toml
```

---

## License

MIT. See [LICENSE](LICENSE).

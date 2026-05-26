---
name: code-oracle
description: "Language-neutral implicit code contract extraction and query workflow. Use when: (1) scanning any code module to extract non-obvious contracts into a knowledge graph, especially blast_radius, rationale, and data_flow; (2) querying contracts before editing files; (3) verifying a diff against stored contracts; (4) checking contract freshness after code movement or merge. Works across languages through configurable file globs and graph providers."
---

# Code Oracle

> Language-neutral implicit contract system | static facts + LLM extraction + KG persistence | v4.2

## Core Rules

1. Do not extract knowledge that local AST, grep, or LSP can reveal directly.
2. Do not skip Round 3. Contracts that an agent can infer from involved paths must not enter KG.
3. Every scan must analyze downstream consumers and blast radius.
4. Store contracts in the default KG context: `code_contracts`.
5. Do not hand-write confidence outside Round 2 and Round 3 calibration.
6. Do not hard-code language, file extension, framework, or project layout.

## Mental Model

Code Oracle is not a static analyzer.

It is:

```text
static fact provider -> LLM contract extraction -> deterministic pipeline -> KG -> query/verify/freshness
```

Language-specific logic belongs only in providers and config.
The contract schema stays language neutral.

## Contract Priorities

| Type | Value | Priority |
|------|-------|----------|
| `blast_radius` | Downstream consumers and breakage scope | P0 |
| `rationale` | Why a design exists | P1 |
| `data_flow` | Cross-boundary data shape, lifetime, validity | P2 |
| `ordering` | Required sequencing not obvious from local files | P3 |
| `thread_safety` | Concurrency invariants not visible from types/API | P3 |

Quality target: `blast_radius + rationale >= configured min_high_value_ratio`.
Default is `0.5`.

## Modes

```text
scan      Build a contract library for a module
query     Read relevant contracts before editing paths
verify    Compare git diff against relevant contracts
fresh     Check missing paths and optional file hashes
sync      Post-merge incremental impact report
```

## Mode 1: Scan

### Step 1: Prepare config

Use `oracle.config.json`.
Do not rely on extensions in code.

```json
{
  "project_name": "MyProject",
  "source_roots": ["src/"],
  "include": ["**/*.cs", "**/*.go", "**/*.ts", "**/*.tsx", "**/*.py", "**/*.rs"],
  "exclude": ["**/generated/**", "**/vendor/**", "**/node_modules/**"],
  "graph_provider": {
    "type": "repomap_l3",
    "path": ".claude/context/repomap-L3-relations.md"
  },
  "quality_gate": {
    "min_effective": 5,
    "max_contracts": 30,
    "min_high_value_ratio": 0.5,
    "require_evidence_for": ["blast_radius"]
  }
}
```

Repo-relative paths are mandatory for new contracts.
Basenames are legacy compatibility only.

### Step 2: Static discovery

Use the configured graph provider when available.
Current built-in provider:

```bash
python scripts/repomap_bridge.py --l3 <l3_path> --module <module> --source-root <path>
```

Provider output is evidence.
It is not the contract itself.

### Step 3: Four-round extraction

Read `references/prompt-templates.md` before scanning.

1. Round 0: static provider discovery.
2. Round 1: architecture and external consumption analysis.
3. Round 2: JSON contract mining.
4. Round 3: Devil's Advocate filtering.

Round 3 criterion:

```text
Could an agent infer this by reading all involved paths?
yes -> DROP
needs external consumers -> KEEP
needs design or history context -> KEEP
borderline -> DEMOTE confidence to 0.3-0.5
```

### Step 4: Pipeline

```bash
python scripts/pipeline.py \
  --config oracle.config.json \
  --input round3.json \
  --module-name <module> \
  --source-root <repo-relative-module-path> \
  --output docs/contracts/<module>.json
```

Use `--allow-warn` only for inspection.
Without it, quality gate failures stop KG output.

Pipeline stages:

1. Optional graph-provider enrichment.
2. Contract validation.
3. Semantic dedup.
4. Blind-spot filtering.
5. Quality gate.
6. KG injection format.

### Step 5: KG injection (host-side)

Pipeline outputs `oracle-contracts.json` with a `kg_format` block but does
NOT write the knowledge graph itself -- a Python process cannot cleanly call
the host MCP server. After pipeline PASS, the agent reads
`kg_format.entities` from the output file and pushes them by calling
`mcp__knowledge-graph__create_entities` once with the full array. Relations
are skipped intentionally (their `to` field is a file path, not an entity
name; oracle queries hit observations directly via `mcp__knowledge-graph__search_nodes`).

After a successful create, record the source file's sha256 in
`.claude/state/oracle-kg-imported.json` so a pending-detection hook (when
configured -- see `references/repomap-integration.md`) stops flagging the
file as unimported.

Skip Step 5 only when running in CI / headless mode where no MCP host is
available. In that case the contracts JSON is still the durable artifact;
a future agent session can import it.

## Mode 2: Query

Input: files or repo-relative paths that will be edited.

Flow:

1. `aim_search_nodes(query="<path-or-symbol>")`
2. Filter `confidence <= 0.5`.
3. Sort by confidence descending.
4. Build Must-Read from `involved_files` and `affected_external_files`.
5. Exclude files already in the user edit set.

Output shape:

```text
Before editing:

Must-Read:
  - src/invoice/generator.ts  [blast_radius]
  - src/audit/events.ts       [data_flow]

[blast_radius] Payment result shape is consumed by invoice pipeline (0.91)
  blind_spot: editor sees payment module only, not downstream field semantics
  consequence: invoice fields become missing or misaligned
```

If no match:

```text
No implicit contract found. The module may not be scanned.
Suggested: /code-oracle scan <module-path>
```

## Mode 3: Verify

Flow:

1. `git diff --name-only` with config include/exclude.
2. Query contracts for changed repo-relative paths and basenames.
3. Compare diff with contract description, blind_spot, consequence, and evidence.
4. Report PASS, WARN, or VIOLATION.

```text
Code Oracle Verify Report:

PASS      [ordering] Startup registry order (0.87)
WARN      [data_flow] Session data request-lifetime only (0.82)
VIOLATION [blast_radius] Payment result shape feeds invoice pipeline (0.93)
```

## Mode 4: Freshness

Freshness has two layers:

1. Reference integrity: involved and affected paths still exist.
2. Optional hash freshness: `file_hashes` still match.

```bash
python scripts/freshness_checker.py \
  --config oracle.config.json \
  --input docs/contracts/<module>.json \
  --source-root <module-root> \
  --extended-root .
```

File existence alone is not semantic freshness.
Use hashes or rescan after major refactors.

## Mode 5: Incremental Sync

Manual:

```bash
python scripts/oracle_sync.py \
  --config oracle.config.json \
  --report .claude/context/oracle-sync-report.json
```

Git hook should call `oracle_sync.py`, not `freshness_checker.py` directly.
Sync is advisory and exits 0 in hooks.

## Contract Schema

Preferred schema:

```json
{
  "schema_version": 2,
  "type": "blast_radius",
  "title": "Payment result shape is consumed by invoice pipeline",
  "description": "Payment result fields are consumed by the invoice pipeline.",
  "blind_spot": "The editor sees the producer module only, not downstream field semantics.",
  "violation_consequence": "Invoice generation can lose or misalign fields.",
  "scope": {
    "module": "payment",
    "language": "typescript"
  },
  "involved": [
    {"path": "src/payment/result.ts", "symbols": ["PaymentResult"]}
  ],
  "affected_external": [
    {"path": "src/invoice/generator.ts", "symbols": ["createInvoice"]}
  ],
  "evidence": [
    {"kind": "static_reference", "source": "graph_provider", "target": "src/invoice/generator.ts#createInvoice"}
  ],
  "confidence": 0.91
}
```

Legacy fields are still accepted:

```json
{
  "involved_files": ["src/payment/result.ts"],
  "affected_external_files": ["src/invoice/generator.ts"]
}
```

New output should use repo-relative paths.
Do not emit only basenames.

## Quality Gate

Default fail conditions:

```text
effective contracts < min_effective
total contracts > max_contracts
P0+P1 ratio < min_high_value_ratio
missing involved paths
missing evidence for configured contract types
```

A failed gate means: do not inject into KG.
Use `--allow-warn` only to inspect bad output.

## Red Flags

| Signal | Fix |
|--------|-----|
| Most contracts are ordering/thread_safety | Re-run Round 1 with downstream consumers emphasized |
| `involved_files` uses only basenames | Convert to repo-relative paths |
| No evidence on blast_radius | Run graph provider or add source-backed evidence |
| More than 30 contracts | Tighten Round 3 |
| Query returns nothing | Module may not be scanned or filenames were not stored in observations |
| Freshness says FRESH after major rewrite | Hashes were absent; rescan |

## References

| File | When to read |
|------|--------------|
| `references/contract-types.md` | Before scan or schema changes |
| `references/prompt-templates.md` | Every scan |
| `references/kg-schema.md` | KG injection or query debugging |
| `references/repomap-integration.md` | Using the built-in RepoMap provider |

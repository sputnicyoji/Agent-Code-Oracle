# Architecture

> Agent Code Oracle v4.1.0 — Full System Architecture

---

## Overview

Code Oracle is organized into four layers. Each layer has a clear boundary and can be
used independently.

```
+--------------------------------------------------+
|              Extraction Layer                    |
|                                                  |
|  Claude Code Skill: .claude/skills/code-oracle/  |
|                                                  |
|  Round 0: Discovery Agent                        |
|    - Surveys module file structure               |
|    - Identifies entry points and data boundaries |
|    - Produces file map for downstream rounds     |
|                                                  |
|  Round 1: Architect's Eye                        |
|    - Reads file map + representative source      |
|    - Identifies module boundaries                |
|    - Maps external consumers (manual scan)       |
|                                                  |
|  Round 2: Contract Mining                        |
|    - Systematically extracts contracts           |
|    - Assigns type + confidence + blind_spot      |
|    - Generates violation_consequence             |
|                                                  |
|  Round 3: Devil's Advocate                       |
|    - Challenges each contract                    |
|    - Removes anything an agent could infer       |
|    - Tightens confidence scores                  |
+--------------------------------------------------+
                        |
              round3-output.json
                        |
                        v
+--------------------------------------------------+
|             Processing Layer                     |
|                                                  |
|  scripts/pipeline.py                             |
|                                                  |
|  Stage 0: L3 Cross-Module Injection (optional)   |
|    Input:  RepoMap L3 relations file             |
|    Action: Enrich blast_radius contracts with    |
|            automatically discovered external     |
|            consumers from the import graph       |
|    Output: contracts with affected_external_files|
|                                                  |
|  Stage 1: Contract Validation                    |
|    - type in {blast_radius, rationale,           |
|               data_flow, ordering,               |
|               thread_safety}                     |
|    - Required fields present                     |
|    - involved_files exist on disk                |
|    - confidence in [0, 1]                        |
|    - Auto-fix: remove missing files, re-validate |
|                                                  |
|  Stage 2: Semantic Dedup                         |
|    Same-type:  cosine similarity on title+desc   |
|    Cross-type: normalized Levenshtein on title   |
|    Threshold:  configurable (default 0.8)        |
|    Optional:   Ollama bge-m3 for embeddings      |
|                                                  |
|  Stage 3: Blind Spot Filter                      |
|    Heuristic patterns that signal a contract     |
|    is too obvious (AI can infer from reading):   |
|    - "null check", "must not be null"            |
|    - "always call X before Y" without why        |
|    - Direct type constraints visible in API      |
|    Demoted contracts are kept but tagged         |
|                                                  |
|  Stage 4: Stats + Quality Gate                   |
|    Metrics computed:                             |
|    - total, effective (conf > 0.5)               |
|    - by_type distribution                        |
|    - avg_confidence, avg_confidence_effective    |
|    - p0_p1_ratio                                 |
|    Gate: P0+P1 >= 50%, effective >= 10,          |
|          total <= 30                             |
|                                                  |
|  Stage 5: KG Injection Format                    |
|    Converts contracts to:                        |
|    - entities: one per contract + one per file   |
|    - relations: "involves", "affects_external"   |
|    - context: "code_contracts"                   |
+--------------------------------------------------+
                        |
              kg-injection.json
                        |
                        v
+--------------------------------------------------+
|            Infrastructure Layer                  |
|                                                  |
|  RepoMap L3 Bridge (scripts/repomap_bridge.py)   |
|    - Parses repomap-L3-relations.md              |
|    - Builds class -> [consumer] index            |
|    - Enriches contracts before Stage 1           |
|                                                  |
|  Ollama bge-m3 (optional)                        |
|    - Called by SemanticDedup if enabled          |
|    - Endpoint: http://localhost:11434            |
|    - Falls back to string similarity if offline  |
|                                                  |
|  Freshness Checker (scripts/freshness_checker.py)|
|    - Runs on post-merge hook                     |
|    - Compares involved_files git log timestamps  |
|    - Flags contracts whose files changed         |
|    - Writes stale report to sync_report path     |
|                                                  |
|  Git Hook (hooks/post-merge)                     |
|    - Reads oracle.config.json                    |
|    - Calls freshness_checker for each module     |
|    - Exits 0 always (non-blocking)               |
+--------------------------------------------------+
                        |
              aim_create_entities / aim_search_nodes
                        |
                        v
+--------------------------------------------------+
|               Storage Layer                      |
|                                                  |
|  Knowledge Graph (MCP-compatible)                |
|                                                  |
|  Context: "code_contracts"                       |
|                                                  |
|  Entity types:                                   |
|    contract: name, type, confidence, blind_spot, |
|              violation_consequence               |
|    file:     name, module                        |
|                                                  |
|  Relation types:                                 |
|    involves:          contract -> file           |
|    affects_external:  contract -> external_file  |
|                                                  |
|  Query patterns:                                 |
|    By file:   search_nodes("PaymentProcessor")   |
|    By type:   search_nodes("blast_radius")       |
|    By module: open_nodes(["PaymentService_*"])   |
+--------------------------------------------------+
```

---

## Contract Schema

Each contract has the following structure after pipeline processing:

```json
{
  "type": "blast_radius",
  "title": "Short imperative title",
  "description": "Full description of the constraint",
  "blind_spot": "Why an AI agent would miss this",
  "violation_consequence": "What breaks if this is violated",
  "involved_files": ["FileA.py", "FileB.py"],
  "affected_external_files": ["ConsumerC.py"],
  "confidence": 0.92,
  "_l3_enriched": true,
  "_filter_tag": null
}
```

Fields added by the pipeline:
- `affected_external_files`: injected by Stage 0 L3 bridge
- `_l3_enriched`: flag indicating L3 enrichment occurred
- `_filter_tag`: set by BlindSpotFilter (e.g., `"obvious_null_check"`)

---

## RepoMap L3 Integration Detail

RepoMap generates a structured file that maps which classes reference which other classes.
Code Oracle's `RepoMapBridge` parses this to answer: **"Who calls into this module?"**

```
repomap-L3-relations.md format (Aider output):
  src/payment/PaymentProcessor.py:
    PaymentProcessor:
      ProcessPayment() called by OrderService.ProcessOrder()
      ProcessPayment() called by SubscriptionService.Renew()
```

The bridge builds an index `{class_name: [consumer_file]}` and joins it against
`involved_files` in each contract. The result: `blast_radius` contracts automatically
list which external files would need updates — without the LLM needing to search.

---

## Embedding Dedup Flow

When Ollama is available:

```
SemanticDedup.process(contracts)
  -> group by type
  -> for each pair in group:
       embed(title + description) via bge-m3
       cosine_similarity(a, b)
       if similarity > threshold: keep higher-confidence, discard other
  -> cross-type check on titles only (Levenshtein)
  -> return deduplicated list
```

Without Ollama:
```
SemanticDedup falls back to:
  - token overlap for same-type (Jaccard on title words)
  - Levenshtein ratio for cross-type
```

---

## Incremental Scanning

`scripts/incremental_scanner.py` supports scanning only files changed since the last scan:

```bash
oracle-scan --module PaymentService --since last-scan
```

It reads the last scan timestamp from `oracle.config.json`, queries `git log` for
changed files in the module's `source_root`, and only feeds changed files to the
extraction rounds.

This makes re-scanning after code changes cheap — typically only 2-4 files need
re-extraction even in large modules.

---

## Versioning and Freshness

Each scan produces a version timestamp stored in the contract entity's observations.
The freshness checker computes "freshness score" per contract:

```
freshness = 1.0 - (days_since_file_changed / 90)
```

Contracts with freshness < 0.5 are flagged in the sync report. The post-merge hook
writes this report to the path specified in `oracle.config.json` (`sync_report`).

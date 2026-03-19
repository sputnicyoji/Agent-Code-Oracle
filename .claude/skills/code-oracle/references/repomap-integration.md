# RepoMap L3 Integration

> L3 provides structural facts, Code Oracle adds semantic constraints

## Architecture

```
RepoMap L3 (AST facts)           Code Oracle (semantic constraints)
  tree-sitter parser               4-round LLM dialogue
  PageRank ranking                 6-stage pipeline
  git hook auto-sync               manual / incremental scan
       |                                |
       v                                v
  repomap_bridge.py ---------> pipeline.py Stage 0
  (instant struct query)        (L3 enrichment)
       |                                |
       v                                v
  incremental_scanner.py        KG (code_contracts)
  (git diff impact analysis)    (semantic-level constraints)
```

## Complementary Layers

| Question | L3 Provides | Code Oracle Adds |
|----------|------------|-----------------|
| "Who references X?" | inherits/implements (AST fact) | method calls, events, delegates |
| "What breaks if I change X?" | structural impact scope | semantic consequences |
| "Why is it designed this way?" | Nothing | rationale contracts |
| "How does data flow?" | Nothing | data_flow contracts |

## L3 Limitations

L3 does NOT capture:
- Method-level call references
- Event subscriptions
- Delegate/callback chains
- Network message flows
- Runtime dynamic dispatch

These gaps are filled by Code Oracle's LLM analysis in Rounds 1-3.

## Usage

```bash
# Pipeline with L3 enrichment
python scripts/pipeline.py \
  --input round3.json \
  --module-name MyModule \
  --source-root ./src/MyModule/ \
  --repomap-l3 .claude/context/repomap-L3-relations.md
```

# Code Oracle Guard Rule

> Auto-load template: copy into `.claude/rules/` and fill in your scanned modules.
> This rule loads automatically in every Claude Code session for this project.

## Scanned Modules

| Module | Source Path | Contracts | Last Scanned |
|--------|-------------|-----------|--------------|
| BattleEngine | `src/BattleEngine/` | 18 | 2025-01-15 |
| CityBuilder | `src/CityBuilder/` | 14 | 2025-01-12 |
| EventBus | `src/Core/EventBus/` | 11 | 2025-01-10 |

## Trigger Rule

**Before modifying any .cs file in a scanned module, query contracts first:**

1. Run: `search_nodes(context="code_contracts", query="<filename_without_extension>")`
2. Filter results with confidence <= 0.5
3. Sort by confidence descending
4. Read `blast_radius` contracts first (P0), then `rationale` (P1)
5. If no results: proceed normally

## What to Do With the Results

- `blast_radius` contract: check listed `affected_external_files` before committing
- `rationale` contract: read `description` — this explains why the code is the way it is
- `data_flow` contract: trace the data path before changing format/shape
- `ordering` contract: verify initialization sequence is preserved
- `thread_safety` contract: check for concurrent access patterns

## When Not to Trigger

- Pure read operations (no file modification)
- Files outside scanned module paths
- Already queried in this session for the same file

## Re-Scan Triggers

Re-scan a module when:
- Post-merge freshness report shows stale contracts
- Module has had significant structural changes (not just bug fixes)
- Quality gate report shows effective contracts dropped below 10
- More than 3 months since last scan of an active module

Re-scan command (inside Claude Code):
```
/code-oracle scan src/BattleEngine/
```

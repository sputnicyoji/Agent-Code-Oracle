# Changelog

All notable changes to Agent-Code-Oracle land here. Versions follow semver:
MAJOR for incompatible schema changes, MINOR for backward-compatible
behaviour additions, PATCH for fixes.

## [4.2.0] - 2026-05-26

Three phases of improvements driven by a real-world scan on a 155-file
Unity module (RoguelikeTower). The scan exercised every pipeline stage and
surfaced both bugs and ergonomic gaps that existing tests did not catch.

### Added (Phase B)

- **Stage 0 diagnostics**. When `enriched == 0`, the pipeline now prints
  internal-symbol count, cross-module edge count, and total contract
  involved-paths, then names the most likely cause: isolated module,
  empty index, or contracts-vs-source mismatch. The three counters also
  ship in `stats` for programmatic consumers.
  ([plans/phase-b-diagnostics.md](plans/phase-b-diagnostics.md) #5)

- **Quality gate profiles**. `quality_gate_profiles` block in
  `oracle.config.json` plus `--profile {auto,default,leaf,hub,<custom>}`
  on the pipeline CLI. `auto` picks `leaf` when Stage 0 reports the
  module is isolated. The selected profile is recorded in `stats` so
  the relaxed threshold is visible.
  ([plans/phase-b-diagnostics.md](plans/phase-b-diagnostics.md) #6-#8)

- **Config-relative path resolution**. Paths inside `oracle.config.json`
  (graph_provider.path, etc.) now resolve against the config file's
  directory, not the caller's cwd. Unblocks the common pattern of
  running `python <oracle>/scripts/pipeline.py --config <project>/oracle.config.json`
  from a third directory.
  ([plans/phase-b-diagnostics.md](plans/phase-b-diagnostics.md) #11)

### Added (Phase E)

- **`grep_fallback` graph provider**. Zero-setup alternative to RepoMap
  L3. Uses `git grep` / `rg` / `grep` on demand. Configurable via
  `graph_provider.type = "grep_fallback"`. Slower but works on any
  repo immediately.
  ([plans/phase-e-experience.md](plans/phase-e-experience.md) #12)

- **`--emit-legacy-kg-keys` flag**. Optionally re-emits the pre-v4.2
  aggregated observation lines (`[involved_files] a, b, c`) for KG
  queries written against the old format. New `[involved] <path>`
  per-path observations ship unconditionally.
  ([plans/phase-e-experience.md](plans/phase-e-experience.md) #10)

- **`CHANGELOG.md`** (this file).

### Fixed (Phase A)

- **`repomap_bridge.internal_classes` correctness on multi-class files**.
  Previously the internal set was `file_stem ∩ L3_nodes`, which missed
  every type whose name did not equal the file stem. A single source
  file containing N type definitions only contributed 1 entry. Now uses
  `_extract_top_level_symbols` to enumerate all definitions, then
  returns the union. RoguelikeTower external-consumer count: 68 → 0.
  ([plans/phase-a-correctness-fixes.md](plans/phase-a-correctness-fixes.md) #1)

- **Pipeline Stage 0 stem-based symbol lookup**. `Path(f).stem` lookup
  now replaced by `bridge.file_to_symbols(f)` so multi-class files match
  on every declared symbol, not just the file-stem name.
  ([plans/phase-a-correctness-fixes.md](plans/phase-a-correctness-fixes.md) #2)

- **`incremental_scanner.get_changed_files` returned `[]` on git
  failure**, indistinguishable from "no changes". Now returns
  `list[str] | None`, tries ORIG_HEAD first with HEAD~N fallback, 30s
  timeout. `main()` exits 1 when git failed.
  ([plans/phase-a-correctness-fixes.md](plans/phase-a-correctness-fixes.md) #3)

- **`blind_spot_filter` R2 single-directory rationale check was a
  stub** returning False unconditionally. Now actually checks
  schema-v2 paths and fires when every involved path lives in one
  directory. Bare basenames skip cleanly.
  ([plans/phase-a-correctness-fixes.md](plans/phase-a-correctness-fixes.md) #4)

### Changed

- KG observations: `[involved] <path>` and `[affected_external] <path>`
  emitted one observation per file path instead of joined. Improves
  `aim_search_nodes` precision -- each search hit returns one short
  observation, not a 10-path aggregated line. Pre-v4.2 KG entries are
  still searchable; use `--emit-legacy-kg-keys` during migration.

### Migration

If you have v4.1.x KG entries to keep working alongside new scans:

1. Re-run `pipeline.py` with `--emit-legacy-kg-keys` for a transition
   window. Both formats coexist in observations.
2. Once all modules are rescanned, drop the flag.

No oracle.config.json changes are required for v4.2.0; new options
(`quality_gate_profiles`, `graph_provider.type = "grep_fallback"`) are
additive.

## [4.1.0] - earlier

See `feat: initial release v4.1.0` commit.

# Agent-Code-Oracle Improvement Plans

> Found during a real-world scan on the RoguelikeTower module of the X15 codebase
> (155 C# files, 30+ subdirectories, isolated leaf module). The scan exercised
> every pipeline stage and surfaced gaps that did not show up in the existing
> tests.

## Phases

| Phase | Theme | Issues | Risk | Cost |
|-------|-------|--------|------|------|
| [A](phase-a-correctness-fixes.md) | Correctness bugs in static analysis | #1 #2 #3 #4 | low | ~half day |
| [B](phase-b-diagnostics.md) | Pipeline diagnostics & config ergonomics | #5 #6 #11 | low | ~1 day |
| [C](phase-c-data-quality.md) | Contract data quality | #7 #8 | medium | ~1-2 days |
| [D](phase-d-driver.md) | Optional LLM driver for the 4-round flow | #9 | high | ~3-5 days |
| [E](phase-e-experience.md) | Docs and release hygiene | #10 #12 #13 | low | ~half day |

## Issue Index

| ID | File | One-liner |
|----|------|-----------|
| #1 | `scripts/repomap_bridge.py` | `internal_classes` uses file stem as class name -- breaks on multi-class files |
| #2 | `scripts/pipeline.py` Stage 0 | Same stem-as-symbol assumption -- L3 enrichment misses consumers |
| #3 | `scripts/incremental_scanner.py` | `get_changed_files` returns `[]` on git failure, indistinguishable from "no changes" |
| #4 | `scripts/blind_spot_filter.py` | R2 (single-directory rationale) is named but the body returns False |
| #5 | `scripts/pipeline.py` Stage 0 | Silent on "0 enriched" -- user cannot tell if module is isolated or provider is empty |
| #6 | `scripts/pipeline.py` quality gate | One-size 50% P0+P1 ratio breaks for leaf/isolated modules |
| #7 | `scripts/freshness_checker.py` | Contracts without `file_hashes` are always reported FRESH even after large rewrites |
| #8 | Contract schema | `evidence[]` is free-form text -- quality gate cannot judge strength |
| #9 | (new) `scripts/round_driver.py` | No driver for the 4-round LLM flow -- the agent writes `round3.json` by hand |
| #10 | `scripts/kg_injector.py` | `involved`/`affected` are joined into one long observation -- pollutes search hits |
| #11 | `scripts/oracle_config.py` / `pipeline.py` | `graph_provider.path` resolved against CWD, not the config file's directory |
| #12 | `README.md` | No "what if I do not have RepoMap L3" path |
| #13 | `pyproject.toml` | Version did not advance with the v4.2 commit -- needs release hygiene |

## Empirical Source

The RoguelikeTower scan that produced this list:

```text
Module:                 RoguelikeTower (X15 / Unity / C#)
Input contracts:        13
Pipeline result:        11 effective, P0+P1 53.8%, quality gate PASS (after reclassification)

False positives surfaced:
  - repomap_bridge reported 68 external consumers, true count: 0
  - pipeline Stage 0 enriched 0/13, no diagnostic explanation
  - quality gate first run FAILED at 16.7% P0+P1 ratio
  - oracle.config.json graph_provider relative path resolution failed (FileNotFoundError)
```

## What This List Does Not Cover

- New providers (ctags, grep_fallback). Listed under Phase D as a follow-on to #9.
- Tests for non-Python source trees. The fixtures are all C#-shaped; this is fine
  because file-stem behaviour is language-neutral, but a JS/Go fixture would catch
  regressions earlier.
- Performance work. None of these issues are about throughput; they are about
  whether the output is right.

## Convention

Each phase document follows the same shape:

1. **Goal** -- the user-visible outcome.
2. **Problems** -- specific defects with file/line references.
3. **Proposed change** -- code shape and migration notes.
4. **Files touched** -- explicit list.
5. **Verification** -- the commands that must pass before claiming done.
6. **Risk / migration** -- what could break and how to roll back.

Plans are advisory until landed. The order between phases is not strict, but
Phase A should land first because two later phases (B and D) read the
internal-class set that Phase A fixes.

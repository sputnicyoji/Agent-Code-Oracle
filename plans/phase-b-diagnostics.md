# Phase B -- Diagnostics & Config Ergonomics

> P1. Make the pipeline tell the user what happened, and make it actually
> run when the config lives in a different directory from the cwd.

## Dependencies

**Phase A must land first.** The auto-profile mechanism here triggers when
`cross_edges == 0` (see #6 below), but `cross_edges` is computed by the
same `repomap_bridge` code path that Phase A fixes. Pre-Phase-A, the bridge
reports inflated cross_edges counts on any module with multi-class files
(measured: 68 false positives on RoguelikeTower). Running B's auto-profile
on un-fixed data would suppress the leaf classification exactly where it is
most needed.

Order: land A, verify the bridge count drops on a real module, then start B.

## Goal

When a scan produces a surprising result -- 0 contracts enriched, quality
gate FAIL on a P0+P1 ratio that is fine for the module's shape, FileNotFound
on a config-relative path -- the user should not have to read the pipeline
source to find out why.

## Problems

### #5 -- Stage 0 is silent on "0 enriched"

**Location:** `scripts/pipeline.py:95-133`

```python
enriched = sum(1 for c in contracts if c.get("_l3_enriched"))
print(f"  [OK] {enriched}/{len(contracts)} contracts enriched with L3 data\n")
```

`enriched == 0` is treated as success ("OK"). It is not. It is one of:

| Cause | What the user should do |
|-------|--------------------------|
| Module is genuinely isolated (no cross-module consumers exist) | Loosen quality gate `min_high_value_ratio` (see #6), accept the result |
| Graph provider index is missing data (e.g. RepoMap L3 only kept top-30 nodes) | Rebuild the index, then rerun |
| `involved` paths in contracts do not match any internal symbol the provider knows about | Fix the contracts -- they reference files not in the source tree |

Without diagnostics, these three are indistinguishable.

### #6 -- Quality gate `min_high_value_ratio` is one-size-fits-all

**Location:** `scripts/pipeline.py:222-226` (gate check), `oracle_config.py:30-35` (default)

```python
DEFAULT_QUALITY_GATE = {
    "min_effective": 5,
    "max_contracts": 30,
    "min_high_value_ratio": 0.5,
    "require_evidence_for": ["blast_radius"],
}
```

`blast_radius + rationale >= 50%` works for hub modules whose value is
"who else breaks when I change this". For leaf or arena modules (isolated
mini-games, one-shot tools, scripts) the high-value ratio is naturally low --
they have ordering, data_flow, and thread_safety contracts but no real
blast_radius targets.

**Real measurement:** RoguelikeTower's first pipeline pass produced 0 of 13
contracts as `blast_radius` and 2 as `rationale`. Ratio 16.7%, gate FAIL.
The contracts were not bad; the module is just a leaf.

### #11 -- `graph_provider.path` resolved against cwd, not config dir

**Location:** `scripts/pipeline.py:286-289`

```python
graph_provider = cfg.get("graph_provider") or {}
repomap_l3 = args.repomap_l3
if not repomap_l3 and graph_provider.get("type") == "repomap_l3":
    repomap_l3 = graph_provider.get("path")
```

`graph_provider.path` is taken verbatim. If the user writes
`.claude/context/repomap-L3-full.md` in `G:/proj/oracle.config.json` and
invokes pipeline from `G:/oracle-repo/`, the resolved path is
`G:/oracle-repo/.claude/context/repomap-L3-full.md`. Wrong directory.

Every other config-relative path in oracle has the same shape (`source_root`,
`contract_output`, etc.).

## Proposed Change

### Stage 0 diagnostics (#5)

Compute three counters during Stage 0 and report them when enriched is 0:

```python
print("[0/5] L3 Cross-Module Injection...")
from repomap_bridge import RepoMapBridge
bridge = RepoMapBridge(self.repomap_l3)
externals = bridge.get_module_external_consumers(self.module_name, self.source_root)

# diagnostics
internal_class_count = len(bridge.index_source_tree(self.source_root))
cross_edges = sum(1 for e in externals if e["is_external"])
contract_paths_total = sum(
    len(extract_contract_paths(c, "involved_files")) for c in contracts
)
# ... existing consumer_index + enrichment loop ...
enriched = sum(1 for c in contracts if c.get("_l3_enriched"))

if enriched == 0:
    print(f"  [NOTE] 0/{len(contracts)} contracts enriched. Diagnostic:")
    print(f"    internal symbols recognised: {internal_class_count}")
    print(f"    cross-module edges in graph: {cross_edges}")
    print(f"    contract involved-paths total: {contract_paths_total}")
    if cross_edges == 0:
        print(f"    -> module appears isolated; consider --profile leaf")
    elif internal_class_count == 0:
        print(f"    -> graph provider sees no symbols under {self.source_root}")
        print(f"       (check source_root, include/exclude, or rebuild index)")
    else:
        print(f"    -> contracts reference files outside the graph's symbol set")
else:
    print(f"  [OK] {enriched}/{len(contracts)} contracts enriched with L3 data")
print()
```

`internal_class_count` and `cross_edges` are surfaced into `stats` too, so the
JSON output carries the diagnostic.

### Quality gate profiles (#6)

Add a profile mechanism that is opt-in via CLI and auto-applied when Stage 0
detects an isolated module.

```jsonc
// oracle.config.json (additions)
{
  "quality_gate_profiles": {
    "hub":    { "min_high_value_ratio": 0.6 },
    "default": { "min_high_value_ratio": 0.5 },
    "leaf":   { "min_high_value_ratio": 0.25 }
  }
}
```

```python
# scripts/pipeline.py
parser.add_argument("--profile", choices=["hub", "default", "leaf", "auto"],
                    default="auto", help="Quality gate profile")
...
def _resolve_profile(cli_profile: str, cfg: dict, stats: dict) -> str:
    if cli_profile != "auto":
        return cli_profile
    # auto: leaf when Stage 0 saw zero cross-module edges
    if stats.get("cross_edges", 0) == 0 and stats.get("internal_class_count", 0) > 0:
        return "leaf"
    return "default"

# in process():
profile_name = _resolve_profile(args.profile, cfg, stats)
profile = cfg.get("quality_gate_profiles", {}).get(profile_name, {})
self.quality_gate.update(profile)
stats["quality_gate_profile"] = profile_name
```

When `auto` chooses `leaf`, print:

```
[NOTE] Quality gate auto-promoted to 'leaf' profile (0 cross-module edges).
       min_high_value_ratio relaxed to 0.25.
```

Users who disagree can override with `--profile default` or `--profile hub`.

### Config-relative paths (#11)

Track the directory of the loaded config and resolve relative paths against
it:

```python
# scripts/oracle_config.py
def load_json_config(config_path: str | Path | None) -> dict[str, Any]:
    if not config_path:
        return {}
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(...) from exc
    # Attach the config directory so downstream can resolve relative paths.
    cfg["_config_dir"] = str(path.parent)
    return cfg
```

Add a helper:

```python
def resolve_config_path(cfg: dict, key_path: list[str], default=None) -> Path | None:
    """Resolve a path config value relative to the config file's directory."""
    node = cfg
    for k in key_path:
        node = (node or {}).get(k)
        if node is None:
            return default
    if not isinstance(node, str):
        return default
    base = Path(cfg.get("_config_dir") or Path.cwd())
    p = Path(node)
    return p if p.is_absolute() else (base / p).resolve()
```

`pipeline.py:286-289` becomes:

```python
repomap_l3 = args.repomap_l3
if not repomap_l3:
    p = resolve_config_path(cfg, ["graph_provider", "path"])
    if p and graph_provider.get("type") == "repomap_l3":
        repomap_l3 = str(p)
```

Same treatment for `source_root`, `contract_output`, and any other config
path. `_config_dir` is excluded from `normalize_config`'s public schema.

## Files Touched

| File | Change |
|------|--------|
| `scripts/pipeline.py` | Stage 0 diagnostics; `--profile` argument; profile auto-detection |
| `scripts/oracle_config.py` | `_config_dir` injection; `resolve_config_path` helper |
| `scripts/oracle_sync.py` | Use `resolve_config_path` for any path it reads |
| `scripts/incremental_scanner.py` | Use `resolve_config_path` for L3 path |
| `oracle.config.example.json` | Add `quality_gate_profiles` example |
| `README.md` | Document the three profiles; document config path semantics |
| `tests/test_pipeline.py` | New cases: stage-0 diagnostic counters; profile auto-detection on isolated module |
| `tests/test_oracle_config.py` (new) | `_config_dir` is set; `resolve_config_path` resolves correctly |

## Verification

```bash
python -m pytest tests/ -q

# Re-run the RoguelikeTower scan from a working dir that is NOT the X15
# project root. The pipeline must locate the L3 file.
cd /tmp && python <path>/scripts/pipeline.py \
  --config <X15>/oracle.config.json \
  --input <X15>/docs/.../oracle-round3.json \
  --module-name RoguelikeTower \
  --source-root client/Assets/X1/ScriptGame/RoguelikeTower

# Stage 0 should now print:
#   [0/5] L3 Cross-Module Injection...
#     [NOTE] 0/13 contracts enriched. Diagnostic: ...
#     -> module appears isolated; consider --profile leaf
#   [4/5] Stats + Quality Gate...
#     quality_gate_profile: leaf  (auto)
#     P0+P1 ratio: ...% [PASS]
```

## Risk / Migration

- **Config schema additions are non-breaking.** Old configs without the new
  `quality_gate_profiles` block work unchanged with the built-in defaults.
- **`_config_dir` injection** -- internal-only field. Existing code paths
  that iterate `cfg.items()` would see it, but none currently do.
- **Auto-profile** -- could silently relax the gate. Mitigated by printing
  the auto-selection. Users who want strict can pass `--profile default`.
- **Rollback.** All changes are additive except `pipeline.py:286-289` which
  takes one extra branch. Revert in one PR.

## Out of Scope for Phase B

- Multi-language profile presets (e.g. `unity-leaf` vs `web-leaf`). Listed in
  Phase E.
- Replacing the `min_effective` floor with a confidence-weighted metric.
  Considered for Phase C alongside #8.

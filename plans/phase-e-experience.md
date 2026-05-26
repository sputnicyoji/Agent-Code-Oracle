# Phase E -- Docs and Release Hygiene

> P3. Small fixes that compound: better search hits in the KG, a documented
> path for users without RepoMap, and a tagged release that matches the
> commit history.

## Problems

### #10 -- KG observations join long lists into one line

**Location:** `scripts/kg_injector.py:57-59`

```python
if involved:
    observations.append(f"[involved_files] {', '.join(involved)}")
if affected:
    observations.append(f"[affected_external_files] {', '.join(affected)}")
```

When `aim_search_nodes` greps observations for a file path, a contract with
10 involved files becomes one observation 600 chars long. The match returns
the whole line, dragging 9 unrelated paths into the search result.

### #12 -- README assumes RepoMap exists

**Location:** `README.md` ("How It Works" / "Static Provider")

The repository ships `repomap_bridge.py` which parses Aider's RepoMap
output format. RepoMap is not universal. Projects without it currently
have no documented path -- Phase A's testing on X15 worked only because
X15 already has a custom RepoMap generator.

### #13 -- `pyproject.toml` version did not advance with v4.2 commit

**Location:** `pyproject.toml:3`

```toml
version = "4.1.0"
```

The "v4.2" commit message in git history is a semantic claim. PyPI / pip
users see 4.1.0 still. The schema bridge in 4.2 is observable -- this is
not a no-op release.

## Proposed Change

### One observation per path (#10)

```python
# scripts/kg_injector.py
for f in involved:
    observations.append(f"[involved] {f}")
for f in affected:
    observations.append(f"[affected_external] {f}")
if affected:
    observations.append(f"[external_consumer_count] {len(affected)}")
```

Keys change from `[involved_files]` and `[affected_external_files]` to
`[involved]` and `[affected_external]` -- matching the schema v2 names
already used in the JSON shape.

### Migration -- this is a real schema break

Existing KG entries written by Oracle <= 4.2 carry the old `[involved_files]`
and `[affected_external_files]` keys. A search for `[involved] foo.cs` will
not match them; a search for `[involved_files] foo.cs` will not match new
entries either. Users with a mixed KG (some modules scanned pre-rename, some
post) need either:

- **Path A (recommended).** Run the migration script once. New default.
  ```bash
  python scripts/migrate_kg_observations.py --kg-context code_contracts
  ```
  The script reads existing entries from the KG, rewrites
  `[involved_files] a, b, c` into N separate `[involved] a` /
  `[involved] b` / `[involved] c` observations, deletes the old aggregated
  line. Idempotent; safe to run multiple times.

- **Path B (compatibility shim, kept for one minor release).** The injector
  emits **both** old and new observations during 4.2.x:
  ```python
  for f in involved:
      observations.append(f"[involved] {f}")
  if involved:
      observations.append(f"[involved_files] {', '.join(involved)}")  # legacy
  ```
  Doubles observation count but lets old queries keep working without a
  migration. Dropped in 4.3.

Phase E ships with **Path A as default and Path B as opt-in** via
`--emit-legacy-kg-keys` flag on `pipeline.py`. README documents both;
default is "rename now, run migration script". Path B exists for users who
cannot schedule the migration immediately.

### Document graph provider alternatives (#12)

New section in README:

```markdown
## Graph Providers

The pipeline's Stage 0 enrichment needs a cross-file reference graph. Oracle
supports several providers, ranked by setup cost:

| Provider | Install | Strength |
|----------|---------|----------|
| `repomap_l3` (Aider format) | `pip install aider-chat` then run on the repo | Most common; supports all aider's languages |
| `ctags_universal` | `apt install universal-ctags` or platform equivalent | Fastest; class/function granularity |
| `grep_fallback` | No install; built-in | Zero-setup; slow on large repos |

`grep_fallback` is for projects that have neither RepoMap nor ctags. It uses
`git grep` (or `rg` when available) to compute "who references symbol X"
on demand. No persistent index; each pipeline invocation pays the cost.

In `oracle.config.json`:

\`\`\`jsonc
{
  "graph_provider": {
    "type": "grep_fallback",
    "include_dirs": ["src/"]  // optional restriction
  }
}
\`\`\`
```

Implementation of `grep_fallback` provider is a stretch goal of Phase E.
The mechanism:

```python
# scripts/providers/grep_provider.py (new)
class GrepProvider:
    """Compute external consumers via git grep / rg on demand.

    Slower than indexed providers but zero-setup.
    """
    def __init__(self, repo_root: Path, include_dirs: list[str] | None = None):
        self.repo_root = repo_root
        self.include_dirs = include_dirs or ["."]
        self._grep_cmd = self._detect_grep()

    def get_module_external_consumers(self, module_name: str, source_root: str) -> list[dict]:
        from repomap_bridge import _extract_top_level_symbols  # Phase A helper
        internal_symbols = set()
        for path in Path(source_root).rglob("*"):
            if path.is_file():
                internal_symbols.update(_extract_top_level_symbols(path))

        results = []
        source_root_p = Path(source_root).resolve()
        for sym in internal_symbols:
            for hit_file in self._grep_symbol(sym):
                hit_path = Path(hit_file).resolve()
                try:
                    hit_path.relative_to(source_root_p)
                    is_external = False
                except ValueError:
                    is_external = True
                if is_external:
                    results.append({
                        "class_name": sym,
                        "consumer_name": Path(hit_file).stem,
                        "relation_type": "reference",
                        "is_external": True,
                    })
        return results

    def _grep_symbol(self, symbol: str) -> list[str]:
        # Prefer rg, fall back to git grep, then plain grep
        # Match identifier-bounded; not just substring
        ...
```

Provider dispatch in `pipeline.py:286-289` becomes:

```python
provider_type = (graph_provider or {}).get("type")
if provider_type == "repomap_l3":
    repomap_l3 = resolve_config_path(cfg, ["graph_provider", "path"])
    bridge = RepoMapBridge(str(repomap_l3))
elif provider_type == "grep_fallback":
    bridge = GrepProvider(repo_root, graph_provider.get("include_dirs"))
elif provider_type is None:
    bridge = None
else:
    raise ValueError(f"Unknown graph_provider.type: {provider_type}")
```

### Release hygiene (#13)

- Bump `pyproject.toml` to `4.2.0`.
- Tag `v4.2.0` once changes land.
- Add `CHANGELOG.md` (referenced from README) so reasons for the bump are
  visible without scanning git log.
- Add a pre-commit hook (`hooks/pre-commit` or `.github/workflows/version-check.yml`)
  that verifies `pyproject.toml.version` matches the latest `v*` tag when
  the commit message starts with `release:`. Avoids the next "subject says
  4.2 body is 4.1" drift.

## Files Touched

| File | Change |
|------|--------|
| `scripts/kg_injector.py` | One observation per path; `--emit-legacy-kg-keys` flag plumbed through |
| `scripts/migrate_kg_observations.py` (new) | One-shot rewrite of old `[involved_files]` lines |
| `scripts/providers/grep_provider.py` (new) | grep-based provider |
| `pipeline.py` | Provider dispatch |
| `README.md` | Graph Providers section, link to plans/ |
| `CHANGELOG.md` (new) | v4.2 entry, format for future entries |
| `pyproject.toml` | version 4.2.0 |
| `.github/workflows/version-check.yml` (new) | Optional CI gate |
| `tests/test_kg_injector.py` | Assert one observation per path |
| `tests/test_grep_provider.py` (new) | Provider contract test |

## Verification

```bash
python -m pytest tests/ -q

# KG observation split
python scripts/pipeline.py --config ... --input ... --output out.json
python -c "
import json
d = json.load(open('out.json'))
for e in d['kg_format']['entities']:
    involved_lines = [o for o in e['observations'] if o.startswith('[involved] ')]
    # one line per file, never aggregated
    for line in involved_lines:
        assert ',' not in line.split(' ', 1)[1], 'paths still joined'
print('one observation per path: ok')
"

# Grep provider works without RepoMap
cd /tmp/test-repo && python <oracle>/scripts/pipeline.py \
  --config <config-with-grep_fallback> \
  --input <round3.json> --module-name X --source-root src/X --output out.json
# expect: Stage 0 enrichment count > 0 on a repo that has cross-module
# references, even when no L3 file exists

# Version
python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
# expect: 4.2.0
```

## Risk / Migration

- **Observation key rename is a schema break.** Mitigation documented
  above (Path A migration script; Path B legacy-key emission). Without
  one of those, users with mixed-vintage KG state will silently lose
  hits. Choose Path A unless deferring the migration is essential.
- **Grep provider performance.** O(n_symbols * grep_cost). Acceptable on
  small/medium repos; user warning printed when `internal_symbols > 500`.
- **CI hook.** Optional. If the project does not use GitHub Actions, skip
  the workflow file -- the pre-commit hook still works locally.

## Out of Scope for Phase E

- ctags provider. Listed as a follow-on; conceptually identical to grep but
  with an index.
- Auto-detecting which provider to use ("if `tags` file exists, prefer ctags
  over grep"). Could be added once two non-RepoMap providers exist.
- KG entry deduplication / merge with existing KG state. The injector is
  format-only; merge is the user's MCP tool problem.

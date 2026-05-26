# Phase A -- Correctness Fixes

> P0. Land first. Two later phases (B Stage-0 diagnostic, D round driver) read
> the internal-class set this phase corrects.

## Goal

Make the static analysis honest. `get_module_external_consumers` and
`pipeline.py` Stage 0 currently return numbers that look authoritative but
are quietly wrong on multi-class source files. Downstream stages then either
enrich the wrong contracts or fail to enrich the right ones.

## Problems

### #1 -- `repomap_bridge.internal_classes` uses file stem as class name

**Location:** `scripts/repomap_bridge.py:110-119`

```python
internal_classes: set[str] = set()
if source_root and os.path.isdir(source_root):
    for dirpath, _, filenames in os.walk(source_root):
        for f in filenames:
            stem = Path(f).stem
            if stem in self.nodes:
                internal_classes.add(stem)
```

`internal_classes` is the set used to decide whether a consumer is "external".
A file `Buff.cs` containing nine `Buff` subclasses contributes one entry
(`Buff`). The other eight subclasses are absent from `internal_classes`, so
when they appear as children of internal nodes they are misreported as
external consumers.

**Real measurement:** on RoguelikeTower the bridge reported 68 external
consumers; the true count of cross-module consumers is 0. The 68 entries
were inner-class inheritance edges leaking through.

### #2 -- `pipeline.py` Stage 0 has the same assumption

**Location:** `scripts/pipeline.py:111-115`

```python
for f in involved:
    symbol = Path(f).stem
    l3_consumers.extend(consumer_index.get(symbol, []))
```

`consumer_index` is keyed by symbol name. Looking it up by file stem only
works when the file holds exactly one top-level symbol that shares the file's
name. Any other layout misses real cross-module enrichment opportunities.

### #3 -- `incremental_scanner.get_changed_files` returns `[]` on failure

**Location:** `scripts/incremental_scanner.py:49-75`

```python
if result.returncode != 0:
    print(f"Warning: git diff failed: {result.stderr.strip()}")
    return []
```

`oracle_sync.py:81-123` already fixed the same shape (returns `list[str] | None`
with ORIG_HEAD fallback). The incremental scanner still has the original
behaviour, so a broken git invocation looks identical to a clean merge with no
matching files.

### #4 -- `blind_spot_filter.R2` is named but disabled

**Location:** `scripts/blind_spot_filter.py:117-132`

```python
def _check_r2_single_dir(self, contract: dict) -> bool:
    """R2: involved_files all in same directory (rationale only)"""
    ...
    return False  # Disabled until path info is available
```

Schema v2 introduced full repo-relative paths under `involved[].path`; the
"path info is available" precondition is satisfied. The check still returns
False unconditionally. Either remove it or implement it -- a named filter that
silently no-ops is worse than no filter.

## Proposed Change

### Internal class set (fixes #1 + #2 together)

Introduce a single source of truth for "what symbols live in this module's
source tree". Both the bridge and the pipeline read from it.

```python
# scripts/repomap_bridge.py
class RepoMapBridge:
    def __init__(self, l3_path: str):
        ...
        self._file_to_symbols: dict[str, set[str]] = {}

    def index_source_tree(self, source_root: str,
                          include: list[str] | None = None,
                          exclude: list[str] | None = None) -> set[str]:
        """Build internal symbol set by scanning source files for class-like
        definitions and intersecting with L3 nodes.

        Returns the set of symbols this module defines that are also visible
        in the reference graph. Side-effect: populates self._file_to_symbols.
        """
        internal: set[str] = set()
        for path in _walk_source_files(source_root, include, exclude):
            symbols = _extract_top_level_symbols(path)
            self._file_to_symbols[str(path)] = symbols
            internal.update(s for s in symbols if s in self.nodes)
        return internal

    def get_module_external_consumers(self, module_name, source_root):
        internal = self.index_source_tree(source_root)
        # ... unchanged downstream, but now `internal` is correct ...
```

`_extract_top_level_symbols` is a language-neutral regex pass:

```python
_DEFN_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|export|pub|static)?\s*"
    r"(?:abstract|sealed|final|partial|async)?\s*"
    r"(?:class|struct|interface|record|trait|impl|enum|type)\s+"
    r"([A-Z][A-Za-z0-9_]*)",
    re.MULTILINE,
)

def _extract_top_level_symbols(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return set(_DEFN_RE.findall(text))
```

Trade-off: regex over tree-sitter. Tree-sitter is more accurate but adds a
per-language dependency and breaks the "zero deps" tagline. The regex
over-includes (e.g. it matches `class` inside a comment block) but the only
consequence is "more symbols marked internal" -- the safe direction.

`pipeline.py` Stage 0 then reads `bridge._file_to_symbols` instead of computing
`Path(f).stem`:

```python
for contract in contracts:
    for f in extract_contract_paths(contract, "involved_files"):
        symbols = bridge.file_to_symbols(f)  # public accessor
        for sym in symbols:
            l3_consumers.extend(consumer_index.get(sym, []))
```

### Incremental scanner (fixes #3)

Mirror the `oracle_sync.py` pattern exactly. Lift the helper if you want one
implementation, or copy verbatim. Either way: return `list[str] | None`,
ORIG_HEAD first then HEAD~N, 30s timeout, distinguish stderr cases.

### Blind-spot R2 (fixes #4)

Schema v2 paths are now available. Implement the original intent:

```python
def _check_r2_single_dir(self, contract: dict) -> bool:
    if contract.get("type") != "rationale":
        return False
    paths = extract_contract_paths(contract, "involved_files")
    if len(paths) <= 1:
        return False
    dirs = {str(Path(p).parent) for p in paths if "/" in p or "\\" in p}
    # only fire when every path was repo-relative (not bare basenames)
    if len(dirs) <= 1 and len(dirs) > 0:
        return True
    return False
```

Effect: a `rationale` contract whose involved files all sit in one directory
gets a `R2:single_dir_warn` tag (no demotion, just a flag). Rationale should
typically span a boundary; single-directory rationale is often "code style"
masquerading as design intent.

## Files Touched

| File | Change |
|------|--------|
| `scripts/repomap_bridge.py` | Add `_file_to_symbols`, `index_source_tree`, `file_to_symbols`; rewire `get_module_external_consumers` |
| `scripts/pipeline.py` | Replace `Path(f).stem` lookup with bridge accessor |
| `scripts/incremental_scanner.py` | Adopt `oracle_sync.get_changed_files` shape (None on failure, ORIG_HEAD fallback, timeout) |
| `scripts/blind_spot_filter.py` | Real R2 implementation using v2 paths |
| `tests/test_repomap_bridge.py` | Add fixture file `Buff.cs` style: one file with 3 class definitions; assert `internal_classes` covers all three |
| `tests/test_pipeline.py` | Add case: contract with `involved` pointing at multi-class file; assert enrichment fires |
| `tests/test_blind_spot_filter.py` | Add R2 case: rationale with paths from one dir gets tag; rationale with paths from two dirs does not |
| `tests/fixtures/multi_class_module/` | New fixture tree: 3-4 source files, one of them with multiple definitions |

## Verification

```bash
# Baseline must remain green
python -m pytest tests/ -q

# New tests prove the bug is gone
python -m pytest tests/test_repomap_bridge.py::TestInternalClassesMultiDefn -v
python -m pytest tests/test_pipeline.py::TestStage0MultiSymbolFile -v
python -m pytest tests/test_blind_spot_filter.py::TestR2SingleDir -v

# Integration smoke against the same RoguelikeTower case that exposed #1+#2
python scripts/repomap_bridge.py \
  --l3 <path>/repomap-L3-full.md \
  --module RoguelikeTower \
  --source-root <path>/client/Assets/X1/ScriptGame/RoguelikeTower/

# Acceptance: "External consumers of RoguelikeTower" count should drop from
# 68 to a number close to the truth (0-3, depending on actual edges). The
# manual filesystem audit script that already exists can confirm.
```

## Risk / Migration

- **Symbol over-detection from regex.** Matches inside comments and strings.
  Consequence: a few extra entries in `internal_classes`, which only widens
  the "this is internal" set. Cannot produce false external positives.
- **Regex per-language coverage.** Current pattern covers C#/Java/TS/Rust/Go
  style declarations. Python `class Foo:` is also covered. C++ `class Foo {`
  and PHP are covered. Erlang/Lisp/F# are not -- document this in the
  bridge docstring.
- **Backwards compat.** `get_module_external_consumers` signature unchanged.
  The internal set widens; results never lose consumers, may drop spurious
  externals. No KG schema change.
- **Rollback.** Each fix is one file; revert by `git revert <hash>`.

## Out of Scope for Phase A

- Tree-sitter parser integration. Listed in Phase D.
- Adding new languages to the regex. Driven by user reports.
- Touching `oracle_sync.py` -- already correct.
- **Function-level symbol extraction.** The regex captures type-level
  declarations (`class` / `struct` / `interface` / `record` / `trait` /
  `impl` / `enum` / `type`) only. Go free functions (`func Foo()`), Rust
  free functions (`fn bar()`), C/C++ free functions, and similar are not
  detected. Oracle's contracts target cross-module impact at the type
  boundary, so this is acceptable for v1. If a downstream module needs
  function-level granularity (e.g. a Go library scan), extend the regex
  in a follow-on -- the existing pattern is a single regex that already
  treats the trailing identifier as the symbol name, so adding `func` /
  `fn` is one line plus a fixture.

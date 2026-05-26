# Phase C -- Contract Data Quality

> P1. Make freshness real, and make `evidence` more than a free-text annotation.
> Medium risk because both changes touch the contract schema.

## Goal

A `freshness_checker` that cannot detect changes is decoration. An `evidence`
field that is free-form text cannot meaningfully gate contract quality.
Both currently exist; both currently soften the system instead of
strengthening it.

## Problems

### #7 -- Freshness reports FRESH when there is no hash to compare

**Location:** `scripts/freshness_checker.py:88-100, 122-130`

```python
def _hash_mismatches(self, contract: dict) -> list[str]:
    expected = contract.get("file_hashes") or {}
    if not isinstance(expected, dict):
        return []
    mismatches = []
    for file_ref, expected_hash in expected.items():
        ...
    return mismatches
```

```python
if not missing_involved and not missing_external and not hash_changed:
    status = "FRESH"
```

`hash_changed` is the only way to detect content drift. It is only populated
when the contract carries `file_hashes`. Pipeline output does not emit
`file_hashes`. Result: every contract reports FRESH forever, even after the
files have been completely rewritten.

This is the highest-impact bug in the freshness path. It is silent. Users
who run `freshness_checker` get a green report and assume their KG is current.

### #8 -- `evidence[]` is unstructured

**Location:** contract schema (defined in `references/prompt-templates.md`),
quality gate at `scripts/pipeline.py:228-231`

```python
require_evidence_for = set(gate.get("require_evidence_for", []) or [])
for c in contracts:
    if c.get("type") in require_evidence_for and not c.get("evidence"):
        failures.append(f"missing evidence for {c.get('type')}: {c.get('title')}")
```

The gate only checks `evidence` is non-empty. A `blast_radius` contract with
`evidence: [{"kind": "design_rationale", "source": "doc", "target": "Patterns.md"}]`
passes -- but a doc-only justification is weak for blast_radius, which by
definition is about cross-file impact.

Evidence kinds in actual use (observed on RoguelikeTower):

| Kind | Strength | Verifiable? |
|------|----------|-------------|
| `static_reference` | strongest | yes (must resolve in graph provider) |
| `code_comment` | strong | yes (file:line must exist) |
| `data_flow_trace` | medium | partial (file refs must exist) |
| `design_rationale` | weak | no (free text) |

## Proposed Change

### Auto-hash on pipeline output (#7)

Add a final post-injection step in `pipeline.py` that hashes every
`involved_files` path and writes the result back into the contract before
output:

```python
# scripts/pipeline.py
def _hash_involved_files(self, contracts: list[dict], repo_root: Path) -> None:
    """Populate contract['file_hashes'] with sha256 of involved+affected paths.
    Skipped for paths over MAX_HASH_BYTES (default 10 MB) to keep scans fast.
    """
    MAX_HASH_BYTES = 10 * 1024 * 1024
    for c in contracts:
        hashes: dict[str, str] = {}
        for key in ("involved_files", "affected_external_files"):
            for rel in extract_contract_paths(c, key):
                p = (repo_root / rel)
                if not p.exists() or p.stat().st_size > MAX_HASH_BYTES:
                    continue
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                hashes[rel] = h.hexdigest()
        if hashes:
            c["file_hashes"] = hashes
```

Wired right before Stage 5 (KG injection), guarded by a `--hash-involved` /
`--no-hash-involved` flag, defaulting to **on** so freshness works out of the
box.

`freshness_checker.py` already reads `file_hashes`; once contracts carry the
field, freshness becomes meaningful. No checker change required.

For migration: contracts written before this lands have no `file_hashes`.
Freshness on those still reports FRESH unless the involved files vanish.
Document this as "freshness is only meaningful for contracts scanned with
v4.3+". A `scripts/backfill_hashes.py` one-shot can populate hashes for
existing contract JSON files based on current file content -- but that
operation has a clear semantic gap: "current content" might not match what
the contract was extracted against. Backfill should be opt-in and warn the
user.

### Evidence kinds become enumerated and verifiable (#8)

Schema additions (no breaking change -- new optional checks):

```jsonc
{
  "evidence": [
    {
      "kind": "static_reference",
      "source": "repomap_l3" | "ctags" | "manual",
      "target": "<path>#<symbol>"
    },
    {
      "kind": "code_comment",
      "source": "<path>:<line>",
      "target": "<quoted comment text>"
    },
    {
      "kind": "data_flow_trace",
      "source": "<path>:<line>",
      "target": "<path>:<line>"
    },
    {
      "kind": "design_rationale",
      "source": "doc" | "commit:<sha>" | "issue:<id>",
      "target": "<path-or-url>"
    }
  ]
}
```

Validator additions (`scripts/contract_validator.py`):

```python
VALID_EVIDENCE_KINDS = {
    "static_reference", "code_comment",
    "data_flow_trace", "design_rationale",
}

def _validate_evidence(self, contract: dict) -> list[str]:
    errors = []
    for i, ev in enumerate(contract.get("evidence") or []):
        if not isinstance(ev, dict):
            errors.append(f"evidence[{i}] not an object")
            continue
        kind = ev.get("kind")
        if kind not in VALID_EVIDENCE_KINDS:
            errors.append(f"evidence[{i}] invalid kind: {kind}")
            continue
        if kind == "code_comment":
            src = ev.get("source", "")
            if ":" not in src:
                errors.append(f"evidence[{i}] code_comment needs source 'file:line'")
            else:
                # Verify file exists (line check is best-effort)
                file_ref = src.rsplit(":", 1)[0]
                if not resolve_file_ref(file_ref, self._file_cache):
                    errors.append(f"evidence[{i}] code_comment source file not found: {file_ref}")
        elif kind == "static_reference":
            tgt = ev.get("target", "")
            if "#" not in tgt:
                errors.append(f"evidence[{i}] static_reference needs target 'path#symbol'")
    return errors
```

Quality gate extension (`scripts/pipeline.py`):

```python
strong_kinds = {"static_reference", "code_comment", "data_flow_trace"}
require_strong = set(gate.get("require_strong_evidence_for", []) or [])
for c in contracts:
    if c.get("type") in require_strong:
        has_strong = any(
            (ev or {}).get("kind") in strong_kinds for ev in (c.get("evidence") or [])
        )
        if not has_strong:
            failures.append(
                f"{c['type']} contract '{c['title']}' lacks strong evidence "
                f"(needs one of: {sorted(strong_kinds)})"
            )
```

Default config (`scripts/oracle_config.py`):

```python
DEFAULT_QUALITY_GATE = {
    "min_effective": 5,
    "max_contracts": 30,
    "min_high_value_ratio": 0.5,
    "require_evidence_for": ["blast_radius"],
    # NEW. Default empty so the gate is opt-in. Existing KG content uses
    # design_rationale heavily; switching this on by default would fail
    # every previously-scanned module. Document the upgrade path in the
    # release notes; let users flip it on when they are ready to rescan.
    "require_strong_evidence_for": [],
}
```

The README example shows the value users *should* migrate toward
(`["blast_radius"]`) but the in-code default is empty. Migration sequence:

1. Land #7 (auto-hash). All new scans carry hashes.
2. Users see the migration warning when running pipeline with old
   contract JSON: "evidence kinds are now classified; consider setting
   `require_strong_evidence_for: [blast_radius]` after rescanning your
   modules."
3. Users opt in module by module.

## Files Touched

| File | Change |
|------|--------|
| `scripts/pipeline.py` | `_hash_involved_files` step, `--hash-involved` flag, strong-evidence gate |
| `scripts/contract_validator.py` | `_validate_evidence` method |
| `scripts/oracle_config.py` | Default gate adds `require_strong_evidence_for` |
| `scripts/backfill_hashes.py` (new) | One-shot to populate file_hashes for legacy contracts |
| `oracle.config.example.json` | Document strong-evidence option |
| `references/contract-types.md` (skill ref) | Document evidence kinds with strength tiers |
| `references/prompt-templates.md` (skill ref) | Round 2 prompt should call out kind values |
| `tests/test_pipeline.py` | Hash auto-fill case; strong-evidence gate case |
| `tests/test_contract_validator.py` (new) | Evidence kind validation cases |

## Verification

```bash
python -m pytest tests/ -q

# Hash propagation
python scripts/pipeline.py --config ... --input ... --output out.json --hash-involved
python -c "
import json
d = json.load(open('out.json'))
for c in d['contracts']:
    assert c.get('file_hashes'), c['title']
print('all contracts carry file_hashes')
"

# Freshness now detects modifications
echo '// touch' >> <one-of-the-involved-files>
python scripts/freshness_checker.py --input out.json --source-root <module-path>
# expect: status STALE with hash_changed listing the touched file

# Strong-evidence gate
# Create a contract with only design_rationale evidence for a blast_radius type
# and assert pipeline fails the gate, exit code 2
```

## Risk / Migration

- **Breaking change avoided by default-empty.** Earlier draft of this
  phase defaulted `require_strong_evidence_for` to `["blast_radius"]`,
  which would have failed every pre-v4.3 module (most current contracts
  carry only `design_rationale` evidence). Default is now `[]`; users
  flip it on per-config after a rescan. Pipeline still prints a one-time
  migration note pointing at the new gate option.
- **Hash performance.** sha256 over involved files. RoguelikeTower test:
  155 files * ~10KB avg = under 200ms. The MAX_HASH_BYTES guard prevents
  pathological cases.
- **Backfill semantics.** Backfilling hashes against current files makes a
  hash that says "this contract is fresh against today's code" -- which may
  hide already-stale contracts. Backfill is opt-in and prints a warning.
- **Rollback.** Hash writing is gated by flag; strong evidence is a config
  field. Both can be disabled without code changes.

## Out of Scope for Phase C

- Confidence-weighted quality gate. Could replace `min_effective`. Considered
  for a future "Phase F: gate redesign".
- AST-based code-comment verification (extracting the actual line and
  checking the text matches). Listed in Phase D alongside the round driver.

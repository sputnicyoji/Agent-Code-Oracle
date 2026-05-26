"""Backfill `file_hashes` into legacy contract JSON files.

Phase C #7 made the pipeline write `file_hashes` for every involved /
affected path. Contracts produced before v4.3 do not carry that field,
so freshness_checker reports them FRESH regardless of code drift.

This one-shot reads an existing contracts JSON file and writes the
current sha256 of each involved/affected path back into the contract.

CAVEAT (worth stating loudly): hashing against TODAY's file content
assumes today's code is what the contract was extracted from. If the
code already drifted since the original scan, backfill freezes the
drifted state as "current truth", which silently hides the staleness
the freshness check should have surfaced. Prefer re-running the
pipeline (`oracle-pipeline`) instead of backfilling when the original
extraction date is more than a few days old.

CLI:
    python scripts/backfill_hashes.py \\
        --input docs/contracts/MyModule.json \\
        --source-root src/MyModule \\
        --output docs/contracts/MyModule.json    # in-place rewrite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from contract_validator import ContractValidator
from oracle_config import (
    extract_contract_paths,
    load_json_config,
    normalize_config,
)


MAX_HASH_BYTES = 10 * 1024 * 1024


def _hash_file(path: Path) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_HASH_BYTES:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def backfill(input_path: Path, source_root: str, config_path: str | None) -> dict:
    cfg = normalize_config(load_json_config(config_path)) if config_path else normalize_config({})
    validator = ContractValidator(
        source_root=source_root,
        include=cfg.get("include"),
        exclude=cfg.get("exclude"),
    )

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "contracts" in data:
        contracts = data["contracts"]
        envelope = data
    elif isinstance(data, list):
        contracts = data
        envelope = None
    else:
        raise SystemExit("Input must be a contract list or {'contracts': [...]}")

    total_filled = 0
    for c in contracts:
        existing = c.get("file_hashes") or {}
        hashes = dict(existing) if isinstance(existing, dict) else {}
        paths: list[str] = []
        paths.extend(extract_contract_paths(c, "involved_files"))
        paths.extend(extract_contract_paths(c, "affected_external_files"))
        for rel in paths:
            if rel in hashes:
                continue  # do not stomp existing entries
            abs_path = validator.resolve_absolute(rel)
            if abs_path is None:
                continue
            digest = _hash_file(abs_path)
            if digest is None:
                continue
            hashes[rel] = digest
            total_filled += 1
        if hashes:
            c["file_hashes"] = hashes

    print(
        f"[backfill_hashes] Filled {total_filled} hashes across "
        f"{len(contracts)} contracts.",
        file=sys.stderr,
    )
    print(
        "[backfill_hashes] WARNING: hashes computed against CURRENT file "
        "content. If the code already drifted since extraction, freshness "
        "will mistakenly report FRESH after this backfill.",
        file=sys.stderr,
    )
    if envelope is not None:
        return envelope
    return {"contracts": contracts}


def main():
    parser = argparse.ArgumentParser(description="Backfill file_hashes into legacy contracts")
    parser.add_argument("--input", required=True, help="Contract JSON to read")
    parser.add_argument("--source-root", required=True, help="Module source root for file resolution")
    parser.add_argument("--output", help="Output path (default: in-place overwrite of --input)")
    parser.add_argument("--config", help="Path to oracle.config.json (optional; supplies include/exclude)")

    args = parser.parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    output_path = Path(args.output) if args.output else input_path

    result = backfill(input_path, args.source_root, args.config)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Wrote {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

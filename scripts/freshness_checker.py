"""
Freshness Checker — Contract staleness detection

Checks whether contracts already injected into KG are still valid:
1. Do files in involved_files still exist in the codebase
2. Do files in affected_external_files still exist

Input: pipeline output JSON (containing contracts) + source_root
Output: staleness report (FRESH / STALE / MISSING_ALL)

CLI:
    python freshness_checker.py --input result.json --source-root ./src/MyModule/
    python freshness_checker.py --input result.json --source-root ./src/ --extended
"""

import json
import os
import sys
import argparse
import hashlib
from pathlib import Path

from oracle_config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    build_file_index,
    extract_contract_paths,
    find_repo_root,
    load_json_config,
    normalize_config,
    resolve_file_ref,
)


class FreshnessChecker:
    """Contract staleness detector"""

    def __init__(
        self,
        source_root: str,
        extended_root: str = None,
        repo_root: str = None,
        include: list[str] = None,
        exclude: list[str] = None,
    ):
        """
        Args:
            source_root: Module source directory (for validating involved_files)
            extended_root: Extended source directory (for validating affected_external_files, typically src/)
        """
        self.repo_root = find_repo_root(repo_root or source_root or ".")
        self.include = include or DEFAULT_INCLUDE
        self.exclude = exclude or DEFAULT_EXCLUDE
        self.module_index = build_file_index(
            [source_root],
            repo_root=self.repo_root,
            include=self.include,
            exclude=self.exclude,
        ) if source_root else {}
        self.extended_index = build_file_index(
            [extended_root],
            repo_root=self.repo_root,
            include=self.include,
            exclude=self.exclude,
        ) if extended_root else {}
        self.module_files = set(self.module_index.keys())
        self.extended_files = set(self.extended_index.keys())

    def _build_file_set(self, root: str) -> set[str]:
        """Build set of filenames"""
        index = build_file_index([root], repo_root=self.repo_root, include=self.include, exclude=self.exclude)
        return set(index.keys())

    def _resolve(self, file_ref: str, extended: bool = False) -> str | None:
        index = self.extended_index if extended and self.extended_index else self.module_index
        return resolve_file_ref(file_ref, index)

    def _sha256(self, rel_path: str) -> str | None:
        path = self.repo_root / rel_path
        if not path.exists():
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _hash_mismatches(self, contract: dict) -> list[str]:
        expected = contract.get("file_hashes") or {}
        if not isinstance(expected, dict):
            return []
        mismatches = []
        for file_ref, expected_hash in expected.items():
            rel = self._resolve(file_ref, extended=True) or self._resolve(file_ref)
            if not rel:
                continue
            current = self._sha256(rel)
            if current and expected_hash and current != expected_hash:
                mismatches.append(rel)
        return mismatches

    def check(self, contracts: list[dict]) -> list[dict]:
        """
        Check contract freshness

        Returns:
            [{title, status, missing_files, missing_external}]
            status: FRESH | STALE | MISSING_ALL
        """
        results = []
        for c in contracts:
            title = c.get("title", "unknown")
            involved = extract_contract_paths(c, "involved_files")
            affected = extract_contract_paths(c, "affected_external_files")

            # Check involved_files
            missing_involved = [f for f in involved if not self._resolve(f)]

            # Check affected_external_files (use extended directory)
            missing_external = [f for f in affected if not self._resolve(f, extended=True)]
            hash_changed = self._hash_mismatches(c)

            # Determine status
            if not missing_involved and not missing_external and not hash_changed:
                status = "FRESH"
            elif len(missing_involved) == len(involved) and involved:
                status = "MISSING_ALL"
            else:
                status = "STALE"

            results.append({
                "title": title,
                "status": status,
                "confidence": c.get("confidence", 0),
                "type": c.get("type", "unknown"),
                "missing_files": missing_involved,
                "missing_external": missing_external,
                "hash_changed": hash_changed,
            })

        return results

    def print_report(self, results: list[dict]) -> None:
        """Print staleness report"""
        fresh = [r for r in results if r["status"] == "FRESH"]
        stale = [r for r in results if r["status"] == "STALE"]
        missing = [r for r in results if r["status"] == "MISSING_ALL"]

        print(f"\n=== Freshness Check Report ===")
        print(f"Total: {len(results)} | FRESH: {len(fresh)} | STALE: {len(stale)} | MISSING_ALL: {len(missing)}\n")

        if stale:
            print("--- STALE (some files missing, needs update) ---")
            for r in stale:
                print(f"  [{r['type']}] {r['title'][:70]} (conf={r['confidence']})")
                if r["missing_files"]:
                    print(f"    missing involved_files: {', '.join(r['missing_files'])}")
                if r["missing_external"]:
                    print(f"    missing affected_external: {', '.join(r['missing_external'])}")
                if r.get("hash_changed"):
                    print(f"    hash changed: {', '.join(r['hash_changed'])}")
            print()

        if missing:
            print("--- MISSING_ALL (all files missing, recommend deletion) ---")
            for r in missing:
                print(f"  [{r['type']}] {r['title'][:70]} (conf={r['confidence']})")
                print(f"    missing: {', '.join(r['missing_files'])}")
            print()

        if fresh and not stale and not missing:
            print("All contracts are FRESH. No action needed.\n")

        # Summary
        print(f"=== Summary ===")
        rate = len(fresh) / len(results) * 100 if results else 0
        print(f"  Freshness rate: {len(fresh)}/{len(results)} ({rate:.0f}%)")
        if stale or missing:
            print(f"  Action needed: {len(stale)} to update, {len(missing)} to delete")
        print()


def main():
    parser = argparse.ArgumentParser(description="Code Oracle Freshness Checker")
    parser.add_argument("--input", required=True, help="Pipeline output JSON (or round3 JSON)")
    parser.add_argument("--source-root", help="Module source root for file check (required unless --repomap-l3)")
    parser.add_argument("--extended-root", help="Extended source root for affected_external_files check")
    parser.add_argument("--repomap-l3", help="Use L3 class names for file check (faster than os.walk)")
    parser.add_argument("--config", help="Path to oracle.config.json")

    args = parser.parse_args()

    # Read input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support pipeline output or raw array
    if isinstance(data, dict) and "contracts" in data:
        contracts = data["contracts"]
    elif isinstance(data, list):
        contracts = data
    else:
        print("Error: Input must be JSON array or {contracts: [...]}", file=sys.stderr)
        sys.exit(1)

    cfg = normalize_config(load_json_config(args.config)) if args.config else normalize_config({})

    # Create checker: L3 fast-path or standard os.walk
    if args.repomap_l3:
        from repomap_bridge import RepoMapBridge
        bridge = RepoMapBridge(args.repomap_l3)
        l3_files = {name + ".cs" for name in bridge.nodes}
        # L3 covers all classes project-wide; use source_root subset if available
        if args.source_root:
            module_files = FreshnessChecker(args.source_root).module_files
        else:
            module_files = l3_files
        checker = FreshnessChecker.__new__(FreshnessChecker)
        checker.repo_root = find_repo_root(args.source_root or ".")
        # __init__ bypassed: every attribute __init__ would set must be
        # populated here. Forgetting `include`/`exclude` made any later refactor
        # that touches them (e.g. through `_build_file_set`) AttributeError.
        checker.include = list(cfg.get("include") or DEFAULT_INCLUDE)
        checker.exclude = list(cfg.get("exclude") or DEFAULT_EXCLUDE)
        checker.module_index = {f: [f] for f in module_files}
        checker.extended_index = {f: [f] for f in l3_files}
        checker.module_files = module_files
        checker.extended_files = l3_files
    else:
        if not args.source_root:
            print("Error: --source-root required unless --repomap-l3 is provided", file=sys.stderr)
            sys.exit(1)
        checker = FreshnessChecker(
            source_root=args.source_root,
            extended_root=args.extended_root,
            include=cfg.get("include"),
            exclude=cfg.get("exclude"),
        )
    results = checker.check(contracts)
    checker.print_report(results)

    # Exit code: 0 = all fresh, 1 = has stale/missing
    stale_count = sum(1 for r in results if r["status"] != "FRESH")
    sys.exit(1 if stale_count > 0 else 0)


if __name__ == "__main__":
    main()

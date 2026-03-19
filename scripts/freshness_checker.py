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
from pathlib import Path


class FreshnessChecker:
    """Contract staleness detector"""

    def __init__(self, source_root: str, extended_root: str = None):
        """
        Args:
            source_root: Module source directory (for validating involved_files)
            extended_root: Extended source directory (for validating affected_external_files, typically src/)
        """
        self.module_files = self._build_file_set(source_root) if source_root else set()
        self.extended_files = self._build_file_set(extended_root) if extended_root else set()

    def _build_file_set(self, root: str) -> set[str]:
        """Build set of filenames"""
        files = set()
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.endswith(".cs"):
                    files.add(f)
        return files

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
            involved = c.get("involved_files", [])
            affected = c.get("affected_external_files", [])

            # Check involved_files
            missing_involved = [f for f in involved if f not in self.module_files]

            # Check affected_external_files (use extended directory)
            check_set = self.extended_files if self.extended_files else self.module_files
            missing_external = [f for f in affected if f not in check_set]

            # Determine status
            if not missing_involved and not missing_external:
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
        checker.module_files = module_files
        checker.extended_files = l3_files
    else:
        if not args.source_root:
            print("Error: --source-root required unless --repomap-l3 is provided", file=sys.stderr)
            sys.exit(1)
        checker = FreshnessChecker(
            source_root=args.source_root,
            extended_root=args.extended_root,
        )
    results = checker.check(contracts)
    checker.print_report(results)

    # Exit code: 0 = all fresh, 1 = has stale/missing
    stale_count = sum(1 for r in results if r["status"] != "FRESH")
    sys.exit(1 if stale_count > 0 else 0)


if __name__ == "__main__":
    main()

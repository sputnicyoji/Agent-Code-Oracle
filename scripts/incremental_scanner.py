"""
Incremental Scanner -- git diff + L3 reference graph driven contract impact analysis

CLI:
    python incremental_scanner.py --l3 <l3_path> --contracts <existing.json> --commits 5
    python incremental_scanner.py --contracts <existing.json> --diff-files File1.cs File2.cs
"""

import json
import subprocess
import argparse
from pathlib import Path

from oracle_config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    extract_contract_paths,
    is_included,
    load_json_config,
    normalize_config,
)


class IncrementalScanner:
    """Analyze impact of code changes on existing contracts"""

    def __init__(
        self,
        l3_path: str = None,
        contracts_path: str = None,
        include: list[str] = None,
        exclude: list[str] = None,
    ):
        self.bridge = None
        if l3_path:
            from repomap_bridge import RepoMapBridge
            self.bridge = RepoMapBridge(l3_path)
        self.contracts = self._load_contracts(contracts_path) if contracts_path else []
        self.include = include or DEFAULT_INCLUDE
        self.exclude = exclude or DEFAULT_EXCLUDE

    def _load_contracts(self, path: str) -> list[dict]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "contracts" in data:
            return data["contracts"]
        return data if isinstance(data, list) else []

    def get_changed_files(self, commits: int = 1) -> list[str]:
        """Get changed files from git diff using configured include/exclude globs."""
        try:
            # Find repo root dynamically
            root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True
            )
            if root_result.returncode != 0:
                print(f"Warning: not a git repo: {root_result.stderr.strip()}")
                return []
            repo_root = root_result.stdout.strip()
            result = subprocess.run(
                ["git", "diff", "--name-only", f"HEAD~{commits}"],
                capture_output=True, text=True, cwd=repo_root
            )
            if result.returncode != 0:
                print(f"Warning: git diff failed: {result.stderr.strip()}")
                return []
            return [
                f.replace("\\", "/")
                for f in result.stdout.splitlines()
                if is_included(f, self.include, self.exclude)
            ]
        except FileNotFoundError:
            print("Warning: git not found in PATH")
            return []

    def analyze(self, changed_files: list[str]) -> dict:
        """Analyze contract impact of changed files"""
        affected_contracts = []
        uncovered_files = []
        impact_chain = []

        for fname in changed_files:
            base = Path(fname).name
            matched = []
            for c in self.contracts:
                involved = set(extract_contract_paths(c, "involved_files"))
                affected = set(extract_contract_paths(c, "affected_external_files"))
                involved_basenames = {Path(p).name for p in involved}
                affected_basenames = {Path(p).name for p in affected}
                if (
                    fname in involved or fname in affected
                    or base in involved_basenames or base in affected_basenames
                ):
                    matched.append(c)
            if matched:
                for c in matched:
                    affected_contracts.append({
                        "title": c["title"],
                        "type": c["type"],
                        "confidence": c["confidence"],
                        "trigger_file": fname,
                        "action": "REVIEW" if c["type"] == "blast_radius" else "CHECK",
                    })
            else:
                uncovered_files.append(fname)

            # L3 impact chain
            if self.bridge:
                symbol_name = Path(fname).stem
                consumers = self.bridge.get_symbol_consumers(symbol_name)
                if consumers:
                    impact_chain.append({
                        "source": fname,
                        "consumers": [c["name"] for c in consumers],
                        "consumer_count": len(consumers),
                    })

        return {
            "changed_files": changed_files,
            "affected_contracts": affected_contracts,
            "uncovered_files": uncovered_files,
            "impact_chain": impact_chain,
            "summary": {
                "total_changed": len(changed_files),
                "contracts_affected": len(affected_contracts),
                "uncovered": len(uncovered_files),
                "high_impact": sum(1 for ic in impact_chain if ic["consumer_count"] >= 3),
            },
        }

    def print_report(self, result: dict):
        s = result["summary"]
        print(f"\n=== Incremental Impact Report ===")
        print(f"Changed: {s['total_changed']} | Contracts hit: {s['contracts_affected']}"
              f" | Uncovered: {s['uncovered']} | High-impact: {s['high_impact']}")

        if result["affected_contracts"]:
            print(f"\n--- Affected Contracts ---")
            for ac in result["affected_contracts"]:
                print(f"  [{ac['action']}] [{ac['type']}] {ac['title'][:60]}")
                print(f"         trigger: {ac['trigger_file']} (conf={ac['confidence']})")

        if result["uncovered_files"]:
            print(f"\n--- Uncovered Files ---")
            for f in result["uncovered_files"]:
                print(f"  {f}")

        if result["impact_chain"]:
            print(f"\n--- L3 Impact Chain ---")
            for ic in result["impact_chain"]:
                flag = " [!]" if ic["consumer_count"] >= 3 else ""
                print(f"  {ic['source']} -> {ic['consumer_count']} consumers{flag}")
                for c in ic["consumers"][:5]:
                    print(f"    <- {c}")
                if len(ic["consumers"]) > 5:
                    print(f"    ... +{len(ic['consumers']) - 5} more")


def main():
    parser = argparse.ArgumentParser(description="Code Oracle Incremental Scanner")
    parser.add_argument("--l3", help="Path to repomap-L3-relations.md")
    parser.add_argument("--contracts", required=True, help="Existing contracts JSON")
    parser.add_argument("--commits", type=int, default=1, help="Commits to diff")
    parser.add_argument("--diff-files", nargs="+", help="Explicit changed .cs files")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--config", help="Path to oracle.config.json")

    args = parser.parse_args()
    cfg = normalize_config(load_json_config(args.config)) if args.config else normalize_config({})
    scanner = IncrementalScanner(args.l3, args.contracts, cfg.get("include"), cfg.get("exclude"))
    changed = args.diff_files or scanner.get_changed_files(args.commits)

    if not changed:
        print("No .cs files changed.")
        return

    result = scanner.analyze(changed)
    scanner.print_report(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()

"""
Code Oracle Pipeline

Five-stage post-processing pipeline:
1. Contract Validation  — format validation + file existence check
2. Semantic Dedup       — same-type + cross-type deduplication
3. Blind Spot Filter    — heuristic filter for AI-inferrable contracts
4. Stats + Quality Gate — statistics + quality gate
5. KG Injection         — convert to KG injection format

CLI:
    python pipeline.py --input round3.json --module-name MyModule --source-root ./src/
    python pipeline.py --input round3.json --module-name MyModule --output result.json
"""

import json
import sys
import argparse
from pathlib import Path

from contract_validator import ContractValidator
from semantic_dedup import SemanticDedup
from blind_spot_filter import BlindSpotFilter
from kg_injector import KGInjector


class OraclePipeline:
    """Contract post-processing pipeline"""

    def __init__(self, module_name: str, source_root: str = None,
                 dedup_threshold: float = 0.8, repomap_l3: str = None):
        self.module_name = module_name
        self.source_root = source_root
        self.repomap_l3 = repomap_l3
        self.validator = ContractValidator(source_root)
        self.dedup = SemanticDedup(threshold=dedup_threshold)
        self.blind_spot_filter = BlindSpotFilter()
        self.injector = KGInjector()

    def process(self, contracts: list[dict]) -> dict:
        """
        Execute full pipeline

        Args:
            contracts: Round 3 output contract list

        Returns:
            {
                "contracts": [...],
                "kg_format": {"entities": [...], "relations": [...], "context": "code_contracts"},
                "stats": {...}
            }
        """
        print(f"\n=== Code Oracle Pipeline ===")
        print(f"Module: {self.module_name}")
        print(f"Input: {len(contracts)} contracts\n")

        # Defensive copy to avoid mutating caller's data
        contracts = [dict(c) for c in contracts]

        # Stage 0: L3 Cross-Module Injection (optional)
        if self.repomap_l3:
            print("[0/5] L3 Cross-Module Injection...")
            from repomap_bridge import RepoMapBridge
            bridge = RepoMapBridge(self.repomap_l3)
            externals = bridge.get_module_external_consumers(
                self.module_name, self.source_root
            )
            # Preindex: class_name -> [consumer.cs]
            consumer_index: dict[str, list[str]] = {}
            for e in externals:
                if e["is_external"]:
                    consumer_index.setdefault(e["class_name"], []).append(
                        e["consumer_name"] + ".cs"
                    )
            for contract in contracts:
                involved = contract.get("involved_files", [])
                l3_consumers = []
                for f in involved:
                    cls = f.replace(".cs", "")
                    l3_consumers.extend(consumer_index.get(cls, []))
                if l3_consumers:
                    existing_ext = contract.get("affected_external_files", [])
                    contract["affected_external_files"] = list(
                        set(existing_ext + l3_consumers)
                    )
                    contract["_l3_enriched"] = True
            enriched = sum(1 for c in contracts if c.get("_l3_enriched"))
            print(f"  [OK] {enriched}/{len(contracts)} contracts enriched with L3 data\n")

        # Stage 1: Validation
        print("[1/5] Contract Validation...")
        validated = self.validator.process(contracts)
        print(f"  [OK] {len(validated)}/{len(contracts)} passed\n")

        # Stage 2: Semantic Dedup (same-type + cross-type)
        print("[2/5] Semantic Dedup...")
        unique = self.dedup.process(validated)
        print(f"  [OK] {len(unique)} unique contracts\n")

        # Stage 3: Blind Spot Filter
        print("[3/5] Blind Spot Filter...")
        filtered = self.blind_spot_filter.process(unique)
        print()

        # Stage 4: Stats + Quality Gate
        print("[4/5] Stats + Quality Gate...")
        stats = self._compute_stats(filtered)
        self._print_quality_gate(stats)
        print()

        # Stage 5: KG Injection
        print("[5/5] KG Injection Format...")
        kg_format = self.injector.convert(filtered, self.module_name)
        print(f"  [OK] {len(kg_format['entities'])} entities, {len(kg_format['relations'])} relations\n")

        print("=== Pipeline Complete ===\n")
        return {
            "contracts": filtered,
            "kg_format": kg_format,
            "stats": stats,
        }

    def _compute_stats(self, contracts: list[dict]) -> dict:
        """Compute statistics (including effective contract count)"""
        total = len(contracts)
        by_type = {}
        confidences = []

        for c in contracts:
            t = c.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            confidences.append(c.get("confidence", 0))

        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        effective = sum(1 for conf in confidences if conf > 0.5)

        # P0+P1 ratio check (based on effective contracts)
        p0_p1_count = by_type.get("blast_radius", 0) + by_type.get("rationale", 0)
        p0_p1_ratio = p0_p1_count / total if total > 0 else 0

        # Demoted contracts
        demoted = [c for c in contracts if c.get("_filter_tag")]
        demoted_titles = [f"  - [{c.get('_filter_tag')}] {c['title'][:60]}" for c in demoted]

        return {
            "total": total,
            "effective": effective,
            "by_type": by_type,
            "avg_confidence": round(avg_conf, 3),
            "avg_confidence_effective": round(
                sum(c for c in confidences if c > 0.5) / effective if effective > 0 else 0, 3
            ),
            "p0_p1_ratio": round(p0_p1_ratio, 3),
            "p0_p1_check": "PASS" if p0_p1_ratio >= 0.5 else "WARN: blast_radius + rationale < 50%",
            "demoted_contracts": demoted_titles,
        }

    def _print_quality_gate(self, stats: dict) -> None:
        """Print quality gate results"""
        total = stats["total"]
        effective = stats["effective"]
        p0_p1_ratio = stats["p0_p1_ratio"]

        print(f"  Total: {total}, Effective (conf > 0.5): {effective}")
        print(f"  Avg confidence: {stats['avg_confidence']} (effective: {stats['avg_confidence_effective']})")
        print(f"  P0+P1 ratio: {p0_p1_ratio:.1%} [{stats['p0_p1_check']}]")

        if stats["demoted_contracts"]:
            print(f"  Demoted contracts:")
            for line in stats["demoted_contracts"]:
                print(line)

        # Quality gate checks
        if effective < 10:
            print("  [WARN] Effective contracts < 10, consider re-scanning")
        if total > 30:
            print("  [WARN] Total > 30, Round 3 filtering may be too lenient")


def main():
    parser = argparse.ArgumentParser(description="Code Oracle Pipeline")
    parser.add_argument("--input", required=True, help="Round 3 output JSON")
    parser.add_argument("--module-name", required=True, help="Module name (e.g. MyModule)")
    parser.add_argument("--source-root", help="Source root for file validation")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--dedup-threshold", type=float, default=0.8, help="Dedup threshold")
    parser.add_argument("--repomap-l3", help="Path to repomap-L3-relations.md for L3 enrichment")

    args = parser.parse_args()

    # Read input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both array and {"contracts": [...]} format
    if isinstance(data, dict) and "contracts" in data:
        contracts = data["contracts"]
    elif isinstance(data, list):
        contracts = data
    else:
        print("Error: Input must be JSON array or {contracts: [...]}", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    pipeline = OraclePipeline(
        module_name=args.module_name,
        source_root=args.source_root,
        dedup_threshold=args.dedup_threshold,
        repomap_l3=args.repomap_l3,
    )
    result = pipeline.process(contracts)

    # Output
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Saved to: {output_path}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    # Print stats
    print("\n=== Stats ===")
    print(json.dumps(result["stats"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

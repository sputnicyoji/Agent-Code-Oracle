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

import hashlib
import json
import os
import sys
import argparse
from pathlib import Path

from contract_validator import ContractValidator
from semantic_dedup import SemanticDedup
from blind_spot_filter import BlindSpotFilter
from kg_injector import KGInjector
from oracle_config import (
    DEFAULT_QUALITY_GATE,
    extract_contract_paths,
    load_json_config,
    normalize_config,
    resolve_config_path,
    set_contract_paths,
)


class QualityGateError(RuntimeError):
    """Raised when contracts fail the configured quality gate.

    `partial_contracts` carries the Stage 1-3 output so `--allow-warn` callers
    can write the already-validated / deduped / filtered set, NOT the raw
    pipeline input.
    """

    def __init__(self, failures: list[str], stats: dict,
                 partial_contracts: list[dict] | None = None):
        super().__init__("Quality gate failed: " + "; ".join(failures))
        self.failures = failures
        self.stats = stats
        self.partial_contracts = partial_contracts or []


class OraclePipeline:
    """Contract post-processing pipeline"""

    def __init__(self, module_name: str, source_root: str = None,
                 dedup_threshold: float = 0.8, repomap_l3: str = None,
                 repo_root: str = None, include: list[str] = None,
                 exclude: list[str] = None, quality_gate: dict = None,
                 quality_gate_profiles: dict = None,
                 profile: str = "auto",
                 emit_legacy_kg_keys: bool = False,
                 graph_provider: dict = None,
                 repo_root_for_grep: str = None,
                 hash_involved: bool = True,
                 max_hash_bytes: int = 10 * 1024 * 1024,
                 allow_warn: bool = False):
        self.module_name = module_name
        self.source_root = source_root
        self.repomap_l3 = repomap_l3
        # Full provider config (type + provider-specific options). When set,
        # supersedes the bare `repomap_l3` path argument.
        self.graph_provider = graph_provider or {}
        self.repo_root_for_grep = repo_root_for_grep
        # Auto-hash settings. file_hashes is what freshness_checker reads
        # to decide if an involved file has changed since the contract was
        # extracted. Without it, freshness always reports FRESH; with it,
        # any byte change in any involved/affected file flips the contract
        # to STALE with `hash_changed` listing the file.
        self.hash_involved = hash_involved
        self.max_hash_bytes = max_hash_bytes
        # Base gate; per-profile overrides apply lazily in process() once we
        # know the auto-selected profile.
        self.quality_gate = dict(DEFAULT_QUALITY_GATE)
        self.quality_gate.update(quality_gate or {})
        self.quality_gate_profiles = dict(quality_gate_profiles or {})
        self.profile = profile  # "auto" | "default" | "leaf" | "hub" | custom
        self.allow_warn = allow_warn
        self.validator = ContractValidator(
            source_root,
            repo_root=repo_root,
            include=include,
            exclude=exclude,
        )
        self.dedup = SemanticDedup(threshold=dedup_threshold)
        self.blind_spot_filter = BlindSpotFilter()
        self.injector = KGInjector(emit_legacy_keys=emit_legacy_kg_keys)
        # Populated by Stage 0 when it runs; consumed by _compute_stats and
        # the quality-gate profile auto-selector. Empty dict means Stage 0
        # was skipped (no repomap_l3 configured).
        self._stage0_diagnostics: dict = {}

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

        # Stage 0: Cross-Module Enrichment via configured provider.
        # `_make_provider` returns repomap_l3 / grep_fallback / None based on
        # graph_provider config; backward-compatible with the legacy
        # `repomap_l3` path argument when no provider config is supplied.
        bridge = self._make_provider()
        if bridge is not None:
            gp_type = (self.graph_provider or {}).get("type") or "repomap_l3"
            print(f"[0/5] Cross-Module Enrichment ({gp_type})...")
            externals = bridge.get_module_external_consumers(
                self.module_name, self.source_root
            )
            # Preindex: symbol_name -> [consumer_symbol]
            consumer_index: dict[str, list[str]] = {}
            for e in externals:
                if e["is_external"]:
                    consumer_index.setdefault(e["class_name"], []).append(
                        e["consumer_name"]
                    )
            for contract in contracts:
                involved = extract_contract_paths(contract, "involved_files")
                l3_consumers = []
                for f in involved:
                    # Previously `Path(f).stem` -- only matched contracts whose
                    # involved file name happened to equal the symbol name
                    # defined inside it. Now we ask the bridge for every
                    # type-level symbol the file declares and look each up,
                    # which makes multi-class files (Buff.cs, IEntityCommand.cs,
                    # ...) findable. `bridge.get_module_external_consumers`
                    # above already populated `_file_to_symbols` for the
                    # module source tree.
                    for symbol in bridge.file_to_symbols(f):
                        l3_consumers.extend(consumer_index.get(symbol, []))
                if l3_consumers:
                    # Read existing externals through the bridge so v2 contracts
                    # that only have `affected_external` (not the v1
                    # `affected_external_files` key) are not silently dropped
                    # when we merge L3 consumers in.
                    existing_ext = extract_contract_paths(
                        contract, "affected_external_files"
                    )
                    merged = sorted(set(existing_ext + l3_consumers))
                    set_contract_paths(contract, merged, "affected_external_files")
                    contract["_l3_enriched"] = True
                    contract.setdefault("evidence", []).append({
                        "kind": "static_reference",
                        "source": "repomap_l3",
                        "target": ", ".join(sorted(set(l3_consumers))),
                    })
            enriched = sum(1 for c in contracts if c.get("_l3_enriched"))

            # Diagnostic counters. Used both by the [NOTE] message printed
            # when enriched == 0 (so the user knows WHY enrichment found
            # nothing) and by quality-gate auto-profile (cross_edges == 0
            # AND internal_class_count > 0 -> isolated module -> leaf).
            internal_class_count = len(bridge._file_to_symbols)
            # Note: index_source_tree stores both repo-relative paths AND
            # basenames as keys, so the raw len() double-counts. Use the
            # set of distinct symbol-set values instead.
            internal_symbols = set()
            for syms in bridge._file_to_symbols.values():
                internal_symbols.update(syms)
            internal_class_count = len(internal_symbols)
            cross_edges = sum(1 for e in externals if e["is_external"])
            contract_paths_total = sum(
                len(extract_contract_paths(c, "involved_files")) for c in contracts
            )
            self._stage0_diagnostics = {
                "internal_class_count": internal_class_count,
                "cross_edges": cross_edges,
                "contract_paths_total": contract_paths_total,
                "enriched": enriched,
            }

            if enriched == 0:
                print(f"  [NOTE] 0/{len(contracts)} contracts enriched. Diagnostic:")
                print(f"    internal symbols recognised: {internal_class_count}")
                print(f"    cross-module edges in graph: {cross_edges}")
                print(f"    contract involved-paths total: {contract_paths_total}")
                if cross_edges == 0 and internal_class_count > 0:
                    print(f"    -> module appears isolated; consider --profile leaf")
                elif internal_class_count == 0:
                    print(f"    -> graph provider sees no symbols under "
                          f"{self.source_root}")
                    print(f"       (check source_root, include/exclude, or rebuild "
                          f"the L3 index)")
                else:
                    print(f"    -> contracts reference files outside the graph's "
                          f"symbol set")
            else:
                print(f"  [OK] {enriched}/{len(contracts)} contracts enriched with L3 data")
            print()

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

        # Resolve profile now that Stage 0 diagnostics are in stats. Auto
        # selection chooses 'leaf' when the module is isolated (Stage 0 saw
        # source symbols but zero cross-module edges). Explicit --profile
        # overrides auto. Unknown profile name falls back silently to base
        # gate rather than crashing.
        selected_profile = self._resolve_profile(stats)
        if selected_profile and selected_profile in self.quality_gate_profiles:
            overrides = self.quality_gate_profiles[selected_profile]
            self.quality_gate.update(overrides)
            stats["quality_gate_profile"] = selected_profile
            if self.profile == "auto" and selected_profile != "default":
                print(f"  [NOTE] Quality gate auto-promoted to '{selected_profile}' "
                      f"profile (cross-module edges = "
                      f"{stats.get('cross_edges', 'n/a')}).")
                # Adjustments after the print line so the user sees the
                # threshold that will actually be applied below.
                if "min_high_value_ratio" in overrides:
                    print(f"         min_high_value_ratio = "
                          f"{overrides['min_high_value_ratio']}")
        elif selected_profile:
            stats["quality_gate_profile"] = selected_profile

        failures = self._quality_gate_failures(stats, filtered)
        stats["quality_gate_pass"] = not failures
        stats["quality_gate_failures"] = failures
        self._print_quality_gate(stats)
        print()

        if failures and not self.allow_warn:
            # Hand the caller the Stage 1-3 output so --allow-warn writes
            # validated/deduped/filtered contracts rather than the raw input.
            raise QualityGateError(failures, stats, partial_contracts=filtered)

        # Stage 4.5: hash involved files so freshness_checker can detect
        # content drift later. The step is silent when hash_involved is
        # False or no contracts had resolvable files.
        if self.hash_involved:
            self._hash_files(filtered)

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

        stats = {
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
        # Stage 0 diagnostic counters travel into stats so the JSON output
        # carries them and downstream consumers (auto-profile selector,
        # dashboards) can read why enrichment had the shape it did.
        if self._stage0_diagnostics:
            stats.update(self._stage0_diagnostics)
        return stats

    def _hash_files(self, contracts: list[dict]) -> None:
        """Populate `contract['file_hashes']` with sha256 of involved and
        affected paths. Skip files over `self.max_hash_bytes` and files
        the validator could not resolve (deleted, ignored by include
        globs, etc.). Existing `file_hashes` keys are preserved when the
        file is unresolvable now -- removing them would silently mask
        already-tracked staleness for paths that just got renamed.
        """
        hashed_total = 0
        for contract in contracts:
            existing = contract.get("file_hashes") or {}
            hashes: dict[str, str] = dict(existing) if isinstance(existing, dict) else {}
            paths: list[str] = []
            paths.extend(extract_contract_paths(contract, "involved_files"))
            paths.extend(extract_contract_paths(contract, "affected_external_files"))
            for rel in paths:
                abs_path = self.validator.resolve_absolute(rel)
                if abs_path is None:
                    continue
                try:
                    size = abs_path.stat().st_size
                except OSError:
                    continue
                if size > self.max_hash_bytes:
                    continue
                h = hashlib.sha256()
                try:
                    with open(abs_path, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                except OSError:
                    continue
                hashes[rel] = h.hexdigest()
                hashed_total += 1
            if hashes:
                contract["file_hashes"] = hashes
        if hashed_total:
            print(f"  [hash] sha256 written for {hashed_total} file references")

    def _make_provider(self):
        """Construct the graph provider for Stage 0, or None when none
        is configured. Explicit graph_provider config wins over the
        legacy repomap_l3 shortcut.
        """
        gp = self.graph_provider or {}
        gp_type = gp.get("type")
        if gp_type == "grep_fallback":
            from providers.grep_provider import GrepProvider
            repo_root = (
                self.repo_root_for_grep
                or gp.get("repo_root")
                or os.getcwd()
            )
            return GrepProvider(
                repo_root=repo_root,
                include_dirs=gp.get("include_dirs"),
            )
        if gp_type == "repomap_l3":
            from repomap_bridge import RepoMapBridge
            path = gp.get("path") or self.repomap_l3
            if not path:
                return None
            return RepoMapBridge(path)
        if gp_type and gp_type not in ("repomap_l3", "grep_fallback"):
            raise ValueError(
                f"Unknown graph_provider.type: {gp_type!r}. "
                f"Supported: repomap_l3, grep_fallback."
            )
        # No explicit provider; fall back to repomap_l3 path argument if set.
        if self.repomap_l3:
            from repomap_bridge import RepoMapBridge
            return RepoMapBridge(self.repomap_l3)
        return None

    def _resolve_profile(self, stats: dict) -> str:
        """Return the profile name to apply.

        - Explicit non-auto profile -> use it as-is.
        - 'auto' -> 'leaf' when Stage 0 confirmed the module is isolated
          (saw internal symbols but zero cross-module edges), otherwise
          'default'.
        - When Stage 0 did not run (no repomap_l3 configured) auto cannot
          tell isolated from default; we conservatively return 'default'.
        """
        if self.profile != "auto":
            return self.profile
        internal = stats.get("internal_class_count", 0)
        cross = stats.get("cross_edges", -1)  # -1 = stage 0 skipped
        if cross == 0 and internal > 0:
            return "leaf"
        return "default"

    def _quality_gate_failures(self, stats: dict, contracts: list[dict]) -> list[str]:
        gate = self.quality_gate
        failures = []
        if stats["effective"] < int(gate.get("min_effective", 0)):
            failures.append(
                f"effective contracts {stats['effective']} < {gate.get('min_effective')}"
            )
        if stats["total"] > int(gate.get("max_contracts", 10**9)):
            failures.append(
                f"total contracts {stats['total']} > {gate.get('max_contracts')}"
            )
        min_ratio = float(gate.get("min_high_value_ratio", 0))
        if stats["p0_p1_ratio"] < min_ratio:
            failures.append(
                f"high-value ratio {stats['p0_p1_ratio']} < {min_ratio}"
            )

        require_evidence_for = set(gate.get("require_evidence_for", []) or [])
        for c in contracts:
            if c.get("type") in require_evidence_for and not c.get("evidence"):
                failures.append(f"missing evidence for {c.get('type')}: {c.get('title')}")

        # Phase C #8: strong-evidence ratchet. design_rationale-only
        # evidence is insufficient for the configured types. Imported
        # lazily to avoid a hard dependency on contract_validator at
        # module import time (process() already needs it; this is just
        # the symbol).
        require_strong_for = set(gate.get("require_strong_evidence_for", []) or [])
        if require_strong_for:
            from contract_validator import STRONG_EVIDENCE_KINDS
            for c in contracts:
                if c.get("type") not in require_strong_for:
                    continue
                evidence = c.get("evidence") or []
                has_strong = any(
                    isinstance(ev, dict)
                    and ev.get("kind") in STRONG_EVIDENCE_KINDS
                    for ev in evidence
                )
                if not has_strong:
                    failures.append(
                        f"{c.get('type')} contract '{c.get('title')}' needs "
                        f"at least one evidence entry with kind in "
                        f"{sorted(STRONG_EVIDENCE_KINDS)} "
                        f"(design_rationale alone is insufficient)"
                    )
        return failures

    def _print_quality_gate(self, stats: dict) -> None:
        """Print quality gate results"""
        total = stats["total"]
        effective = stats["effective"]
        p0_p1_ratio = stats["p0_p1_ratio"]

        print(f"  Total: {total}, Effective (conf > 0.5): {effective}")
        print(f"  Avg confidence: {stats['avg_confidence']} (effective: {stats['avg_confidence_effective']})")
        print(f"  P0+P1 ratio: {p0_p1_ratio:.1%} [{stats['p0_p1_check']}]")
        print(f"  Quality gate: {'PASS' if stats.get('quality_gate_pass') else 'FAIL'}")

        if stats["demoted_contracts"]:
            print(f"  Demoted contracts:")
            for line in stats["demoted_contracts"]:
                print(line)

        for failure in stats.get("quality_gate_failures", []):
            print(f"  [FAIL] {failure}")


def main():
    parser = argparse.ArgumentParser(description="Code Oracle Pipeline")
    parser.add_argument("--input", required=True, help="Round 3 output JSON")
    parser.add_argument("--module-name", required=True, help="Module name (e.g. MyModule)")
    parser.add_argument("--source-root", help="Source root for file validation")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--dedup-threshold", type=float, default=0.8, help="Dedup threshold")
    parser.add_argument("--repomap-l3", help="Path to repomap-L3-relations.md for L3 enrichment")
    parser.add_argument("--config", help="Path to oracle.config.json")
    parser.add_argument("--allow-warn", action="store_true", help="Write output even when quality gate fails")
    parser.add_argument(
        "--profile", default="auto",
        help="Quality gate profile (auto|default|leaf|hub|<custom>). "
             "'auto' uses Stage 0 diagnostics to pick 'leaf' for isolated modules.",
    )
    parser.add_argument(
        "--emit-legacy-kg-keys", action="store_true",
        help="Also emit the pre-v4.2 aggregated [involved_files] / "
             "[affected_external_files] observation lines for KG queries "
             "that still target the old format.",
    )
    parser.add_argument(
        "--no-hash-involved", action="store_true",
        help="Skip the sha256 step. Without it freshness_checker cannot "
             "detect content drift, only file deletion.",
    )

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

    cfg = normalize_config(load_json_config(args.config)) if args.config else normalize_config({})
    graph_provider = dict(cfg.get("graph_provider") or {})
    repomap_l3 = args.repomap_l3
    if graph_provider.get("type") == "repomap_l3":
        # Resolve relative to the config file's directory so callers can
        # invoke the pipeline from any cwd (e.g. running the oracle scripts
        # against a separate project's oracle.config.json).
        resolved = resolve_config_path(cfg, ["graph_provider", "path"])
        if resolved is not None:
            graph_provider["path"] = str(resolved)
            if not repomap_l3:
                repomap_l3 = str(resolved)

    # For grep_fallback, default repo_root_for_grep to the config's
    # directory unless the config explicitly provides one. Keeps the
    # invocation pattern symmetric with repomap_l3 -- both providers
    # interpret paths relative to where the config lives.
    repo_root_for_grep = graph_provider.get("repo_root")
    if graph_provider.get("type") == "grep_fallback" and not repo_root_for_grep:
        repo_root_for_grep = cfg.get("_config_dir") or os.getcwd()

    # Run pipeline
    pipeline = OraclePipeline(
        module_name=args.module_name,
        source_root=args.source_root,
        dedup_threshold=args.dedup_threshold,
        repomap_l3=repomap_l3,
        include=cfg.get("include"),
        exclude=cfg.get("exclude"),
        quality_gate=cfg.get("quality_gate"),
        quality_gate_profiles=cfg.get("quality_gate_profiles"),
        profile=args.profile,
        emit_legacy_kg_keys=args.emit_legacy_kg_keys,
        graph_provider=graph_provider,
        repo_root_for_grep=repo_root_for_grep,
        hash_involved=not args.no_hash_involved,
        allow_warn=args.allow_warn,
    )
    try:
        result = pipeline.process(contracts)
    except QualityGateError as exc:
        print(str(exc), file=sys.stderr)
        if not args.allow_warn:
            sys.exit(2)
        # Use the Stage 1-3 filtered contracts the exception carries -- writing
        # the raw input here would silently undo validation, dedup, and the
        # blind-spot filter.
        result = {
            "contracts": exc.partial_contracts,
            "kg_format": {"entities": [], "relations": [], "context": "code_contracts"},
            "stats": exc.stats,
        }

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

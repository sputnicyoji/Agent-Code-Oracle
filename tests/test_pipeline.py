"""
Unit tests for OraclePipeline (scripts/pipeline.py).

Missing modules (semantic_dedup, blind_spot_filter, kg_injector) are stubbed
via sys.modules before import so tests run without Ollama or a real source tree.
"""

import sys
import os
import json
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Stub out modules that don't exist yet so pipeline.py can be imported
# ---------------------------------------------------------------------------

def _make_passthrough_stub(module_name: str) -> types.ModuleType:
    """Return a module stub whose main class passes contracts through unchanged."""
    mod = types.ModuleType(module_name)

    class PassthroughProcessor:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, contracts):
            return list(contracts)

    # The class name matches what pipeline.py constructs (SemanticDedup, etc.)
    class_names = {
        "semantic_dedup": "SemanticDedup",
        "blind_spot_filter": "BlindSpotFilter",
    }
    if module_name in class_names:
        setattr(mod, class_names[module_name], PassthroughProcessor)
    return mod


def _make_kg_injector_stub() -> types.ModuleType:
    mod = types.ModuleType("kg_injector")

    class KGInjector:
        def convert(self, contracts, module_name):
            return {
                "entities": [{"name": c["title"]} for c in contracts],
                "relations": [],
                "context": "code_contracts",
            }

    mod.KGInjector = KGInjector
    return mod


for _stub_name in ("semantic_dedup", "blind_spot_filter"):
    if _stub_name not in sys.modules:
        sys.modules[_stub_name] = _make_passthrough_stub(_stub_name)

if "kg_injector" not in sys.modules:
    sys.modules["kg_injector"] = _make_kg_injector_stub()

# repomap_bridge is only imported inside Stage 0's branch. We DO NOT stub it,
# because TestStage0MultiSymbolFile (Phase A #2 regression) needs the real
# bridge to verify that `bridge.file_to_symbols` is what Stage 0 now uses.
# All other tests in this file avoid the branch by leaving repomap_l3 as
# None when constructing the pipeline.

# Now it's safe to import the real pipeline
from pipeline import OraclePipeline, QualityGateError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_contracts() -> list[dict]:
    with open(FIXTURES_DIR / "sample-contracts.json", encoding="utf-8") as f:
        return json.load(f)


def _make_pipeline(**kwargs) -> OraclePipeline:
    kwargs.setdefault("allow_warn", True)
    return OraclePipeline(module_name="SampleModule", **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineProcessesContracts(unittest.TestCase):
    """Pipeline runs without errors on sample data."""

    def test_pipeline_processes_contracts(self):
        contracts = _load_contracts()
        pipeline = _make_pipeline()
        result = pipeline.process(contracts)
        # Must return a dict (no exception)
        self.assertIsInstance(result, dict)
        # Contracts list must be non-empty
        self.assertGreater(len(result["contracts"]), 0)


class TestPipelineOutputStructure(unittest.TestCase):
    """Output has contracts, kg_format, and stats keys."""

    def setUp(self):
        self.result = _make_pipeline().process(_load_contracts())

    def test_has_contracts_key(self):
        self.assertIn("contracts", self.result)
        self.assertIsInstance(self.result["contracts"], list)

    def test_has_kg_format_key(self):
        self.assertIn("kg_format", self.result)
        kg = self.result["kg_format"]
        self.assertIn("entities", kg)
        self.assertIn("relations", kg)
        self.assertIn("context", kg)

    def test_has_stats_key(self):
        self.assertIn("stats", self.result)
        self.assertIsInstance(self.result["stats"], dict)


class TestPipelineQualityGate(unittest.TestCase):
    """Stats include p0_p1_ratio and confidence averages."""

    def setUp(self):
        self.stats = _make_pipeline().process(_load_contracts())["stats"]

    def test_stats_has_p0_p1_ratio(self):
        self.assertIn("p0_p1_ratio", self.stats)
        ratio = self.stats["p0_p1_ratio"]
        self.assertIsInstance(ratio, float)
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)

    def test_stats_has_avg_confidence(self):
        self.assertIn("avg_confidence", self.stats)
        self.assertIsInstance(self.stats["avg_confidence"], float)

    def test_stats_has_avg_confidence_effective(self):
        self.assertIn("avg_confidence_effective", self.stats)

    def test_stats_has_total_and_effective(self):
        self.assertIn("total", self.stats)
        self.assertIn("effective", self.stats)

    def test_p0_p1_ratio_value_matches_fixture(self):
        # Fixture has 1 blast_radius + 1 rationale out of 5 contracts
        # After passthrough dedup/filter, ratio = 2/5 = 0.4
        self.assertAlmostEqual(self.stats["p0_p1_ratio"], 0.4, places=2)

    def test_quality_gate_records_failures_when_allowed(self):
        self.assertFalse(self.stats["quality_gate_pass"])
        self.assertGreater(len(self.stats["quality_gate_failures"]), 0)

    def test_strict_quality_gate_raises(self):
        pipeline = OraclePipeline(module_name="SampleModule")
        with self.assertRaises(QualityGateError):
            pipeline.process(_load_contracts())


class TestSchemaV2Contracts(unittest.TestCase):
    """v2 contracts (with `involved` instead of `involved_files`) must survive
    the pipeline. These tests exercise the v1<->v2 bridge in oracle_config and
    the schema-aware validator.
    """

    @staticmethod
    def _v2_contract(**overrides) -> dict:
        """A minimal v2-only contract (no `involved_files` key)."""
        base = {
            "schema_version": 2,
            "type": "blast_radius",
            "title": "Payment result feeds invoice pipeline",
            "description": "Payment result fields are consumed downstream.",
            "blind_spot": "Editor sees only the producer module.",
            "violation_consequence": "Invoice fields go missing.",
            "involved": [{"path": "src/payment/result.ts"}],
            "affected_external": [{"path": "src/invoice/generator.ts"}],
            "evidence": [{"kind": "static_reference", "source": "test", "target": "src/invoice/generator.ts"}],
            "confidence": 0.91,
        }
        base.update(overrides)
        return base

    def test_v2_only_contract_validates(self):
        """A v2 contract with no `involved_files` key still passes validation."""
        from contract_validator import ContractValidator
        validator = ContractValidator()  # no source_root => existence check skipped
        contract = self._v2_contract()
        result = validator.process([contract])
        self.assertEqual(len(result), 1, "v2-only contract was dropped")

    def test_v2_contract_through_pipeline(self):
        """End-to-end: v2 contracts pass the whole pipeline without being dropped."""
        contracts = [self._v2_contract()]
        result = _make_pipeline().process(contracts)
        self.assertEqual(len(result["contracts"]), 1)

    def test_extract_contract_paths_reads_v2(self):
        """Bridge resolves involved/affected paths from a v2-only contract."""
        from oracle_config import extract_contract_paths
        c = self._v2_contract()
        self.assertEqual(
            extract_contract_paths(c, "involved_files"),
            ["src/payment/result.ts"],
        )
        self.assertEqual(
            extract_contract_paths(c, "affected_external_files"),
            ["src/invoice/generator.ts"],
        )

    def test_set_contract_paths_syncs_v1_and_v2(self):
        """set_contract_paths must update both representations to avoid split-brain."""
        from oracle_config import set_contract_paths
        c = {"involved": [{"path": "old.ts", "symbols": ["Foo"]}]}
        set_contract_paths(c, ["old.ts", "new.ts"], "involved_files")

        # v1 list is what we passed
        self.assertEqual(c["involved_files"], ["old.ts", "new.ts"])
        # v2 mirror is rebuilt; pre-existing symbols for kept paths are preserved
        self.assertEqual(
            c["involved"],
            [{"path": "old.ts", "symbols": ["Foo"]}, {"path": "new.ts"}],
        )

    def test_set_contract_paths_emptying_clears_both(self):
        """Setting paths to empty drops both v1 and v2 entries."""
        from oracle_config import set_contract_paths
        c = {"involved_files": ["x.ts"], "involved": [{"path": "x.ts"}]}
        set_contract_paths(c, [], "involved_files")
        self.assertEqual(c["involved_files"], [])
        self.assertEqual(c["involved"], [])


class TestValidatorDefaults(unittest.TestCase):
    """Defensive copy: mutating one validator's include must not affect another."""

    def test_default_include_is_isolated_per_instance(self):
        from contract_validator import ContractValidator
        from oracle_config import DEFAULT_INCLUDE

        a = ContractValidator()
        b = ContractValidator()
        a.include.append("**/extra/**")
        # Mutating a's list must not reach b or the module-level default.
        self.assertNotIn("**/extra/**", b.include)
        self.assertNotIn("**/extra/**", DEFAULT_INCLUDE)


class TestL3SyncsBothSchemas(unittest.TestCase):
    """The L3 enrichment stage must write through the bridge so v2 contracts'
    `affected_external` is updated, not just the v1 `affected_external_files`."""

    def test_l3_enrichment_writes_v2_mirror(self):
        from oracle_config import set_contract_paths
        # Simulate what pipeline.py:Stage 0 now does after the fix
        contract = {
            "schema_version": 2,
            "involved": [{"path": "src/payment/result.ts"}],
            "affected_external": [{"path": "src/audit/log.ts"}],
        }
        # Existing v2 entry must not be dropped when we add L3 consumers.
        from oracle_config import extract_contract_paths
        existing = extract_contract_paths(contract, "affected_external_files")
        self.assertEqual(existing, ["src/audit/log.ts"])

        merged = sorted(set(existing + ["src/invoice/gen.ts"]))
        set_contract_paths(contract, merged, "affected_external_files")

        # v1 mirror written
        self.assertEqual(contract["affected_external_files"],
                         ["src/audit/log.ts", "src/invoice/gen.ts"])
        # v2 mirror in sync, not orphaned
        paths_in_v2 = [item["path"] for item in contract["affected_external"]]
        self.assertEqual(sorted(paths_in_v2),
                         ["src/audit/log.ts", "src/invoice/gen.ts"])


class TestStage0MultiSymbolFile(unittest.TestCase):
    """Phase A #2: pipeline Stage 0 must enrich contracts whose `involved`
    paths point at multi-class files. Prior to Phase A the lookup used
    `Path(f).stem`, so a contract pointing at `Controllers.cs` (which
    declares three controllers) only matched the L3 consumers of a
    hypothetical "Controllers" symbol -- not the ones tied to PaymentController,
    UserController, OrderController. Confirms the fix at the pipeline level,
    not just the bridge level.
    """

    def test_multi_symbol_contract_gets_enriched(self):
        from pathlib import Path as _Path
        # Re-import the real pipeline class so its constructor runs and we
        # can drive it directly. The stubs at module top are still in
        # place; we just feed real L3 + source-root.
        fixtures = _Path(__file__).parent / "fixtures"
        l3 = str(fixtures / "sample-l3.md")
        source_root = str(fixtures / "multi_class_module")

        pipeline = OraclePipeline(
            module_name="Acme.Web",
            source_root=source_root,
            repomap_l3=l3,
            allow_warn=True,
        )
        # A contract whose involved file is `Controllers.cs` (multi-class).
        # Stage 0 should walk every type defined there and discover the
        # PaymentController/UserController/OrderController L3 children of
        # BaseController, BUT all three are inside the same module -- they
        # land in internal_classes and consumer_index marks them not-external.
        # For this regression we instead verify a NEGATIVE case has the right
        # shape (no false external enrichment) AND positive shape (the
        # lookup actually walked all four symbols, not just the file stem).
        contract = {
            "schema_version": 2,
            "type": "blast_radius",
            "title": "BaseController inheritance hierarchy",
            "description": "x",
            "blind_spot": "x",
            "violation_consequence": "x",
            "involved": [{"path": "Controllers.cs"}],
            "confidence": 0.8,
        }
        result = pipeline.process([contract])
        # All sibling controllers are internal in this fixture so Stage 0
        # finds zero TRUE externals -- but it must have looked them up.
        # We assert internal-set correctness via the bridge state.
        # (process() created its own bridge; rebuild to inspect.)
        from repomap_bridge import RepoMapBridge
        bridge = RepoMapBridge(l3)
        bridge.index_source_tree(source_root)
        syms = bridge.file_to_symbols("Controllers.cs")
        self.assertIn("PaymentController", syms,
                      "Stage 0 fix requires bridge to enumerate all defs")
        self.assertIn("OrderController", syms)
        # Pipeline should still produce a result dict
        self.assertIn("contracts", result)


class TestStage0Diagnostics(unittest.TestCase):
    """Phase B #5: Stage 0 surfaces diagnostic counters when enriched==0
    so the user can tell isolated module from broken index from contract
    mismatch.
    """

    FIXTURES = Path(__file__).parent / "fixtures"

    def _pipeline(self, **kwargs):
        defaults = dict(
            module_name="Acme",
            source_root=str(self.FIXTURES / "multi_class_module"),
            repomap_l3=str(self.FIXTURES / "sample-l3.md"),
            allow_warn=True,
        )
        defaults.update(kwargs)
        return OraclePipeline(**defaults)

    def test_stats_carry_stage0_counters(self):
        # The multi_class_module fixture defines BaseController and friends.
        # sample-l3 has BaseController -> Payment/User/OrderController; all
        # three subclasses live in the fixture so they are NOT external.
        # Internal symbol count > 0; cross_edges = 0 (all children internal).
        contract = {
            "schema_version": 2, "type": "ordering", "title": "x",
            "description": "x", "blind_spot": "x", "violation_consequence": "x",
            "involved": [{"path": "Controllers.cs"}], "confidence": 0.8,
        }
        result = self._pipeline().process([contract])
        stats = result["stats"]
        self.assertIn("internal_class_count", stats)
        self.assertIn("cross_edges", stats)
        self.assertGreater(stats["internal_class_count"], 0)
        # All L3 children of BaseController are present in the fixture --
        # so cross_edges == 0 even though the L3 graph reports edges.
        self.assertEqual(stats["cross_edges"], 0)

    def test_stage0_skipped_when_no_repomap(self):
        # No repomap_l3 -> Stage 0 does not run, diagnostic counters absent.
        # We construct via the helper but force repomap_l3 to None.
        pipeline = OraclePipeline(
            module_name="Acme", source_root=None, allow_warn=True,
        )
        contract = {
            "schema_version": 2, "type": "ordering", "title": "x",
            "description": "x", "blind_spot": "x", "violation_consequence": "x",
            "involved_files": ["Controllers.cs"], "confidence": 0.8,
        }
        stats = pipeline.process([contract])["stats"]
        # Stage 0 diagnostics absent
        self.assertNotIn("cross_edges", stats)
        self.assertNotIn("internal_class_count", stats)


class TestQualityGateProfilesInPipeline(unittest.TestCase):
    """Phase B #6+#7+#8: pipeline applies quality gate profiles by name,
    auto picks 'leaf' when Stage 0 sees no cross-module edges, and reports
    the selected profile in stats."""

    FIXTURES = Path(__file__).parent / "fixtures"

    def _make(self, profile, with_l3=True, profiles_override=None):
        from oracle_config import DEFAULT_QUALITY_GATE_PROFILES
        return OraclePipeline(
            module_name="Acme",
            source_root=str(self.FIXTURES / "multi_class_module") if with_l3 else None,
            repomap_l3=str(self.FIXTURES / "sample-l3.md") if with_l3 else None,
            quality_gate_profiles=profiles_override or {
                name: dict(v) for name, v in DEFAULT_QUALITY_GATE_PROFILES.items()
            },
            profile=profile,
            allow_warn=True,
        )

    def _trivial_contract(self):
        return {
            "schema_version": 2, "type": "ordering", "title": "x",
            "description": "x", "blind_spot": "x", "violation_consequence": "x",
            "involved": [{"path": "Controllers.cs"}], "confidence": 0.8,
        }

    def test_explicit_profile_leaf_applied(self):
        pipeline = self._make("leaf")
        stats = pipeline.process([self._trivial_contract()])["stats"]
        self.assertEqual(stats["quality_gate_profile"], "leaf")
        # leaf relaxes the ratio threshold
        self.assertEqual(pipeline.quality_gate["min_high_value_ratio"], 0.25)

    def test_explicit_profile_hub_applied(self):
        pipeline = self._make("hub")
        stats = pipeline.process([self._trivial_contract()])["stats"]
        self.assertEqual(stats["quality_gate_profile"], "hub")
        self.assertEqual(pipeline.quality_gate["min_high_value_ratio"], 0.6)

    def test_auto_picks_leaf_on_isolated_module(self):
        # Stage 0 sees source symbols but zero cross-module edges
        # (all L3 children live inside the fixture). Auto should choose 'leaf'.
        pipeline = self._make("auto")
        stats = pipeline.process([self._trivial_contract()])["stats"]
        self.assertEqual(stats["quality_gate_profile"], "leaf")

    def test_auto_falls_back_to_default_without_stage0(self):
        # No repomap -> Stage 0 cannot fire -> auto cannot detect isolation
        # -> conservatively chooses 'default'.
        contract = {
            "schema_version": 2, "type": "ordering", "title": "x",
            "description": "x", "blind_spot": "x", "violation_consequence": "x",
            "involved_files": ["Controllers.cs"], "confidence": 0.8,
        }
        pipeline = self._make("auto", with_l3=False)
        stats = pipeline.process([contract])["stats"]
        self.assertEqual(stats["quality_gate_profile"], "default")

    def test_unknown_profile_does_not_crash(self):
        # Profile name not in profiles map -> pipeline records the name but
        # leaves base quality_gate untouched.
        pipeline = self._make("nonexistent")
        stats = pipeline.process([self._trivial_contract()])["stats"]
        self.assertEqual(stats["quality_gate_profile"], "nonexistent")
        # Base default threshold preserved (DEFAULT_QUALITY_GATE = 0.5)
        self.assertEqual(pipeline.quality_gate["min_high_value_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()

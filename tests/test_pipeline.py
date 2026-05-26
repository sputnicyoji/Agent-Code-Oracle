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

# repomap_bridge is only imported inside a branch; stub it defensively
if "repomap_bridge" not in sys.modules:
    sys.modules["repomap_bridge"] = types.ModuleType("repomap_bridge")

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


if __name__ == "__main__":
    unittest.main()

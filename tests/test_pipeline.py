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
from pipeline import OraclePipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_contracts() -> list[dict]:
    with open(FIXTURES_DIR / "sample-contracts.json", encoding="utf-8") as f:
        return json.load(f)


def _make_pipeline(**kwargs) -> OraclePipeline:
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


if __name__ == "__main__":
    unittest.main()

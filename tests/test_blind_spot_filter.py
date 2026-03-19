"""
Unit tests for BlindSpotFilter (scripts/blind_spot_filter.py).

Real implementation behaviour (from blind_spot_filter.py):
  - R1: rationale type where (title + description + blind_spot) has >= 3 thread-safety
        keyword hits -> _filter_tag = "R1:thread_safety_disguise", confidence = DEMOTE_CONFIDENCE (0.4)
  - R3: thread_safety type -> always demoted (confidence = max(0.5, old - 0.2))
  - Clean rationale without enough keyword hits -> no tag, confidence unchanged
"""

import sys
import importlib.util
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Load the real module directly by path to avoid sys.modules stub pollution
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location(
    "blind_spot_filter_real", str(SCRIPTS_DIR / "blind_spot_filter.py")
)
if _spec and _spec.loader:
    _bsf_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_bsf_mod)
    BlindSpotFilter = _bsf_mod.BlindSpotFilter
    DEMOTE_CONFIDENCE = _bsf_mod.DEMOTE_CONFIDENCE
    THREAD_SAFETY_MIN_HITS = _bsf_mod.THREAD_SAFETY_MIN_HITS
else:
    raise ImportError("Cannot load blind_spot_filter.py from scripts/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(type_: str, title: str, description: str = "generic description",
              blind_spot: str = "generic blind spot",
              confidence: float = 0.88) -> dict:
    return {
        "type": type_,
        "title": title,
        "description": description,
        "blind_spot": blind_spot,
        "violation_consequence": "test",
        "involved_files": ["Foo.cs"],
        "confidence": confidence,
    }


def _make_thread_heavy_contract(type_: str = "rationale",
                                confidence: float = 0.88) -> dict:
    """
    Build a contract whose combined text contains >= THREAD_SAFETY_MIN_HITS
    distinct thread-safety keywords (guaranteed to trigger R1).
    Uses three distinct English keywords: thread, concurrent, atomic.
    """
    return {
        "type": type_,
        "title": "Dispatch uses thread-safe concurrent channel",
        "description": "Designed for thread safety using an atomic counter",
        "blind_spot": "Replacing with non-concurrent implementation breaks guarantees",
        "violation_consequence": "test",
        "involved_files": ["Foo.cs"],
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestThreadSafetyDisguiseDemoted(unittest.TestCase):
    """R1: rationale with enough thread_safety keywords gets demoted."""

    def _run(self, contract: dict) -> dict:
        result = BlindSpotFilter().process([contract])
        self.assertEqual(len(result), 1)
        return result[0]

    def test_rationale_with_three_thread_keywords_gets_tag(self):
        """R1 triggers when title+description+blind_spot has >= 3 keyword hits."""
        c = _make_thread_heavy_contract("rationale")
        out = self._run(c)
        self.assertIn("_filter_tag", out, "Expected _filter_tag to be set for R1")

    def test_r1_tag_value(self):
        c = _make_thread_heavy_contract("rationale")
        out = self._run(c)
        self.assertIn("thread_safety", out.get("_filter_tag", ""))

    def test_demoted_confidence_is_lower(self):
        original_conf = 0.88
        c = _make_thread_heavy_contract("rationale", confidence=original_conf)
        out = self._run(c)
        self.assertLess(out["confidence"], original_conf)

    def test_demoted_confidence_equals_demote_constant(self):
        c = _make_thread_heavy_contract("rationale", confidence=0.9)
        out = self._run(c)
        self.assertAlmostEqual(out["confidence"], DEMOTE_CONFIDENCE)

    def test_demoted_confidence_not_negative(self):
        c = _make_thread_heavy_contract("rationale", confidence=0.1)
        out = self._run(c)
        self.assertGreaterEqual(out["confidence"], 0.0)

    def test_non_rationale_type_not_r1_tagged(self):
        """R1 only fires for rationale type, not for other types."""
        c = _make_thread_heavy_contract("blast_radius")
        out = self._run(c)
        # blast_radius may get R3 if it were thread_safety type, but not R1
        tag = out.get("_filter_tag", "")
        self.assertNotIn("R1", tag, "R1 should not fire for non-rationale types")

    def test_thread_safety_type_gets_r3_not_r1(self):
        """thread_safety type gets R3 global demotion, not R1."""
        c = _make_thread_heavy_contract("thread_safety")
        out = self._run(c)
        tag = out.get("_filter_tag", "")
        # R3 should fire, R1 should not
        self.assertIn("R3", tag, "thread_safety type should get R3 tag")
        self.assertNotIn("R1", tag)


class TestCleanRationalePreserved(unittest.TestCase):
    """Rationale without thread_safety keywords keeps confidence."""

    def test_clean_rationale_no_tag(self):
        c = _contract(
            "rationale",
            "EventBus uses synchronous dispatch intentionally",
            description="Synchronous dispatch ensures ordering guarantees for dependent handlers",
        )
        result = BlindSpotFilter().process([c])
        self.assertEqual(len(result), 1)
        self.assertNotIn("_filter_tag", result[0])

    def test_clean_rationale_confidence_unchanged(self):
        original_conf = 0.88
        c = _contract(
            "rationale",
            "EventBus uses synchronous dispatch intentionally",
            confidence=original_conf,
        )
        result = BlindSpotFilter().process([c])
        self.assertAlmostEqual(result[0]["confidence"], original_conf)

    def test_rationale_with_only_one_keyword_not_demoted(self):
        """Only 1 keyword hit — below THREAD_SAFETY_MIN_HITS threshold."""
        c = _contract(
            "rationale",
            "ConnectionPool design rationale",
            description="Uses a pool pattern for performance",
            blind_spot="One thread may contend, but this is acceptable",
        )
        result = BlindSpotFilter().process([c])
        # 'thread' appears once in blind_spot → 1 hit < 3 → no R1
        self.assertNotIn("_filter_tag", result[0])

    def test_blast_radius_never_r1_tagged(self):
        c = _contract("blast_radius", "PaymentGateway output affects OrderService")
        result = BlindSpotFilter().process([c])
        tag = result[0].get("_filter_tag", "")
        self.assertNotIn("R1", tag)

    def test_ordering_never_r1_tagged(self):
        c = _contract("ordering", "DatabaseMigration must complete before SchemaValidator")
        result = BlindSpotFilter().process([c])
        tag = result[0].get("_filter_tag", "")
        self.assertNotIn("R1", tag)

    def test_data_flow_never_r1_tagged(self):
        c = _contract("data_flow", "UserSession flows through auth pipeline")
        result = BlindSpotFilter().process([c])
        tag = result[0].get("_filter_tag", "")
        self.assertNotIn("R1", tag)

    def test_empty_input_returns_empty(self):
        result = BlindSpotFilter().process([])
        self.assertEqual(result, [])

    def test_all_contracts_returned(self):
        """Filter never drops contracts — it only demotes (lowers confidence)."""
        contracts = [
            _contract("blast_radius", "PaymentGateway blast radius"),
            _contract("rationale", "EventBus synchronous dispatch"),
            _contract("data_flow", "UserSession validity"),
        ]
        result = BlindSpotFilter().process(contracts)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()

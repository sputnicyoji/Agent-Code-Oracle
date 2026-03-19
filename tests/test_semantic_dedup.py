"""
Unit tests for SemanticDedup (scripts/semantic_dedup.py).

Real implementation (semantic_dedup.py v4.1):
  - Pass 1 (same-type): title similarity via difflib > threshold (default 0.8)
                        → keep higher confidence
  - Pass 2 (cross-type): involved_files overlap >= cross_type_file_overlap (default 0.6)
                         AND description similarity > cross_type_desc_threshold (default 0.5)
                         → keep higher-priority type (blast_radius < rationale < data_flow < ...)
  - Pass 3 (embedding): Ollama bge-m3 optional, silently skipped when unavailable
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
    "semantic_dedup_real", str(SCRIPTS_DIR / "semantic_dedup.py")
)
if _spec and _spec.loader:
    _sd_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_sd_mod)
    SemanticDedup = _sd_mod.SemanticDedup
else:
    raise ImportError("Cannot load semantic_dedup.py from scripts/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(type_: str, title: str, files: list[str],
              confidence: float = 0.9,
              description: str = "default description for testing purposes") -> dict:
    return {
        "type": type_,
        "title": title,
        "description": description,
        "blind_spot": "test blind spot",
        "violation_consequence": "test consequence",
        "involved_files": files,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSameTypeDedup(unittest.TestCase):
    """Contracts with same type and similar title are deduplicated."""

    def test_exact_duplicate_removed(self):
        contracts = [
            _contract("blast_radius", "PaymentGateway output affects OrderService", ["PaymentGateway.cs"], 0.9),
            _contract("blast_radius", "PaymentGateway output affects OrderService", ["PaymentGateway.cs"], 0.7),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 1)

    def test_higher_confidence_kept_on_dedup(self):
        contracts = [
            _contract("blast_radius", "PaymentGateway output affects OrderService", ["A.cs"], 0.7),
            _contract("blast_radius", "PaymentGateway output affects OrderService", ["A.cs"], 0.95),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["confidence"], 0.95)

    def test_different_titles_not_deduped(self):
        contracts = [
            _contract("rationale", "EventBus uses synchronous dispatch", ["EventBus.cs"], 0.88),
            _contract("rationale", "ConnectionPool uses lock-free queue", ["ConnectionPool.cs"], 0.78),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 2)

    def test_all_unique_contracts_preserved(self):
        contracts = [
            _contract("blast_radius", "PaymentGateway affects OrderService", ["A.cs"]),
            _contract("rationale", "EventBus synchronous dispatch rationale", ["B.cs"]),
            _contract("data_flow", "UserSession validity window in request lifecycle", ["C.cs"]),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 3)


class TestCrossTypeDedup(unittest.TestCase):
    """
    Cross-type dedup: contracts with overlapping files AND similar descriptions
    are deduplicated (real impl checks description similarity, not title similarity).
    """

    def test_cross_type_overlap_deduped(self):
        """Same files + very similar descriptions + different types → deduped."""
        shared_desc = "ConnectionPool manages connections using a concurrent data structure for safe access"
        contracts = [
            _contract("blast_radius", "ConnectionPool concurrent access risk",
                      ["ConnectionPool.cs"], 0.9, description=shared_desc),
            _contract("thread_safety", "ConnectionPool concurrent access issue",
                      ["ConnectionPool.cs"], 0.8, description=shared_desc),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 1)

    def test_cross_type_higher_priority_type_kept(self):
        """blast_radius (priority 0) wins over thread_safety (priority 4)."""
        shared_desc = "ConnectionPool manages connections using a concurrent queue for safe shared access"
        contracts = [
            _contract("blast_radius", "ConnectionPool design",
                      ["ConnectionPool.cs"], 0.85, description=shared_desc),
            _contract("thread_safety", "ConnectionPool concurrency",
                      ["ConnectionPool.cs"], 0.9, description=shared_desc),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "blast_radius")

    def test_cross_type_no_file_overlap_kept(self):
        """No file overlap → not a duplicate even with very similar descriptions."""
        same_desc = "Service output blast radius affects all downstream consumers of the API"
        contracts = [
            _contract("blast_radius", "ServiceA output affects consumers",
                      ["ServiceA.cs"], 0.9, description=same_desc),
            _contract("data_flow", "ServiceB output affects consumers",
                      ["ServiceB.cs"], 0.85, description=same_desc),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 2)

    def test_cross_type_different_descriptions_kept(self):
        """Same files but descriptions below similarity threshold → not a duplicate."""
        contracts = [
            _contract("blast_radius", "PaymentGateway output blast radius",
                      ["Gateway.cs"], 0.9,
                      description="PaymentGateway result structure is consumed by multiple downstream services"),
            _contract("thread_safety", "ConnectionPool locking strategy",
                      ["Gateway.cs"], 0.8,
                      description="ConnectionPool uses a ConcurrentQueue to prevent race conditions under load"),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertEqual(len(result), 2)


class TestEmbeddingFallback(unittest.TestCase):
    """When Ollama unavailable, only difflib passes run (no crash)."""

    def test_no_crash_without_ollama(self):
        """SemanticDedup must not raise even if Ollama is unreachable."""
        contracts = [
            _contract("blast_radius", "PaymentGateway output affects OrderService", ["A.cs"]),
            _contract("rationale", "EventBus synchronous dispatch rationale", ["B.cs"]),
        ]
        dedup = SemanticDedup(threshold=0.8)
        try:
            result = dedup.process(contracts)
        except Exception as exc:
            self.fail(f"SemanticDedup raised unexpectedly without Ollama: {exc}")
        self.assertIsInstance(result, list)

    def test_difflib_path_returns_list(self):
        """Return value is always a list, even on empty input."""
        result = SemanticDedup(threshold=0.8).process([])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_result_count_not_greater_than_input(self):
        """Dedup never creates new contracts."""
        contracts = [
            _contract("blast_radius", "A affects B", ["A.cs"]),
            _contract("rationale", "C uses D design", ["C.cs"]),
            _contract("ordering", "E before F", ["E.cs"]),
        ]
        result = SemanticDedup(threshold=0.8).process(contracts)
        self.assertLessEqual(len(result), len(contracts))


if __name__ == "__main__":
    unittest.main()

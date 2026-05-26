"""
Unit tests for RepoMapBridge (scripts/repomap_bridge.py).

Real implementation API:
    bridge.get_consumers(class_name) -> list[dict]
        Each dict has keys: "name" (str), "type" (str, relation type e.g. "inherits")

    bridge.get_high_impact_classes(min_refs=10) -> list[tuple[str, int]]
        Returns (class_name, refs) tuples sorted by refs descending,
        filtered to refs >= min_refs

    bridge.get_class_info(class_name) -> dict | None
        Returns {"refs": int, "rank": float, "children": [...], "parents": [...]}
"""

import sys
import importlib.util
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

SAMPLE_L3 = FIXTURES_DIR / "sample-l3.md"

# ---------------------------------------------------------------------------
# Load the real module directly by path to avoid sys.modules stub pollution
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location(
    "repomap_bridge_real", str(SCRIPTS_DIR / "repomap_bridge.py")
)
if _spec and _spec.loader:
    _rb_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_rb_mod)
    RepoMapBridge = _rb_mod.RepoMapBridge
else:
    raise ImportError("Cannot load repomap_bridge.py from scripts/")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseSampleL3(unittest.TestCase):
    """Parses sample L3 file correctly."""

    def setUp(self):
        self.bridge = RepoMapBridge(str(SAMPLE_L3))

    def test_base_controller_parsed(self):
        consumers = self.bridge.get_consumers("BaseController")
        self.assertGreater(len(consumers), 0, "BaseController should have consumers")

    def test_all_four_root_classes_found(self):
        # Sample L3 has: BaseController, IRepository, EventBus, BaseService
        for class_name in ("BaseController", "IRepository", "EventBus", "BaseService"):
            info = self.bridge.get_class_info(class_name)
            self.assertIsNotNone(info, f"{class_name} should be parseable")

    def test_unknown_class_returns_empty(self):
        result = self.bridge.get_consumers("NonExistentClass")
        self.assertEqual(result, [])

    def test_consumers_are_dicts(self):
        """get_consumers() returns list[dict] with 'name' and 'type' keys."""
        consumers = self.bridge.get_consumers("EventBus")
        for c in consumers:
            self.assertIsInstance(c, dict)
            self.assertIn("name", c)
            self.assertIn("type", c)

    def test_class_info_has_expected_keys(self):
        info = self.bridge.get_class_info("BaseController")
        self.assertIsNotNone(info)
        self.assertIn("refs", info)
        self.assertIn("rank", info)
        self.assertIn("children", info)


class TestGetConsumers(unittest.TestCase):
    """Returns inheriting/implementing classes for a given class."""

    def setUp(self):
        self.bridge = RepoMapBridge(str(SAMPLE_L3))

    def _consumer_names(self, class_name: str) -> list[str]:
        return [c["name"] for c in self.bridge.get_consumers(class_name)]

    def test_base_controller_has_three_consumers(self):
        consumers = self.bridge.get_consumers("BaseController")
        self.assertEqual(len(consumers), 3)

    def test_base_controller_consumers_correct(self):
        names = self._consumer_names("BaseController")
        self.assertIn("PaymentController", names)
        self.assertIn("UserController", names)
        self.assertIn("OrderController", names)

    def test_event_bus_consumers(self):
        names = self._consumer_names("EventBus")
        self.assertIn("OrderService", names)
        self.assertIn("NotificationService", names)

    def test_irepository_consumers(self):
        names = self._consumer_names("IRepository")
        self.assertIn("UserRepository", names)
        self.assertIn("OrderRepository", names)

    def test_base_service_consumers(self):
        names = self._consumer_names("BaseService")
        self.assertIn("PaymentGateway", names)
        self.assertIn("InvoiceGenerator", names)
        self.assertIn("AuthMiddleware", names)

    def test_relation_type_recorded(self):
        """Relation type (inherits/implements) is stored in each consumer dict."""
        consumers = self.bridge.get_consumers("BaseController")
        relation_types = {c["type"] for c in consumers}
        self.assertIn("inherits", relation_types)


class TestHighImpactClasses(unittest.TestCase):
    """Returns classes sorted by ref count."""

    def setUp(self):
        self.bridge = RepoMapBridge(str(SAMPLE_L3))

    def test_returns_list(self):
        result = self.bridge.get_high_impact_classes(min_refs=1)
        self.assertIsInstance(result, list)

    def test_sorted_by_refs_descending(self):
        result = self.bridge.get_high_impact_classes(min_refs=1)
        refs = [item[1] for item in result]
        self.assertEqual(refs, sorted(refs, reverse=True))

    def test_top1_is_base_controller(self):
        # BaseController has refs=45, the highest in the fixture
        result = self.bridge.get_high_impact_classes(min_refs=1)
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0][0], "BaseController")

    def test_result_items_are_tuples(self):
        result = self.bridge.get_high_impact_classes(min_refs=1)
        for item in result:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)
            self.assertIsInstance(item[0], str)  # class name
            self.assertIsInstance(item[1], int)  # refs count

    def test_min_refs_filter_works(self):
        """min_refs filters out classes below threshold."""
        all_results = self.bridge.get_high_impact_classes(min_refs=1)
        filtered = self.bridge.get_high_impact_classes(min_refs=25)
        self.assertLessEqual(len(filtered), len(all_results))
        for _, refs in filtered:
            self.assertGreaterEqual(refs, 25)

    def test_all_four_classes_returned_with_low_min_refs(self):
        # Fixture has 4 root classes, all with refs >= 20
        result = self.bridge.get_high_impact_classes(min_refs=1)
        self.assertEqual(len(result), 4)

    def test_high_min_refs_returns_fewer(self):
        # Only BaseController has refs >= 40
        result = self.bridge.get_high_impact_classes(min_refs=40)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "BaseController")


class TestInternalClassesMultiDefn(unittest.TestCase):
    """Phase A #1: `index_source_tree` must enumerate ALL type definitions
    inside each source file, not just the one whose name equals the file
    stem. Regression for the bug where Buff.cs-style multi-class files
    leaked their siblings into the external-consumer bucket.
    """

    MULTI_DIR = FIXTURES_DIR / "multi_class_module"

    def setUp(self):
        self.bridge = RepoMapBridge(str(SAMPLE_L3))
        self.internal = self.bridge.index_source_tree(str(self.MULTI_DIR))

    def test_internal_set_includes_non_filename_classes(self):
        # Controllers.cs declares PaymentController, UserController, OrderController.
        # All three are L3 nodes (inherits from BaseController in the fixture).
        for name in ("PaymentController", "UserController", "OrderController"):
            self.assertIn(name, self.internal,
                          f"{name} should be in internal set (defined in Controllers.cs)")

    def test_internal_set_includes_base_types(self):
        # BaseController is the file's "stem"; old behaviour found it.
        # Verify the refactor still finds it.
        self.assertIn("BaseController", self.internal)

    def test_file_to_symbols_returns_all_definitions(self):
        # Look up by basename works because index_source_tree indexes both
        # paths and basenames.
        controllers = self.bridge.file_to_symbols("Controllers.cs")
        self.assertEqual(
            controllers,
            {"BaseController", "PaymentController", "UserController", "OrderController"},
        )

    def test_file_to_symbols_picks_up_sub_dir(self):
        # The walker must descend into nested directories.
        events = self.bridge.file_to_symbols("EventBus.cs")
        self.assertEqual(events, {"EventBus", "OrderService"})

    def test_non_source_files_ignored(self):
        # README.txt contains the word "class" but must NOT contribute.
        # _SOURCE_SUFFIXES gates this.
        readme_syms = self.bridge.file_to_symbols("README.txt")
        self.assertEqual(readme_syms, set())
        self.assertNotIn("ShouldNotBeFound", self.internal)

    def test_unknown_file_returns_empty_set(self):
        # Non-indexed file -- bridge returns empty set, never raises.
        self.assertEqual(self.bridge.file_to_symbols("nope.cs"), set())

    def test_get_module_external_consumers_uses_full_internal_set(self):
        """End-to-end: BaseController's three subclasses live in the same
        Controllers.cs file. Old code marked them external (file stem
        mismatch). New code recognises them as internal, so the L3 edges
        between BaseController and its subclasses do NOT show up as
        external consumers."""
        results = self.bridge.get_module_external_consumers(
            "Acme.Web", str(self.MULTI_DIR)
        )
        externals = [r for r in results if r["is_external"]]
        # All three subclasses are internal -> 0 external edges from
        # BaseController. The other L3 root classes (IRepository, EventBus,
        # BaseService) might still produce edges if their children are NOT
        # defined in this fixture. UserRepository / OrderRepository ARE
        # defined here; PaymentGateway / InvoiceGenerator / AuthMiddleware
        # / NotificationService are NOT.
        external_consumer_names = {r["consumer_name"] for r in externals}
        for inside_name in ("PaymentController", "UserController",
                            "OrderController", "UserRepository",
                            "OrderRepository", "OrderService"):
            self.assertNotIn(inside_name, external_consumer_names,
                             f"{inside_name} is in fixture; should not be external")


if __name__ == "__main__":
    unittest.main()

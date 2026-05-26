"""
Unit tests for KGInjector (scripts/kg_injector.py).

Phase E #10 split the one-line-per-file-list observations into one-line-per-path
to keep aim_search_nodes precision intact. The pre-v4.2 aggregated format is
gated behind `emit_legacy_keys=True` for callers with mixed-vintage KG entries.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(
    "kg_injector_real", str(SCRIPTS_DIR / "kg_injector.py")
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["kg_injector_real"] = _mod
_spec.loader.exec_module(_mod)
KGInjector = _mod.KGInjector


def _contract(involved, affected=None):
    return {
        "type": "blast_radius",
        "title": "T",
        "description": "d", "blind_spot": "bs", "violation_consequence": "vc",
        "confidence": 0.9,
        "involved_files": list(involved),
        "affected_external_files": list(affected or []),
    }


def _observations(out):
    return out["entities"][0]["observations"]


class TestKGInjectorObservationSplit(unittest.TestCase):
    """One observation per path -- the search-precision fix."""

    def test_each_involved_path_gets_own_observation(self):
        c = _contract(involved=["a.cs", "b.cs", "c.cs"])
        obs = _observations(KGInjector().convert([c], "Mod"))
        # Three [involved] lines, one per path, with no aggregated form
        involved_lines = [o for o in obs if o.startswith("[involved] ")]
        self.assertEqual(sorted(involved_lines),
                         ["[involved] a.cs", "[involved] b.cs", "[involved] c.cs"])
        # The legacy aggregated line is absent by default
        self.assertFalse(any(o.startswith("[involved_files] ") for o in obs))

    def test_each_affected_path_gets_own_observation(self):
        c = _contract(involved=["a.cs"], affected=["x.cs", "y.cs"])
        obs = _observations(KGInjector().convert([c], "Mod"))
        affected_lines = [o for o in obs if o.startswith("[affected_external] ")]
        self.assertEqual(sorted(affected_lines),
                         ["[affected_external] x.cs", "[affected_external] y.cs"])
        # External consumer count still emitted
        self.assertIn("[external_consumer_count] 2", obs)

    def test_no_aggregated_lines_when_legacy_disabled(self):
        c = _contract(involved=["a.cs", "b.cs"], affected=["x.cs"])
        obs = _observations(KGInjector().convert([c], "Mod"))
        # No comma-aggregated line under any of the old keys
        for o in obs:
            if o.startswith("[involved_files] ") or o.startswith("[affected_external_files] "):
                self.fail(f"unexpected legacy line emitted: {o!r}")

    def test_legacy_keys_optionally_emitted(self):
        c = _contract(involved=["a.cs", "b.cs"], affected=["x.cs"])
        obs = _observations(KGInjector(emit_legacy_keys=True).convert([c], "Mod"))
        # Legacy aggregated lines present AND new per-path lines also present
        self.assertIn("[involved_files] a.cs, b.cs", obs)
        self.assertIn("[affected_external_files] x.cs", obs)
        self.assertIn("[involved] a.cs", obs)
        self.assertIn("[involved] b.cs", obs)
        self.assertIn("[affected_external] x.cs", obs)

    def test_empty_involved_emits_nothing_for_that_kind(self):
        c = _contract(involved=[], affected=["x.cs"])
        obs = _observations(KGInjector().convert([c], "Mod"))
        self.assertFalse(any(o.startswith("[involved] ") for o in obs))
        self.assertIn("[affected_external] x.cs", obs)

    def test_relations_unaffected_by_observation_split(self):
        c = _contract(involved=["a.cs", "b.cs"], affected=["x.cs"])
        out = KGInjector().convert([c], "Mod")
        # 2 'constrains' + 1 'affects_external'
        relation_types = [r["relationType"] for r in out["relations"]]
        self.assertEqual(relation_types.count("constrains"), 2)
        self.assertEqual(relation_types.count("affects_external"), 1)


if __name__ == "__main__":
    unittest.main()

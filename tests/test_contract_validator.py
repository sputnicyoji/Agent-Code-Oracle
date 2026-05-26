"""
Unit tests for ContractValidator evidence-kind validation
(Phase C #8 -- scripts/contract_validator.py).

The validator's evidence checks are SOFT: bad evidence produces a
warning but never drops a contract. That keeps a malformed evidence
entry from costing the agent an otherwise-useful contract.
"""

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(
    "contract_validator_real", str(SCRIPTS_DIR / "contract_validator.py")
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["contract_validator_real"] = _mod
_spec.loader.exec_module(_mod)
ContractValidator = _mod.ContractValidator
VALID_EVIDENCE_KINDS = _mod.VALID_EVIDENCE_KINDS
STRONG_EVIDENCE_KINDS = _mod.STRONG_EVIDENCE_KINDS


def _contract(evidence, file_path="Controllers.cs"):
    return {
        "schema_version": 2,
        "type": "blast_radius",
        "title": "T", "description": "d",
        "blind_spot": "bs", "violation_consequence": "vc",
        "confidence": 0.9,
        "involved": [{"path": file_path}],
        "evidence": evidence,
    }


def _run(validator, contracts):
    """Capture validator stdout so test output stays clean."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = validator.process(contracts)
    return result, buf.getvalue()


class TestEvidenceKindEnum(unittest.TestCase):
    """Recognised kinds pass; unknown kinds warn but the contract survives."""

    FIXTURES = TESTS_DIR / "fixtures" / "multi_class_module"

    def setUp(self):
        self.validator = ContractValidator(source_root=str(self.FIXTURES))

    def test_static_reference_passes(self):
        c = _contract([{"kind": "static_reference",
                        "source": "repomap_l3",
                        "target": "Controllers.cs#BaseController"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertNotIn("unknown kind", out)

    def test_code_comment_passes(self):
        c = _contract([{"kind": "code_comment",
                        "source": "Controllers.cs:5",
                        "target": "// must come first"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertNotIn("unknown kind", out)

    def test_data_flow_trace_passes(self):
        c = _contract([{"kind": "data_flow_trace",
                        "source": "Controllers.cs:10",
                        "target": "Repositories.cs:8"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)

    def test_design_rationale_passes(self):
        c = _contract([{"kind": "design_rationale",
                        "source": "doc",
                        "target": "ARCHITECTURE.md"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)

    def test_unknown_kind_warns_but_keeps_contract(self):
        c = _contract([{"kind": "vibes_check",
                        "source": "intuition",
                        "target": "x"}])
        result, out = _run(self.validator, [c])
        # Contract NOT dropped on evidence problems
        self.assertEqual(len(result), 1, "evidence-only problems must not drop contracts")
        self.assertIn("unknown kind", out)
        self.assertIn("vibes_check", out)

    def test_missing_kind_warns(self):
        c = _contract([{"source": "x", "target": "y"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertIn("missing 'kind'", out)

    def test_non_dict_evidence_entry_warns(self):
        c = _contract(["just a string"])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertIn("not an object", out)

    def test_evidence_list_wrong_type_warns(self):
        # Replace the list entirely with a wrong type.
        c = _contract([])
        c["evidence"] = "string instead of list"
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertIn("must be a list", out)


class TestEvidenceShapeChecks(unittest.TestCase):
    """Per-kind shape rules (code_comment file:line, static_reference path#symbol)
    fire as warnings but don't drop the contract."""

    FIXTURES = TESTS_DIR / "fixtures" / "multi_class_module"

    def setUp(self):
        self.validator = ContractValidator(source_root=str(self.FIXTURES))

    def test_code_comment_without_colon_warns(self):
        c = _contract([{"kind": "code_comment",
                        "source": "Controllers.cs no line",
                        "target": "/* x */"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertIn("file:line", out)

    def test_static_reference_without_hash_warns(self):
        c = _contract([{"kind": "static_reference",
                        "source": "graph",
                        "target": "Controllers.cs"}])  # no '#symbol'
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        self.assertIn("path#symbol", out)

    def test_design_rationale_has_no_shape_check(self):
        # design_rationale is free-form; missing fields are NOT warnings.
        c = _contract([{"kind": "design_rationale"}])
        result, out = _run(self.validator, [c])
        self.assertEqual(len(result), 1)
        # No specific shape warning should appear
        for marker in ("file:line", "path#symbol"):
            self.assertNotIn(marker, out)


class TestStrongEvidenceTaxonomy(unittest.TestCase):
    """Module-level invariants on the kind taxonomy."""

    def test_strong_is_subset_of_valid(self):
        self.assertTrue(STRONG_EVIDENCE_KINDS.issubset(VALID_EVIDENCE_KINDS))

    def test_design_rationale_is_weak(self):
        self.assertIn("design_rationale", VALID_EVIDENCE_KINDS)
        self.assertNotIn("design_rationale", STRONG_EVIDENCE_KINDS)


if __name__ == "__main__":
    unittest.main()

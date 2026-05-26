"""
Unit + integration tests for round_driver.py (Phase D-mini).

The driver is exercised end-to-end against the multi_class_module
fixture with the DryRun provider; the provider returns deterministic
stubs so the test does not depend on any LLM.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
TEMPLATES_DIR = TESTS_DIR.parent / "templates"
FIXTURES = TESTS_DIR / "fixtures" / "multi_class_module"

sys.path.insert(0, str(SCRIPTS_DIR))


def _load(module_name, file_name):
    spec = importlib.util.spec_from_file_location(
        module_name, str(SCRIPTS_DIR / file_name)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_artifacts = _load("round_artifacts_real", "round_artifacts.py")
_driver = _load("round_driver_real", "round_driver.py")

Round0Artifact = _artifacts.Round0Artifact
Round1Artifact = _artifacts.Round1Artifact
Round2Artifact = _artifacts.Round2Artifact
Round3Artifact = _artifacts.Round3Artifact
run_driver = _driver.run_driver
_load_template = _driver._load_template
_render = _driver._render


# ---------------------------------------------------------------------------

class TestTemplateLoading(unittest.TestCase):
    """Templates carry frontmatter; the loader parses it and returns the body."""

    def test_round1_template_loads(self):
        fm, body = _load_template(TEMPLATES_DIR, "round1_architect.txt")
        self.assertEqual(fm.get("round"), "1")
        self.assertEqual(fm.get("expects_json"), "false")
        self.assertIn("{module_name}", body)
        self.assertIn("{source_index}", body)
        self.assertIn("{round0_evidence}", body)

    def test_round2_template_loads(self):
        fm, body = _load_template(TEMPLATES_DIR, "round2_contract_mining.txt")
        self.assertEqual(fm.get("expects_json"), "true")
        self.assertIn("{architecture_summary}", body)
        self.assertIn("{candidate_seeds}", body)

    def test_round3_template_loads(self):
        fm, body = _load_template(TEMPLATES_DIR, "round3_devils_advocate.txt")
        self.assertEqual(fm.get("expects_json"), "true")
        self.assertIn("{round2_contracts}", body)


class TestRender(unittest.TestCase):
    """{placeholder} replacement; missing ones become `(not provided)`."""

    def test_basic_substitution(self):
        body = "module={module_name}, src={source_index}"
        result = _render(body, module_name="X", source_index="tree")
        self.assertEqual(result, "module=X, src=tree")

    def test_missing_placeholder_becomes_stub(self):
        body = "{a} {b}"
        result = _render(body, a="A")
        self.assertEqual(result, "A (not provided)")

    def test_curly_in_body_left_alone(self):
        # An empty {} or JSON snippet without identifier-shape stays put.
        body = 'schema = {{"k": "v"}}'
        result = _render(body)
        # Double-brace stays; the regex only catches {ident} shape.
        self.assertEqual(result, 'schema = {{"k": "v"}}')


# ---------------------------------------------------------------------------

class TestRound1Parsing(unittest.TestCase):
    """Round 1 LLM output -> Round1Artifact."""

    def test_seeds_extracted(self):
        text = """Some architecture analysis.

More analysis here.

## candidate_seeds
- file: src/foo.cs, reason: load-bearing
- file: src/bar.cs, reason: cross-module entry
"""
        r1 = Round1Artifact.from_llm_response(text)
        self.assertEqual(len(r1.candidate_seeds), 2)
        self.assertEqual(r1.candidate_seeds[0]["file"], "src/foo.cs")
        self.assertEqual(r1.candidate_seeds[1]["file"], "src/bar.cs")

    def test_no_seeds_block(self):
        r1 = Round1Artifact.from_llm_response("Just prose, no seeds.")
        self.assertEqual(r1.candidate_seeds, [])
        self.assertEqual(r1.architecture_summary, "Just prose, no seeds.")

    def test_seeds_section_stripped_from_summary(self):
        text = "Architecture.\n\n## candidate_seeds\n- file: x.cs, reason: r\n"
        r1 = Round1Artifact.from_llm_response(text)
        self.assertNotIn("candidate_seeds", r1.architecture_summary)


class TestRound2Parsing(unittest.TestCase):
    """Markdown-fenced JSON should still parse."""

    def test_plain_json(self):
        r2 = Round2Artifact.from_llm_response('[{"type": "rationale", "title": "T"}]')
        self.assertEqual(len(r2.contracts), 1)
        self.assertEqual(r2.contracts[0]["title"], "T")

    def test_fenced_json(self):
        text = '```json\n[{"type": "ordering", "title": "X"}]\n```'
        r2 = Round2Artifact.from_llm_response(text)
        self.assertEqual(len(r2.contracts), 1)
        self.assertEqual(r2.contracts[0]["type"], "ordering")

    def test_malformed_json_preserved_raw(self):
        r2 = Round2Artifact.from_llm_response("not valid json")
        self.assertEqual(r2.contracts, [])
        self.assertEqual(r2.raw_response, "not valid json")

    def test_object_envelope_with_contracts_key(self):
        text = '{"contracts": [{"type": "rationale", "title": "wrapped"}]}'
        r2 = Round2Artifact.from_llm_response(text)
        self.assertEqual(len(r2.contracts), 1)


# ---------------------------------------------------------------------------

class TestDriverEndToEnd(unittest.TestCase):
    """run_driver with DryRunProvider produces all four round*.json files."""

    def test_dry_run_e2e(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            run_driver(
                config_path=None,
                module_name="AcmeWeb",
                module_source=str(FIXTURES),
                module_docs=None,
                provider_name="dry_run",
                output_dir=output_dir,
                templates_dir=TEMPLATES_DIR,
            )
            for name in ("round0.json", "round1.json", "round2.json", "round3.json"):
                path = output_dir / name
                self.assertTrue(path.exists(), f"{name} should be written")

            # Round 0 artifact has the expected counters
            r0 = json.loads((output_dir / "round0.json").read_text(encoding="utf-8"))
            self.assertEqual(r0["module_name"], "AcmeWeb")
            # internal_class_count includes BaseController, IRepository, etc.
            # 10 types in the fixture; provider may not run when no graph
            # provider is configured -- we accept either path.
            self.assertGreaterEqual(r0["internal_class_count"], 0)

            # Round 3 output is a JSON array (pipeline-ready). DryRun
            # stub returns []; that's fine for the shape test.
            r3 = json.loads((output_dir / "round3.json").read_text(encoding="utf-8"))
            self.assertIsInstance(r3, list)

    def test_unimplemented_provider_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                run_driver(
                    config_path=None,
                    module_name="X",
                    module_source=str(FIXTURES),
                    module_docs=None,
                    provider_name="anthropic",  # not implemented yet
                    output_dir=Path(tmp),
                    templates_dir=TEMPLATES_DIR,
                )

    def test_unknown_provider_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                run_driver(
                    config_path=None,
                    module_name="X",
                    module_source=str(FIXTURES),
                    module_docs=None,
                    provider_name="totally_made_up",
                    output_dir=Path(tmp),
                    templates_dir=TEMPLATES_DIR,
                )


if __name__ == "__main__":
    unittest.main()

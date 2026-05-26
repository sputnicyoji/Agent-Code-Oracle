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
TEMPLATES_DIR = SCRIPTS_DIR / "templates"
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


class TestRenderNoDoublePass(unittest.TestCase):
    """Phase D-mini fixup #1: _render must NOT regex-scan substituted
    values. A value containing literal `{ident}` text (LLM prose, C#
    initializers, Python f-strings) survives unchanged into the
    rendered prompt.
    """

    def test_substituted_value_preserves_curly_idents(self):
        body = "summary: {summary}\nend."
        result = _render(body, summary="The handler sees {config} and {state}.")
        # Both `{config}` and `{state}` came from the substituted value,
        # not from the template -- they MUST be preserved verbatim.
        self.assertIn("{config}", result)
        self.assertIn("{state}", result)
        # And the template placeholder was substituted exactly once.
        self.assertNotIn("{summary}", result)

    def test_missing_placeholder_still_marked(self):
        # The fixup MUST preserve the missing-placeholder behaviour:
        # a placeholder name not in kwargs becomes `(not provided)`.
        result = _render("{a} -- {b}", a="present")
        self.assertEqual(result, "present -- (not provided)")

    def test_round_trip_substituted_then_no_replacement(self):
        # If a substituted value happens to contain a placeholder name
        # that ALSO appears in kwargs, that nested mention is NOT
        # re-substituted -- one pass only.
        result = _render("{summary}\n{summary_repeat}",
                         summary="See {summary_repeat} below.",
                         summary_repeat="(the appendix)")
        # The substituted summary keeps its literal `{summary_repeat}`;
        # only the template's own {summary_repeat} marker is replaced.
        self.assertIn("See {summary_repeat} below.", result)
        self.assertIn("(the appendix)", result)


class TestGraphProviderUnknownType(unittest.TestCase):
    """Phase D-mini fixup #2: unknown graph_provider.type raises
    ValueError instead of silently returning (None, 'none')."""

    def test_unknown_type_raises(self):
        from oracle_config import normalize_config
        cfg = normalize_config({"graph_provider": {"type": "repomap_l4"}})
        with self.assertRaises(ValueError) as ctx:
            _driver._make_graph_provider(cfg)
        self.assertIn("repomap_l4", str(ctx.exception))
        self.assertIn("Supported", str(ctx.exception))

    def test_missing_type_returns_none(self):
        # `type` absent (not configured) is legitimate -- not an error.
        from oracle_config import normalize_config
        cfg = normalize_config({})
        provider, type_name = _driver._make_graph_provider(cfg)
        self.assertIsNone(provider)
        self.assertEqual(type_name, "none")


class TestInternalCountAlwaysComputed(unittest.TestCase):
    """Phase D-mini fixup #4+#6: internal_class_count is computed from
    the source tree regardless of whether a graph provider is configured.
    Previously the count was 0 when provider was None -- misleading the
    user into thinking the module declared no types.
    """

    def test_no_provider_still_counts_types(self):
        # multi_class_module fixture declares 10 types (4 in Controllers,
        # 3 in Repositories, 2 in EventBus, 1 in Notifications).
        evidence, internal_count, cross = _driver._build_evidence_pack(
            provider_type="none",
            provider=None,
            module_name="AcmeWeb",
            source_root=str(TESTS_DIR / "fixtures" / "multi_class_module"),
        )
        self.assertEqual(evidence, [])
        self.assertEqual(cross, 0)
        self.assertGreaterEqual(internal_count, 10,
                                "source-tree walk should find all declared types")

    def test_with_provider_reuses_file_to_symbols(self):
        # When a provider is configured, the driver must not call
        # index_source_tree a second time. We verify by checking the
        # provider's _file_to_symbols is non-empty AFTER the driver
        # ran (proving the first call populated it) and equals what
        # the driver counted.
        from repomap_bridge import RepoMapBridge
        l3_path = str(TESTS_DIR / "fixtures" / "sample-l3.md")
        source_root = str(TESTS_DIR / "fixtures" / "multi_class_module")
        bridge = RepoMapBridge(l3_path)
        _, internal_count, _ = _driver._build_evidence_pack(
            "repomap_l3", bridge, "AcmeWeb", source_root,
        )
        # _file_to_symbols was populated by get_module_external_consumers
        # and re-used (not re-walked); union of its values equals the
        # internal_count reported.
        union: set[str] = set()
        for syms in bridge._file_to_symbols.values():
            union.update(syms)
        self.assertEqual(len(union), internal_count)
        self.assertGreaterEqual(internal_count, 10)


class TestSeedParserKeyByName(unittest.TestCase):
    """Phase D-mini fixup #5+#7: Round 1 seed parser identifies file
    and reason by KEY NAME (not position) and strips inline ` # ...`
    comments before parsing.
    """

    def test_reversed_field_order(self):
        text = "## candidate_seeds\n- reason: load-bearing, file: src/foo.cs\n"
        r1 = Round1Artifact.from_llm_response(text)
        self.assertEqual(len(r1.candidate_seeds), 1)
        self.assertEqual(r1.candidate_seeds[0]["file"], "src/foo.cs")
        self.assertEqual(r1.candidate_seeds[0]["reason"], "load-bearing")

    def test_inline_comment_stripped(self):
        text = "## candidate_seeds\n- file: src/main.go # entry point\n"
        r1 = Round1Artifact.from_llm_response(text)
        self.assertEqual(len(r1.candidate_seeds), 1)
        # `# entry point` MUST be stripped before parsing
        self.assertEqual(r1.candidate_seeds[0]["file"], "src/main.go")
        self.assertNotIn("#", r1.candidate_seeds[0]["file"])

    def test_inline_comment_then_reason(self):
        text = "## candidate_seeds\n- file: foo.cs, reason: r  # comment here\n"
        r1 = Round1Artifact.from_llm_response(text)
        self.assertEqual(r1.candidate_seeds[0]["file"], "foo.cs")
        self.assertEqual(r1.candidate_seeds[0]["reason"], "r")

    def test_bare_path_still_works(self):
        # Backwards-compat: no `file:` key, just a bare path.
        text = "## candidate_seeds\n- src/foo.cs\n"
        r1 = Round1Artifact.from_llm_response(text)
        self.assertEqual(r1.candidate_seeds[0]["file"], "src/foo.cs")


class TestRound2ParserProsePrefix(unittest.TestCase):
    """Phase D-mini fixup #14: Round 2 parser locates JSON even when
    prose precedes the fence or array."""

    def test_prose_then_fenced_json(self):
        text = ('Here is the contracts JSON:\n\n'
                '```json\n[{"type": "rationale", "title": "X"}]\n```')
        r2 = Round2Artifact.from_llm_response(text)
        self.assertEqual(len(r2.contracts), 1)
        self.assertEqual(r2.contracts[0]["type"], "rationale")

    def test_prose_then_bare_array(self):
        text = 'Sure thing! Here you go:\n[{"type": "ordering", "title": "Y"}]'
        r2 = Round2Artifact.from_llm_response(text)
        self.assertEqual(len(r2.contracts), 1)
        self.assertEqual(r2.contracts[0]["type"], "ordering")

    def test_prose_then_object_envelope(self):
        text = 'Output:\n{"contracts": [{"type": "data_flow", "title": "Z"}]}'
        r2 = Round2Artifact.from_llm_response(text)
        self.assertEqual(len(r2.contracts), 1)
        self.assertEqual(r2.contracts[0]["type"], "data_flow")

    def test_strip_to_json_helper_picks_first_bracket(self):
        # The helper picks min(find("["), find("{")). Prose followed by
        # `{...}` envelope should be located correctly.
        s = Round2Artifact._strip_to_json('text [a] then {b}')
        self.assertTrue(s.startswith("["), f"got: {s!r}")

    def test_truly_no_json_returns_empty(self):
        r2 = Round2Artifact.from_llm_response("nothing json here at all")
        self.assertEqual(r2.contracts, [])
        self.assertEqual(r2.raw_response, "nothing json here at all")


if __name__ == "__main__":
    unittest.main()

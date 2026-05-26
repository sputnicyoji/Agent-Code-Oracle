"""
Unit tests for oracle_config (scripts/oracle_config.py).

Phase B #11 added config-directory-aware path resolution. Phase B #6 added
profile overrides under quality_gate_profiles.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from oracle_config import (  # noqa: E402
    DEFAULT_QUALITY_GATE_PROFILES,
    load_json_config,
    normalize_config,
    resolve_config_path,
)


class TestConfigRelativePaths(unittest.TestCase):
    """Phase B #11: paths in oracle.config.json resolve relative to the
    config file's directory, not the caller's cwd. The original behaviour
    broke `python <oracle>/scripts/pipeline.py --config <project>/oracle.config.json`
    when run from a third directory because every relative path was looked
    up against that third directory."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.cfg_dir = Path(self.tmpdir.name) / "project"
        (self.cfg_dir / "graph").mkdir(parents=True)
        (self.cfg_dir / "graph" / "L3.md").write_text("# stub L3\n")
        self.cfg_path = self.cfg_dir / "oracle.config.json"
        self.cfg_path.write_text(json.dumps({
            "project_name": "Test",
            "graph_provider": {"type": "repomap_l3", "path": "graph/L3.md"},
            "scanned_modules": {
                "M": {"source_root": "src/M", "contract_output": "out/M.json"}
            },
        }))

    def test_load_attaches_config_dir(self):
        cfg = load_json_config(self.cfg_path)
        self.assertIn("_config_dir", cfg)
        # _config_dir is the absolute, resolved directory of the file
        self.assertEqual(Path(cfg["_config_dir"]).resolve(), self.cfg_dir.resolve())

    def test_resolve_config_path_uses_config_dir(self):
        cfg = load_json_config(self.cfg_path)
        resolved = resolve_config_path(cfg, ["graph_provider", "path"])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, (self.cfg_dir / "graph" / "L3.md").resolve())

    def test_absolute_path_returned_as_is(self):
        cfg = load_json_config(self.cfg_path)
        cfg["graph_provider"]["path"] = str((self.cfg_dir / "graph" / "L3.md").resolve())
        resolved = resolve_config_path(cfg, ["graph_provider", "path"])
        self.assertEqual(resolved, (self.cfg_dir / "graph" / "L3.md").resolve())

    def test_missing_key_returns_default(self):
        cfg = load_json_config(self.cfg_path)
        self.assertIsNone(resolve_config_path(cfg, ["nonexistent", "path"]))
        sentinel = Path("/sentinel")
        self.assertEqual(
            resolve_config_path(cfg, ["nope"], default=sentinel),
            sentinel,
        )

    def test_empty_string_returns_default(self):
        cfg = load_json_config(self.cfg_path)
        cfg["graph_provider"]["path"] = ""
        self.assertIsNone(resolve_config_path(cfg, ["graph_provider", "path"]))

    def test_non_string_returns_default(self):
        cfg = load_json_config(self.cfg_path)
        cfg["graph_provider"]["path"] = 42  # type mismatch
        self.assertIsNone(resolve_config_path(cfg, ["graph_provider", "path"]))

    def test_cwd_fallback_when_no_config_dir(self):
        # Direct dict without going through load_json_config (no _config_dir)
        cfg = {"graph_provider": {"path": "relative/under/cwd.md"}}
        resolved = resolve_config_path(cfg, ["graph_provider", "path"])
        # Resolves against cwd, not against any oracle directory.
        self.assertEqual(resolved, (Path.cwd() / "relative/under/cwd.md").resolve())


class TestQualityGateProfiles(unittest.TestCase):
    """Phase B #6: normalize_config adds quality_gate_profiles with
    default/leaf/hub keys. User-supplied profiles merge with built-ins."""

    def test_built_in_profiles_present(self):
        cfg = normalize_config({})
        profiles = cfg.get("quality_gate_profiles")
        self.assertIsNotNone(profiles)
        for name in ("default", "leaf", "hub"):
            self.assertIn(name, profiles)

    def test_built_in_thresholds(self):
        cfg = normalize_config({})
        profiles = cfg["quality_gate_profiles"]
        # Document the actual numeric defaults so tightening them later
        # is visible as a test change.
        self.assertEqual(profiles["default"]["min_high_value_ratio"], 0.5)
        self.assertEqual(profiles["leaf"]["min_high_value_ratio"], 0.25)
        self.assertEqual(profiles["hub"]["min_high_value_ratio"], 0.6)

    def test_user_profile_extends_built_in(self):
        cfg = normalize_config({
            "quality_gate_profiles": {
                "leaf": {"min_high_value_ratio": 0.3, "min_effective": 3},
                "strict_hub": {"min_high_value_ratio": 0.75},
            }
        })
        profiles = cfg["quality_gate_profiles"]
        # leaf override merges -- new key honored, but other built-ins for
        # this profile are NOT obliterated since leaf only has one default.
        self.assertEqual(profiles["leaf"]["min_high_value_ratio"], 0.3)
        self.assertEqual(profiles["leaf"]["min_effective"], 3)
        # Custom profile added alongside built-ins
        self.assertEqual(profiles["strict_hub"]["min_high_value_ratio"], 0.75)
        # Built-ins still intact
        self.assertEqual(profiles["default"]["min_high_value_ratio"], 0.5)

    def test_invalid_profile_override_ignored(self):
        # Non-dict override must not crash; just skip it.
        cfg = normalize_config({
            "quality_gate_profiles": {
                "leaf": "not a dict",
            }
        })
        self.assertEqual(
            cfg["quality_gate_profiles"]["leaf"]["min_high_value_ratio"],
            DEFAULT_QUALITY_GATE_PROFILES["leaf"]["min_high_value_ratio"],
        )


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for GrepProvider (scripts/providers/grep_provider.py).

Phase E #12: zero-setup graph provider that does not need a RepoMap L3 index.
Tests use the multi_class_module fixture (a git repo is not required because
the test patches subprocess; this isolates the provider logic from environment).
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "providers"))

_spec = importlib.util.spec_from_file_location(
    "grep_provider_real",
    str(SCRIPTS_DIR / "providers" / "grep_provider.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["grep_provider_real"] = _mod
_spec.loader.exec_module(_mod)
GrepProvider = _mod.GrepProvider


def _run_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestGrepProviderIndex(unittest.TestCase):
    """Source-tree walking + symbol enumeration are deterministic and do
    not depend on subprocess. Exercise them against the existing fixture."""

    FIXTURES = Path(__file__).parent / "fixtures" / "multi_class_module"

    def setUp(self):
        # We construct via a tmpdir-rooted repo so _detect_grep finds a
        # consistent backend across machines. The patch in real tests
        # replaces subprocess anyway.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo_root = Path(self.tmp.name)
        with patch.object(GrepProvider, "_detect_grep", return_value="grep"):
            self.provider = GrepProvider(repo_root=self.repo_root)

    def test_index_source_tree_finds_multi_class_files(self):
        internal = self.provider.index_source_tree(str(self.FIXTURES))
        for name in ("BaseController", "PaymentController", "UserController",
                     "OrderController", "IRepository", "EventBus",
                     "OrderService", "NotificationService"):
            self.assertIn(name, internal)

    def test_file_to_symbols_works_for_basename(self):
        self.provider.index_source_tree(str(self.FIXTURES))
        syms = self.provider.file_to_symbols("Controllers.cs")
        self.assertEqual(
            syms,
            {"BaseController", "PaymentController",
             "UserController", "OrderController"},
        )

    def test_index_empty_source_root_returns_empty(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        result = self.provider.index_source_tree(empty.name)
        self.assertEqual(result, set())


class TestGrepProviderExternalConsumers(unittest.TestCase):
    """Hit classification: paths inside source_root are internal, paths
    outside are external. Subprocess is patched so the test does not
    depend on which grep flavour is installed."""

    FIXTURES = Path(__file__).parent / "fixtures" / "multi_class_module"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo_root = Path(self.tmp.name)
        with patch.object(GrepProvider, "_detect_grep", return_value="grep"):
            self.provider = GrepProvider(repo_root=self.repo_root)

    @patch("grep_provider_real.subprocess.run")
    def test_consumer_outside_source_root_is_external(self, mock_run):
        # Grep returns a hit OUTSIDE the multi_class_module fixture.
        external_file = str(self.repo_root / "external" / "Caller.cs")
        Path(external_file).parent.mkdir(parents=True, exist_ok=True)
        Path(external_file).write_text("class Caller { BaseController b; }")
        mock_run.return_value = _run_result(stdout=external_file + "\n")
        results = self.provider.get_module_external_consumers(
            "Mod", str(self.FIXTURES)
        )
        externals = [r for r in results if r["is_external"]]
        # Every internal symbol's grep returned the same external hit;
        # at least one external entry must be present.
        self.assertGreater(len(externals), 0)
        self.assertEqual(externals[0]["relation_type"], "reference")

    @patch("grep_provider_real.subprocess.run")
    def test_self_reference_is_skipped(self, mock_run):
        # Each grep invocation returns the file that DEFINES the symbol it
        # was searching for. Provider must filter all of these out because
        # `class Foo { ... }` always matches a grep for "Foo".
        controllers = str(self.FIXTURES / "Controllers.cs")
        repos = str(self.FIXTURES / "Repositories.cs")
        eventbus = str(self.FIXTURES / "sub" / "EventBus.cs")
        notif = str(self.FIXTURES / "sub" / "Notifications.cs")
        defining_for = {
            # Controllers.cs declares 4 types
            "BaseController": controllers, "PaymentController": controllers,
            "UserController": controllers, "OrderController": controllers,
            # Repositories.cs declares 3
            "IRepository": repos, "UserRepository": repos, "OrderRepository": repos,
            # sub/EventBus.cs declares 2
            "EventBus": eventbus, "OrderService": eventbus,
            # sub/Notifications.cs declares 1
            "NotificationService": notif,
        }

        def side(cmd, *_a, **_kw):
            # Last argument before include_dirs is the symbol being grep'd.
            symbol = next((a for a in cmd if a in defining_for), None)
            if symbol is None:
                return _run_result(returncode=1)
            return _run_result(stdout=defining_for[symbol] + "\n")

        mock_run.side_effect = side
        results = self.provider.get_module_external_consumers(
            "Mod", str(self.FIXTURES)
        )
        # Every "hit" was the defining file -- nothing survives the self-
        # reference filter.
        self.assertEqual(results, [],
                         f"expected all self-references filtered; got: {results}")

    @patch("grep_provider_real.subprocess.run")
    def test_no_hits_no_externals(self, mock_run):
        # Grep returns exit 1 (no matches) -- not an error.
        mock_run.return_value = _run_result(returncode=1, stdout="")
        results = self.provider.get_module_external_consumers(
            "Mod", str(self.FIXTURES)
        )
        self.assertEqual(results, [])

    @patch("grep_provider_real.subprocess.run")
    def test_subprocess_timeout_does_not_crash(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=30)
        results = self.provider.get_module_external_consumers(
            "Mod", str(self.FIXTURES)
        )
        # Provider returns gracefully on timeout
        self.assertEqual(results, [])


class TestGrepProviderDetection(unittest.TestCase):
    """_detect_grep prefers git grep > rg > grep, raises only when none."""

    def test_no_grep_available_raises(self):
        # Build a provider where shutil.which always returns None
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch("grep_provider_real.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                GrepProvider(repo_root=tmp.name)


if __name__ == "__main__":
    unittest.main()

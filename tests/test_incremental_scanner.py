"""
Unit tests for IncrementalScanner.get_changed_files (scripts/incremental_scanner.py).

Phase A #3: the method previously returned `[]` whenever git failed,
indistinguishable from a clean diff. After the fix it returns `None` on
real failures (ORIG_HEAD missing AND HEAD~N fallback failing, or git
not in PATH, or timeout) so the caller can refuse to treat "git broken"
as "nothing to do".
"""

import sys
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(
    "incremental_scanner_real", str(SCRIPTS_DIR / "incremental_scanner.py")
)
if _spec and _spec.loader:
    _is_mod = importlib.util.module_from_spec(_spec)
    # Register before exec so @patch() can locate it; without this the
    # decorator's import of "incremental_scanner_real" fails because
    # importlib.util.module_from_spec does NOT populate sys.modules.
    sys.modules["incremental_scanner_real"] = _is_mod
    _spec.loader.exec_module(_is_mod)
    IncrementalScanner = _is_mod.IncrementalScanner
else:
    raise ImportError("Cannot load incremental_scanner.py from scripts/")


def _run_result(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestGetChangedFilesNone(unittest.TestCase):
    """Real git failures must produce None, not []."""

    def setUp(self):
        # Construct scanner without an L3 or contracts file -- the method
        # under test does not need them.
        self.scanner = IncrementalScanner(l3_path=None, contracts_path=None)

    @patch("incremental_scanner_real.subprocess.run")
    def test_returns_none_when_not_a_git_repo(self, mock_run):
        # `git rev-parse --show-toplevel` fails -> not a repo -> None
        mock_run.return_value = _run_result(128, stderr="fatal: not a git repository")
        result = self.scanner.get_changed_files()
        self.assertIsNone(result, "non-repo must return None, not []")

    @patch("incremental_scanner_real.subprocess.run")
    def test_returns_none_when_both_diff_forms_fail(self, mock_run):
        # rev-parse OK, ORIG_HEAD diff fails, HEAD~N diff fails -> None
        mock_run.side_effect = [
            _run_result(0, stdout="/repo\n"),
            _run_result(128, stderr="fatal: bad revision 'ORIG_HEAD'"),
            _run_result(128, stderr="fatal: bad revision 'HEAD~1'"),
        ]
        result = self.scanner.get_changed_files()
        self.assertIsNone(result, "both-form failure must return None")

    @patch("incremental_scanner_real.subprocess.run")
    def test_returns_empty_list_when_diff_is_empty(self, mock_run):
        # rev-parse OK, ORIG_HEAD diff succeeds with empty stdout -> []
        mock_run.side_effect = [
            _run_result(0, stdout="/repo\n"),
            _run_result(0, stdout=""),
        ]
        result = self.scanner.get_changed_files()
        self.assertEqual(result, [], "clean diff is [], not None")

    @patch("incremental_scanner_real.subprocess.run")
    def test_falls_back_to_head_when_orig_head_missing(self, mock_run):
        # ORIG_HEAD missing (first-merge case), HEAD~N succeeds -> list[str]
        mock_run.side_effect = [
            _run_result(0, stdout="/repo\n"),
            _run_result(128, stderr="fatal: bad revision 'ORIG_HEAD'"),
            _run_result(0, stdout="scripts/foo.py\nscripts/bar.py\n"),
        ]
        scanner = IncrementalScanner(
            l3_path=None, contracts_path=None,
            include=["**/*.py"],
        )
        result = scanner.get_changed_files()
        self.assertEqual(result, ["scripts/foo.py", "scripts/bar.py"])

    @patch("incremental_scanner_real.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        # rev-parse OK then ORIG_HEAD diff times out -> None
        mock_run.side_effect = [
            _run_result(0, stdout="/repo\n"),
            subprocess.TimeoutExpired(cmd=[], timeout=30),
        ]
        result = self.scanner.get_changed_files()
        self.assertIsNone(result, "timeout is a real failure, return None")

    @patch("incremental_scanner_real.subprocess.run")
    def test_returns_none_when_git_not_in_path(self, mock_run):
        mock_run.side_effect = FileNotFoundError("git not found")
        result = self.scanner.get_changed_files()
        self.assertIsNone(result, "missing git binary -> None")

    @patch("incremental_scanner_real.subprocess.run")
    def test_invalid_commits_floor_to_one(self, mock_run):
        # commits <= 0 must be coerced to 1, not produce HEAD~-1 etc.
        mock_run.side_effect = [
            _run_result(0, stdout="/repo\n"),
            _run_result(0, stdout=""),
        ]
        # Call with commits=0; should not raise.
        result = self.scanner.get_changed_files(commits=0)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()

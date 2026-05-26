"""Grep-based graph provider.

Zero-setup alternative to RepoMap L3. Walks the source tree to enumerate
type-level symbols, then asks `git grep` / `rg` / plain `grep` to find
files that reference those symbols. Files inside `source_root` are
internal; files outside are external consumers.

Tradeoffs vs the RepoMap provider:

- Pro: no index file to maintain; works on any git repo immediately.
- Pro: language-neutral (whatever the regex finds is a candidate).
- Con: cannot tell "inherits" from "implements" from "calls"; every edge
  has `relation_type = "reference"`.
- Con: slower; grep cost scales with #symbols * #files. The provider
  prints a warning when internal_symbols > 500.

The provider exposes the same surface as RepoMapBridge so pipeline
Stage 0 can swap implementations without conditional shape handling:

    bridge.get_module_external_consumers(module_name, source_root) -> list[dict]
    bridge.file_to_symbols(file_ref) -> set[str]
    bridge.index_source_tree(source_root) -> set[str]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Reuse the same symbol extractor as repomap_bridge so the two providers
# agree on what counts as a "type-level symbol".
from repomap_bridge import _extract_top_level_symbols


class GrepProvider:
    """Discover external consumers via on-demand grep.

    Args:
        repo_root: Absolute repo root. Used to scope the search and to
                   classify hit files as internal vs external.
        include_dirs: Optional list of directories under repo_root to
                      restrict the search to. Defaults to repo_root.
        warn_threshold: Print a one-line stderr warning when the module
                        declares more symbols than this; symbol count is
                        the dominant cost factor.
    """

    def __init__(
        self,
        repo_root: str | Path,
        include_dirs: list[str] | None = None,
        warn_threshold: int = 500,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.include_dirs = [
            str((self.repo_root / d).resolve()) for d in (include_dirs or ["."])
        ]
        self.warn_threshold = warn_threshold
        self._file_to_symbols: dict[str, set[str]] = {}
        self._grep_cmd = self._detect_grep()

    # ------------------------------------------------------------------
    # Provider surface (parallel to RepoMapBridge)
    # ------------------------------------------------------------------

    def index_source_tree(self, source_root: str) -> set[str]:
        """Walk source_root, populate _file_to_symbols, return the union
        of every type-level symbol defined under source_root.
        """
        self._file_to_symbols.clear()
        internal: set[str] = set()
        if not source_root or not os.path.isdir(source_root):
            return internal
        root = Path(source_root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            syms = _extract_top_level_symbols(path)
            if not syms:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            self._file_to_symbols[rel] = syms
            self._file_to_symbols[path.name] = (
                self._file_to_symbols.get(path.name, set()) | syms
            )
            internal.update(syms)
        return internal

    def file_to_symbols(self, file_ref: str) -> set[str]:
        norm = file_ref.replace("\\", "/")
        if norm in self._file_to_symbols:
            return self._file_to_symbols[norm]
        return self._file_to_symbols.get(Path(norm).name, set())

    def get_module_external_consumers(
        self, module_name: str, source_root: str
    ) -> list[dict]:
        internal_classes = self.index_source_tree(source_root)
        if not internal_classes:
            return []
        if len(internal_classes) > self.warn_threshold:
            print(
                f"[grep_provider] {len(internal_classes)} internal symbols "
                f"in {source_root} -- grep cost scales linearly; consider "
                f"a RepoMap L3 index for repos this size.",
                file=sys.stderr,
            )

        source_root_abs = Path(source_root).resolve()
        # Pre-compute the set of file basenames that DEFINE a symbol, so a
        # hit on the defining file itself is not counted as a consumer.
        # (Grepping for `Foo` always returns the file declaring class Foo.)
        defining_files: dict[str, set[str]] = {}
        for fname, syms in self._file_to_symbols.items():
            if "/" in fname or "\\" in fname:
                continue
            for s in syms:
                defining_files.setdefault(s, set()).add(fname)

        results: list[dict] = []
        for sym in sorted(internal_classes):
            for hit_path in self._grep_symbol(sym):
                hp = Path(hit_path).resolve()
                if hp.name in defining_files.get(sym, set()):
                    continue
                try:
                    hp.relative_to(source_root_abs)
                    is_external = False
                except ValueError:
                    is_external = True
                results.append({
                    "class_name": sym,
                    "consumer_name": hp.stem,
                    "relation_type": "reference",
                    "is_external": is_external,
                })
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _detect_grep(self) -> str:
        """Pick the fastest available grep flavour.

        Preference order:
        - `git grep` when repo_root is a git repo (uses index, respects
          .gitignore).
        - `rg` (ripgrep) when present.
        - POSIX `grep` as universal fallback.
        """
        if (self.repo_root / ".git").exists() and shutil.which("git"):
            return "git_grep"
        if shutil.which("rg"):
            return "rg"
        if shutil.which("grep"):
            return "grep"
        raise RuntimeError(
            "grep_fallback provider needs one of: git, rg, grep. "
            "None found in PATH."
        )

    def _grep_symbol(self, symbol: str) -> list[str]:
        """Return absolute paths of files containing `symbol`.

        Identifier-bounded matching avoids `Base` hits inside `BaseService`.
        """
        try:
            if self._grep_cmd == "git_grep":
                result = subprocess.run(
                    [
                        "git", "-C", str(self.repo_root), "grep",
                        "-l", "-w", "-I",
                        symbol,
                        "--", *self.include_dirs,
                    ],
                    capture_output=True, text=True, timeout=30,
                )
            elif self._grep_cmd == "rg":
                result = subprocess.run(
                    [
                        "rg", "-l", "-w", "--no-messages",
                        symbol, *self.include_dirs,
                    ],
                    capture_output=True, text=True, timeout=30,
                )
            else:
                result = subprocess.run(
                    [
                        "grep", "-r", "-l", "-w", "-I",
                        symbol, *self.include_dirs,
                    ],
                    capture_output=True, text=True, timeout=30,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        if result.returncode not in (0, 1):
            return []
        hits: list[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if not p.is_absolute():
                p = (self.repo_root / p).resolve()
            hits.append(str(p))
        return hits

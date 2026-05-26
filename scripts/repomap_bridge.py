"""
RepoMap L3 Bridge -- parse L3 reference graph into structured data

Provides cross-module consumer queries for code-oracle pipeline.

CLI:
    python repomap_bridge.py --l3 ./context/repomap-L3-relations.md --query MyClass
    python repomap_bridge.py --l3 ./context/repomap-L3-relations.md --module MyModule --source-root ./src/
    python repomap_bridge.py --l3 ./context/repomap-L3-relations.md --min-refs 50
"""

import re
import json
import os
import argparse
from pathlib import Path

# Regexes for L3 format
RE_NODE = re.compile(r'^(\S+(?:<\w+>)?)\s+\(refs:\s*(\d+),\s*rank:\s*([\d.]+)\)')
RE_CHILD = re.compile(r'^\s+<-\s+(\S+)\s+\((\w+)\)')
RE_PARENT = re.compile(r'^\s+->\s+(\S+)\s+\((\w+)\)')


# Type-level declarations across the languages oracle commonly scans. The
# pattern intentionally over-includes ("class" inside a comment block still
# matches) -- that direction is safe because it only widens the internal set,
# never reports a false external. Function-level symbols (Go `func`, Rust
# `fn`, C/C++ free functions) are out of scope for v1 -- contracts work at
# the type boundary.
_DEFN_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|export|pub|static)?\s*"
    r"(?:abstract|sealed|final|partial|async)?\s*"
    r"(?:class|struct|interface|record|trait|impl|enum|type)\s+"
    r"([A-Z][A-Za-z0-9_]*)",
    re.MULTILINE,
)

# File extensions we will open to look for type definitions. Anything else is
# ignored -- saves time on binary/asset trees and keeps the regex from being
# applied to formats it does not understand. Extend as new languages land.
_SOURCE_SUFFIXES = {
    ".cs", ".java", ".kt", ".scala",
    ".ts", ".tsx", ".js", ".jsx",
    ".py", ".rs", ".go", ".swift",
    ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h",
    ".php", ".rb",
}


def _extract_top_level_symbols(path: Path) -> set[str]:
    """Return the set of type-level symbol names defined in `path`.

    Regex over tree-sitter: we accept the over-inclusion (matches inside
    comments or strings) because the only consequence is a slightly wider
    "internal" set, which never produces a false external positive. The
    docstring of `index_source_tree` carries the user-facing version of this
    contract.
    """
    if path.suffix.lower() not in _SOURCE_SUFFIXES:
        return set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return set(_DEFN_RE.findall(text))


class RepoMapBridge:
    """Parse and query RepoMap L3 reference graph"""

    def __init__(self, l3_path: str):
        self.nodes: dict[str, dict] = {}
        # Repo-relative path (posix-form) -> set of type-level symbol names
        # defined in that file. Populated lazily by `index_source_tree`.
        self._file_to_symbols: dict[str, set[str]] = {}
        self._parse(l3_path)

    def _parse(self, path: str):
        current_name = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue

                m = RE_NODE.match(line)
                if m:
                    current_name = m.group(1)
                    self.nodes[current_name] = {
                        "refs": int(m.group(2)),
                        "rank": float(m.group(3)),
                        "children": [],
                        "parents": [],
                    }
                    continue

                if current_name is None:
                    continue

                m = RE_CHILD.match(line)
                if m:
                    child_name, rel_type = m.group(1), m.group(2)
                    self.nodes[current_name]["children"].append({
                        "name": child_name, "type": rel_type
                    })
                    continue

                m = RE_PARENT.match(line)
                if m:
                    parent_name, rel_type = m.group(1), m.group(2)
                    self.nodes[current_name]["parents"].append({
                        "name": parent_name, "type": rel_type
                    })

    def get_consumers(self, class_name: str) -> list[dict]:
        """Get all consumers of a symbol. Kept for backward compatibility."""
        return self.get_symbol_consumers(class_name)

    def get_symbol_consumers(self, symbol_name: str) -> list[dict]:
        """Get all known consumers of a symbol."""
        node = self.nodes.get(symbol_name)
        if not node:
            return []
        return node["children"]

    def get_class_info(self, class_name: str) -> dict | None:
        """Get full info for one class"""
        return self.nodes.get(class_name)

    def get_high_impact_classes(self, min_refs: int = 10) -> list[tuple[str, int]]:
        """Get classes with refs >= threshold, sorted descending"""
        result = [
            (name, node["refs"])
            for name, node in self.nodes.items()
            if node["refs"] >= min_refs
        ]
        return sorted(result, key=lambda x: x[1], reverse=True)

    def index_source_tree(self, source_root: str) -> set[str]:
        """Walk `source_root`, parse type-level definitions, populate
        `self._file_to_symbols`, and return the full set of symbols defined
        in the tree.

        The returned set is "every type-level symbol the module declares".
        That is exactly what `get_module_external_consumers` needs to decide
        which child references go outside the module. We deliberately do
        NOT intersect with `self.nodes`: L3 only carries high-impact nodes,
        so a module-internal class that nobody outside the module imports
        will not appear there. Filtering by node membership would push such
        classes back into the "external" bucket when they are referenced
        as children of internal L3 nodes -- the exact failure mode #1 from
        Phase A was trying to fix.

        The previous implementation intersected file *stems* with L3 nodes,
        so a single source file with N type definitions only contributed at
        most one entry (the one whose name matched the file's stem). With
        full per-file parsing, multi-class hierarchies (Buff.cs style) are
        accurately classified.
        """
        self._file_to_symbols.clear()
        internal: set[str] = set()
        if not source_root or not os.path.isdir(source_root):
            return internal

        root = Path(source_root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            symbols = _extract_top_level_symbols(path)
            if not symbols:
                continue
            # Key by posix path relative to source_root for stable lookups
            # regardless of the caller's slash style.
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            self._file_to_symbols[rel] = symbols
            self._file_to_symbols[path.name] = (
                self._file_to_symbols.get(path.name, set()) | symbols
            )
            internal.update(symbols)
        return internal

    def file_to_symbols(self, file_ref: str) -> set[str]:
        """Look up the type-level symbols defined in `file_ref`.

        Callers may pass a repo-relative path, a path relative to the indexed
        source root, or a basename. The bridge first tries the exact key,
        then the basename, so multi-class file lookups work for any of those
        forms. Empty set means "no type definitions known for this file" --
        could be because the file is not source, was not indexed, or simply
        contains no top-level types.
        """
        norm = file_ref.replace("\\", "/")
        if norm in self._file_to_symbols:
            return self._file_to_symbols[norm]
        # Match by basename when callers pass a path that wasn't indexed under
        # the same root we walked.
        base = Path(norm).name
        return self._file_to_symbols.get(base, set())

    def get_module_external_consumers(
        self, module_name: str, source_root: str
    ) -> list[dict]:
        """Get external consumers of symbols defined under a module root.

        Args:
            module_name: Module name (unused directly, for logging)
            source_root: Module source directory for building internal symbol set

        Returns:
            List of {class_name, consumer_name, relation_type, is_external}
        """
        internal_classes = self.index_source_tree(source_root)

        results = []
        for cls_name, node in self.nodes.items():
            if cls_name not in internal_classes:
                continue
            for child in node["children"]:
                is_external = child["name"] not in internal_classes
                results.append({
                    "class_name": cls_name,
                    "consumer_name": child["name"],
                    "relation_type": child["type"],
                    "is_external": is_external,
                })
        return results


def main():
    parser = argparse.ArgumentParser(description="RepoMap L3 Bridge")
    parser.add_argument("--l3", required=True, help="Path to repomap-L3-relations.md")
    parser.add_argument("--query", help="Query consumers of a specific class")
    parser.add_argument("--module", help="Query external consumers of a module")
    parser.add_argument("--source-root", help="Module source root (for --module)")
    parser.add_argument("--min-refs", type=int, default=10, help="Min refs threshold")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    bridge = RepoMapBridge(args.l3)

    if args.query:
        consumers = bridge.get_consumers(args.query)
        info = bridge.get_class_info(args.query)
        if info:
            print(f"{args.query} (refs: {info['refs']}, rank: {info['rank']})")
        print(f"Consumers: {len(consumers)}")
        for c in consumers:
            print(f"  <- {c['name']} ({c['type']})")
        if args.json:
            print(json.dumps(consumers, indent=2, ensure_ascii=False))

    elif args.module and args.source_root:
        externals = bridge.get_module_external_consumers(args.module, args.source_root)
        ext_only = [e for e in externals if e["is_external"]]
        print(f"External consumers of {args.module}: {len(ext_only)}")
        for e in ext_only:
            print(f"  {e['class_name']} <- {e['consumer_name']} ({e['relation_type']})")
        if args.json:
            print(json.dumps(ext_only, indent=2, ensure_ascii=False))

    else:
        high = bridge.get_high_impact_classes(args.min_refs)
        print(f"High-impact classes (refs >= {args.min_refs}): {len(high)}")
        for name, refs in high[:20]:
            print(f"  {name}: {refs} refs")
        if len(high) > 20:
            print(f"  ... and {len(high) - 20} more")

    print(f"\nTotal nodes parsed: {len(bridge.nodes)}")


if __name__ == "__main__":
    main()

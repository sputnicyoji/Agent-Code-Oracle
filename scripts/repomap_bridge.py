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


class RepoMapBridge:
    """Parse and query RepoMap L3 reference graph"""

    def __init__(self, l3_path: str):
        self.nodes: dict[str, dict] = {}
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
        """Get all classes that inherit/implement this class"""
        node = self.nodes.get(class_name)
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

    def get_module_external_consumers(
        self, module_name: str, source_root: str
    ) -> list[dict]:
        """Get external consumers of a module's classes.

        Args:
            module_name: Module name (unused directly, for logging)
            source_root: Module source directory for building internal class set

        Returns:
            List of {class_name, consumer_name, relation_type, is_external}
        """
        internal_classes = set()
        if source_root and os.path.isdir(source_root):
            for dirpath, _, filenames in os.walk(source_root):
                for f in filenames:
                    if f.endswith(".cs"):
                        internal_classes.add(f.replace(".cs", ""))

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

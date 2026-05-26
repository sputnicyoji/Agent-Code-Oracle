"""
Shared configuration helpers for Code Oracle.

The public contract is language neutral:
- file selection is driven by glob patterns, not hard-coded extensions
- paths are stored as repo-relative strings when possible
- legacy config shapes are normalized into one internal shape
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_INCLUDE = ["**/*"]
DEFAULT_EXCLUDE = [
    "**/.git/**",
    "**/.pytest_cache/**",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/generated/**",
    "**/Generated/**",
]

DEFAULT_QUALITY_GATE = {
    "min_effective": 5,
    "max_contracts": 30,
    "min_high_value_ratio": 0.5,
    "require_evidence_for": ["blast_radius"],
}


def find_repo_root(start: str | Path | None = None) -> Path:
    """Find git root, falling back to the provided path or cwd.

    The subprocess call has a short timeout: a hung git (locked index, slow
    network mount) must not freeze the whole pipeline indefinitely.
    """
    cwd = Path(start or ".").resolve()
    if cwd.is_file():
        cwd = cwd.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        # Git missing / hung / forbidden -- fall through to cwd fallback.
        pass
    return cwd


def load_json_config(config_path: str | Path | None) -> dict[str, Any]:
    """Load oracle.config.json. Wraps JSONDecodeError with file context so a
    partial/corrupt config produces a diagnostic instead of a raw stacktrace.
    """
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path}: line {exc.lineno} col {exc.colno}: {exc.msg}"
        ) from exc


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize old and new oracle.config.json schemas."""
    raw = dict(raw or {})
    scanned_modules = raw.get("scanned_modules", {}) or {}

    source_roots: list[str] = []
    contract_paths: list[str] = list(raw.get("contract_paths", []) or [])
    normalized_modules: dict[str, dict[str, str]] = {}

    for name, value in scanned_modules.items():
        if isinstance(value, str):
            module_cfg = {"source_root": value}
        elif isinstance(value, dict):
            module_cfg = dict(value)
        else:
            continue

        source_root = module_cfg.get("source_root")
        contract_output = module_cfg.get("contract_output")
        if source_root:
            source_roots.append(source_root)
        if contract_output:
            contract_paths.append(contract_output)
        normalized_modules[name] = module_cfg

    if raw.get("source_roots"):
        source_roots = list(raw["source_roots"])

    quality_gate = dict(DEFAULT_QUALITY_GATE)
    quality_gate.update(raw.get("quality_gate", {}) or {})

    graph_provider = raw.get("graph_provider")
    if not graph_provider and raw.get("repomap_l3"):
        graph_provider = {"type": "repomap_l3", "path": raw["repomap_l3"]}

    return {
        **raw,
        "source_roots": source_roots or ["."],
        "include": list(raw.get("include", DEFAULT_INCLUDE) or DEFAULT_INCLUDE),
        "exclude": list(raw.get("exclude", DEFAULT_EXCLUDE) or DEFAULT_EXCLUDE),
        "quality_gate": quality_gate,
        "scanned_modules": normalized_modules,
        "contract_paths": list(dict.fromkeys(contract_paths)),
        "graph_provider": graph_provider,
    }


def _as_posix(path: Path) -> str:
    return path.as_posix()


def to_repo_relative(path: str | Path, repo_root: str | Path) -> str:
    p = Path(path)
    root = Path(repo_root).resolve()
    try:
        return _as_posix(p.resolve().relative_to(root))
    except Exception:
        return _as_posix(p)


def matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def is_included(path: str, include: list[str], exclude: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return matches_any(normalized, include) and not matches_any(normalized, exclude)


def build_file_index(
    roots: list[str | Path],
    repo_root: str | Path | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> dict[str, list[str]]:
    """Build an index with both repo-relative paths and basenames.

    Values are repo-relative paths. Keys include:
    - exact repo-relative path
    - basename, only as a lookup key for legacy contracts
    """
    repo = Path(repo_root).resolve() if repo_root else find_repo_root()
    include = include or DEFAULT_INCLUDE
    exclude = exclude or DEFAULT_EXCLUDE
    index: dict[str, list[str]] = {}

    for root_value in roots or [repo]:
        root = Path(root_value)
        if not root.is_absolute():
            root = repo / root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = to_repo_relative(path, repo)
            if not is_included(rel, include, exclude):
                continue
            index.setdefault(rel, []).append(rel)
            index.setdefault(path.name, []).append(rel)

    return index


def resolve_file_ref(file_ref: str, index: dict[str, list[str]]) -> str | None:
    """Resolve a path or basename to a unique repo-relative path.

    Multiple equal entries (same path indexed under both its full key and its
    basename key, or via overlapping source roots) are deduped so the lookup
    still resolves cleanly. A genuine basename ambiguity (two different files
    sharing a name) still returns None.
    """
    matches = index.get(file_ref.replace("\\", "/")) or index.get(Path(file_ref).name)
    if not matches:
        return None
    # Order-preserving dedup -- multiple references to the same file are not
    # ambiguous; only multiple *distinct* repo-relative paths are.
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def extract_contract_paths(contract: dict[str, Any], key: str = "involved_files") -> list[str]:
    """Read legacy *_files fields and schema-v2 involved/affected entries."""
    if key in contract and isinstance(contract[key], list):
        return [str(x) for x in contract[key]]

    object_key = "involved" if key == "involved_files" else "affected_external"
    result = []
    for item in contract.get(object_key, []) or []:
        if isinstance(item, dict) and item.get("path"):
            result.append(str(item["path"]))
        elif isinstance(item, str):
            result.append(item)
    return result


def set_contract_paths(contract: dict[str, Any], paths: list[str], key: str = "involved_files") -> None:
    """Write paths to both the v1 (`*_files`) key and the v2 mirror (`involved`
    or `affected_external`). Keeping the two representations in sync avoids
    split-brain contract outputs where downstream readers see different paths
    depending on which schema they prefer.

    Any pre-existing v2 `symbols` metadata for a kept path is preserved.
    Paths absent from `paths` are dropped from the v2 mirror too.
    """
    contract[key] = list(paths)

    object_key = "involved" if key == "involved_files" else "affected_external"
    existing = contract.get(object_key)

    symbols_by_path: dict[str, list[str]] = {}
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("path") and item.get("symbols"):
                symbols_by_path[str(item["path"])] = list(item["symbols"])

    mirror: list[dict[str, Any]] = []
    for p in paths:
        entry: dict[str, Any] = {"path": p}
        syms = symbols_by_path.get(p)
        if syms:
            entry["symbols"] = syms
        mirror.append(entry)
    contract[object_key] = mirror

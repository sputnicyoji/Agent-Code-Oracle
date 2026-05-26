"""Round driver: orchestrate the 4-round extraction flow.

Phase D-mini scope: Round 0 is deterministic (uses oracle's existing
graph provider). Rounds 1-3 invoke an LLMProvider; with --provider
dry_run the prompts are printed and stub responses are returned, so
the whole flow is exercisable without any API access.

Output:
  {output_dir}/round0.json   Round 0 artifact (typed)
  {output_dir}/round1.json   Round 1 architecture summary + candidate seeds
  {output_dir}/round2.json   Round 2 contract candidates
  {output_dir}/round3.json   Round 3 filtered contracts (pipeline-ready)

The Round 3 output is in the exact shape pipeline.py --input expects.

CLI:
  python scripts/round_driver.py \\
    --config oracle.config.json \\
    --module RoguelikeTower \\
    --module-source client/Assets/X1/ScriptGame/RoguelikeTower \\
    --module-docs docs/x15-module-document/RoguelikeTower_ForAI \\
    --provider dry_run \\
    --output-dir docs/x15-module-document/RoguelikeTower_ForAI/oracle-rounds
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from oracle_config import (
    load_json_config,
    normalize_config,
    resolve_config_path,
)
from round_artifacts import (
    Round0Artifact,
    Round1Artifact,
    Round2Artifact,
    Round3Artifact,
)


REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "templates"


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _make_provider(name: str):
    if name == "dry_run":
        from providers.dry_run_provider import DryRunProvider
        return DryRunProvider()
    if name in ("anthropic", "openai", "ollama", "claude_code"):
        raise SystemExit(
            f"Provider {name!r} is not implemented yet. "
            "Phase D-mini ships only the dry_run provider; the real "
            "providers land in a future mission. Use --provider dry_run "
            "to render and inspect the prompts."
        )
    raise SystemExit(f"Unknown provider: {name!r}")


# ---------------------------------------------------------------------------
# Round 0 -- deterministic
# ---------------------------------------------------------------------------

def _build_source_index(source_root: Path, max_entries: int = 200) -> str:
    """Cheap markdown-ish index of files under source_root. We do not
    walk forever -- after `max_entries` files we truncate. This is for
    the LLM's reference, not a real index.
    """
    if not source_root.is_dir():
        return f"(source root not found: {source_root})"
    lines: list[str] = []
    count = 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root).as_posix()
        lines.append(f"- {rel}")
        count += 1
        if count >= max_entries:
            lines.append(f"... ({count}+ more files truncated)")
            break
    return "\n".join(lines) if lines else "(empty source tree)"


def _build_evidence_pack(provider_type: str, provider, module_name: str,
                        source_root: str) -> tuple[list[dict], int, int]:
    """Run the graph provider and return (evidence_dicts, internal_count,
    cross_edges). Both counts are 0 when no provider is configured.
    """
    if provider is None:
        return [], 0, 0
    externals = provider.get_module_external_consumers(module_name, source_root)
    internal = provider.index_source_tree(source_root)
    cross = sum(1 for e in externals if e.get("is_external"))
    evidence = [
        {
            "class_name": e.get("class_name"),
            "consumer_name": e.get("consumer_name"),
            "relation_type": e.get("relation_type"),
            "is_external": e.get("is_external", False),
        }
        for e in externals
        if e.get("is_external")
    ]
    return evidence, len(internal), cross


def _make_graph_provider(cfg: dict):
    """Build the graph provider from config. Mirrors pipeline.py's logic
    so Round 0 and Stage 0 produce the same evidence shape.
    """
    gp = cfg.get("graph_provider") or {}
    gp_type = gp.get("type")
    if gp_type == "repomap_l3":
        from repomap_bridge import RepoMapBridge
        path = resolve_config_path(cfg, ["graph_provider", "path"])
        if path is None:
            return None, "none"
        return RepoMapBridge(str(path)), "repomap_l3"
    if gp_type == "grep_fallback":
        from providers.grep_provider import GrepProvider
        repo_root = gp.get("repo_root") or cfg.get("_config_dir") or os.getcwd()
        return GrepProvider(repo_root=repo_root, include_dirs=gp.get("include_dirs")), "grep_fallback"
    return None, "none"


def _format_evidence_for_prompt(evidence: list[dict]) -> str:
    if not evidence:
        return "(no cross-module consumers detected by the configured graph provider)"
    lines = [f"- {e['class_name']} <- {e['consumer_name']} ({e['relation_type']})"
             for e in evidence[:60]]
    if len(evidence) > 60:
        lines.append(f"... and {len(evidence) - 60} more cross-module edges")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _load_template(templates_dir: Path, name: str) -> tuple[dict, str]:
    """Load a template file. Returns (frontmatter_dict, body)."""
    path = templates_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    frontmatter: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            frontmatter[k.strip()] = v.strip().strip("\"'")
    return frontmatter, body


def _render(template_body: str, **placeholders) -> str:
    """Replace {name} placeholders. Missing placeholders become an
    empty string -- the driver decides which keys to provide per round.
    """
    out = template_body
    for key, value in placeholders.items():
        out = out.replace("{" + key + "}", str(value))
    # Strip any remaining placeholders so the LLM does not see literal
    # `{foo}` markers. Two-pass regex catches anything not provided.
    out = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "(not provided)", out)
    return out


# ---------------------------------------------------------------------------
# Driver flow
# ---------------------------------------------------------------------------

def _load_module_docs(module_docs_dir: Path | None, max_chars: int = 30_000) -> str:
    """Concatenate Patterns.md / Constraints.md / BestPractice.md / Index.md
    from the module docs directory, truncating to `max_chars`. Empty
    string when no docs supplied.
    """
    if module_docs_dir is None or not module_docs_dir.is_dir():
        return ""
    parts: list[str] = []
    for name in ("Index.md", "Patterns.md", "Constraints.md", "BestPractice.md"):
        candidates = list(module_docs_dir.glob(f"*.{name.split('.')[-1]}"))
        target = next(
            (c for c in candidates if c.name.endswith(name)),
            None,
        )
        if target is None:
            continue
        try:
            parts.append(f"### {target.name}\n\n" + target.read_text(encoding="utf-8"))
        except OSError:
            continue
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + f"\n\n... (module docs truncated at {max_chars} chars)"
    return blob


def _excerpt_seed_files(source_root: Path, seeds: list[dict],
                        max_total_chars: int = 60_000) -> str:
    """Concatenate seed file contents with file dividers. Keeps the
    total under max_total_chars so the Round 2 prompt does not balloon.
    """
    if not seeds:
        return "(no candidate seeds provided -- Round 2 has no source excerpts)"
    chunks: list[str] = []
    used = 0
    for entry in seeds:
        rel = entry.get("file", "")
        if not rel:
            continue
        path = source_root / rel
        if not path.is_file():
            chunks.append(f"### {rel}\n(file not found)\n")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        header = f"### {rel}\n```\n"
        footer = "\n```\n"
        budget = max_total_chars - used - len(header) - len(footer)
        if budget <= 0:
            chunks.append(f"### {rel}\n(remaining seeds skipped -- token budget exhausted)\n")
            break
        excerpt = text if len(text) <= budget else text[:budget] + "\n... (truncated)\n"
        chunks.append(header + excerpt + footer)
        used += len(chunks[-1])
    return "\n".join(chunks)


def run_driver(
    config_path: str | None,
    module_name: str,
    module_source: str,
    module_docs: str | None,
    provider_name: str,
    output_dir: Path,
    templates_dir: Path,
) -> dict:
    cfg = normalize_config(load_json_config(config_path)) if config_path else normalize_config({})
    source_root = Path(module_source).resolve()
    docs_dir = Path(module_docs).resolve() if module_docs else None
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Round Driver ===", file=sys.stderr)
    print(f"Module: {module_name}", file=sys.stderr)
    print(f"Source: {source_root}", file=sys.stderr)
    print(f"Provider: {provider_name}", file=sys.stderr)
    print(f"Output: {output_dir}", file=sys.stderr)

    provider = _make_provider(provider_name)

    # Round 0
    graph_provider, gp_type = _make_graph_provider(cfg)
    evidence, internal_count, cross = _build_evidence_pack(
        gp_type, graph_provider, module_name, str(source_root)
    )
    round0 = Round0Artifact(
        module_name=module_name,
        source_root=str(source_root),
        source_index=_build_source_index(source_root),
        evidence=evidence,
        provider_type=gp_type,
        internal_class_count=internal_count,
        cross_edges=cross,
    )
    (output_dir / "round0.json").write_text(
        json.dumps(round0.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Round 0] internal={internal_count} cross_edges={cross} "
          f"evidence_entries={len(evidence)} provider={gp_type}", file=sys.stderr)

    docs_blob = _load_module_docs(docs_dir)
    evidence_text = _format_evidence_for_prompt(evidence)

    # Round 1
    fm1, body1 = _load_template(templates_dir, "round1_architect.txt")
    prompt1 = _render(
        body1,
        module_name=module_name,
        source_index=round0.source_index,
        round0_evidence=evidence_text,
        module_docs=docs_blob or "(no module docs supplied)",
    )
    r1_text = provider.call("", prompt1, response_format="text",
                            max_tokens=int(fm1.get("max_output_tokens", "6000")),
                            round="round1")
    round1 = Round1Artifact.from_llm_response(r1_text)
    (output_dir / "round1.json").write_text(
        json.dumps(round1.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Round 1] summary chars={len(round1.architecture_summary)} "
          f"seeds={len(round1.candidate_seeds)}", file=sys.stderr)

    # Round 2
    fm2, body2 = _load_template(templates_dir, "round2_contract_mining.txt")
    prompt2 = _render(
        body2,
        module_name=module_name,
        architecture_summary=round1.architecture_summary,
        candidate_seeds=json.dumps(round1.candidate_seeds, indent=2),
        seed_source_excerpts=_excerpt_seed_files(source_root, round1.candidate_seeds),
    )
    r2_text = provider.call("", prompt2, response_format="json",
                            max_tokens=int(fm2.get("max_output_tokens", "12000")),
                            round="round2")
    round2 = Round2Artifact.from_llm_response(r2_text)
    (output_dir / "round2.json").write_text(
        json.dumps(round2.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Round 2] candidate contracts={len(round2.contracts)}", file=sys.stderr)

    # Round 3
    fm3, body3 = _load_template(templates_dir, "round3_devils_advocate.txt")
    prompt3 = _render(
        body3,
        module_name=module_name,
        round2_contracts=json.dumps(round2.contracts, indent=2, ensure_ascii=False),
    )
    r3_text = provider.call("", prompt3, response_format="json",
                            max_tokens=int(fm3.get("max_output_tokens", "12000")),
                            round="round3")
    round3 = Round3Artifact.from_llm_response(r3_text)
    (output_dir / "round3.json").write_text(
        json.dumps(round3.contracts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Round 3] surviving contracts={len(round3.contracts)}", file=sys.stderr)
    print(f"Done. Pipe round3.json into pipeline.py to validate + gate.", file=sys.stderr)

    return {
        "round0": round0.to_dict(),
        "round1": round1.to_dict(),
        "round2": round2.to_dict(),
        "round3": round3.to_dict(),
    }


def main():
    parser = argparse.ArgumentParser(description="Code Oracle Round Driver (Phase D-mini)")
    parser.add_argument("--config", help="Path to oracle.config.json")
    parser.add_argument("--module", required=True, help="Module name (e.g. RoguelikeTower)")
    parser.add_argument("--module-source", required=True, help="Module source directory")
    parser.add_argument("--module-docs", help="Module documentation directory (Index/Patterns/...)")
    parser.add_argument("--provider", default="dry_run",
                        help="LLM provider (dry_run | anthropic | openai | ollama | claude_code). "
                             "Only dry_run is implemented in Phase D-mini.")
    parser.add_argument("--output-dir", required=True, help="Where to write round{0,1,2,3}.json")
    parser.add_argument("--templates-dir", help=f"Override templates directory (default: {DEFAULT_TEMPLATES_DIR})")

    args = parser.parse_args()

    templates_dir = Path(args.templates_dir).resolve() if args.templates_dir else DEFAULT_TEMPLATES_DIR
    if not templates_dir.is_dir():
        print(f"Error: templates dir not found: {templates_dir}", file=sys.stderr)
        sys.exit(1)

    run_driver(
        config_path=args.config,
        module_name=args.module,
        module_source=args.module_source,
        module_docs=args.module_docs,
        provider_name=args.provider,
        output_dir=Path(args.output_dir),
        templates_dir=templates_dir,
    )


if __name__ == "__main__":
    main()

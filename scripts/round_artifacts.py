"""Typed artifacts produced by each round of the extraction driver.

Round 0 is fully deterministic (driven by the round_driver itself).
Round 1, 2, 3 are LLM-produced; each carries a `from_llm_response`
classmethod that turns the provider's response string into a typed
artifact + a `to_dict` for serialisation.

These shapes ARE the contract between the driver and the templates --
keeping them in one file makes it obvious when a template placeholder
must change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Round0Artifact:
    """Output of Round 0 (deterministic static-provider discovery).

    `source_index` is a markdown-ish summary of files / directories; it
    gets dropped into the Round 1 prompt directly. `evidence` is a
    structured representation of the static-provider's cross-module
    consumer dump.
    """

    module_name: str
    source_root: str
    source_index: str  # markdown-ish file tree summary
    evidence: list[dict] = field(default_factory=list)
    provider_type: str = "none"  # "repomap_l3" | "grep_fallback" | "none"
    internal_class_count: int = 0
    cross_edges: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Round0Artifact":
        return cls(**data)


@dataclass
class Round1Artifact:
    """Output of Round 1 (Architect's Eye).

    `architecture_summary` is free-form prose. `candidate_seeds` is the
    YAML-block at the end of the Round 1 prompt -- a list of files
    Round 2 should read in detail.
    """

    architecture_summary: str
    candidate_seeds: list[dict] = field(default_factory=list)  # [{file, reason}]
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_summary": self.architecture_summary,
            "candidate_seeds": self.candidate_seeds,
        }

    @classmethod
    def from_llm_response(cls, text: str) -> "Round1Artifact":
        """Parse the Round 1 response.

        The template asks the LLM to end with a `## candidate_seeds`
        YAML block. We split on that header and stash the rest as the
        architecture summary. Parsing is intentionally forgiving --
        a malformed seeds block becomes an empty list, not an error,
        because Round 2 can fall back to "read every involved file".
        """
        summary = text
        seeds: list[dict] = []
        marker = "## candidate_seeds"
        idx = text.find(marker)
        if idx != -1:
            summary = text[:idx].rstrip()
            seeds_block = text[idx + len(marker):].strip()
            # Naive YAML: lines like `- file: foo, reason: bar` or full YAML
            for line in seeds_block.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("-"):
                    continue
                entry = line.lstrip("-").strip()
                # Accept both {file: x, reason: y} and "x -- y" forms.
                file_part = entry
                reason_part = ""
                if "," in entry:
                    parts = [p.strip() for p in entry.split(",", 1)]
                    file_part = parts[0]
                    reason_part = parts[1] if len(parts) > 1 else ""
                file_value = file_part.split(":", 1)[-1].strip() if ":" in file_part else file_part
                reason_value = reason_part.split(":", 1)[-1].strip() if ":" in reason_part else reason_part
                if file_value:
                    seeds.append({"file": file_value, "reason": reason_value})
        return cls(
            architecture_summary=summary,
            candidate_seeds=seeds,
            raw_response=text,
        )


@dataclass
class Round2Artifact:
    """Output of Round 2 (Contract Mining) -- a list of schema-v2 contracts."""

    contracts: list[dict] = field(default_factory=list)
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"contracts": self.contracts}

    @classmethod
    def from_llm_response(cls, text: str) -> "Round2Artifact":
        """Parse the JSON array from the LLM response.

        Tolerates markdown fences (```json ... ```) because LLMs add
        them even when the prompt says not to.
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Strip a code fence: drop first line and optional trailing fence.
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Cannot parse; preserve raw and return empty list. Driver
            # decides whether to fail or surface a warning.
            return cls(contracts=[], raw_response=text)
        if isinstance(data, dict) and "contracts" in data:
            data = data["contracts"]
        if not isinstance(data, list):
            return cls(contracts=[], raw_response=text)
        return cls(contracts=data, raw_response=text)


@dataclass
class Round3Artifact:
    """Output of Round 3 (Devil's Advocate) -- filtered contracts."""

    contracts: list[dict] = field(default_factory=list)
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"contracts": self.contracts}

    @classmethod
    def from_llm_response(cls, text: str) -> "Round3Artifact":
        # Same parsing shape as Round 2.
        r2 = Round2Artifact.from_llm_response(text)
        return cls(contracts=r2.contracts, raw_response=r2.raw_response)

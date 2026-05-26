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

        Seed line shapes accepted:

            - file: src/foo.cs, reason: load-bearing      # canonical
            - reason: load-bearing, file: src/foo.cs      # reversed order
            - file: src/foo.cs                            # no reason
            - file: src/foo.cs # inline yaml comment      # comment stripped
            - src/foo.cs                                  # bare path

        Field identification is by KEY NAME, not by position -- a
        future LLM run that swaps the order is still parsed correctly.
        Trailing ` # ...` inline YAML comments are stripped before
        parsing so they do not get pulled into the value text.
        """
        summary = text
        seeds: list[dict] = []
        marker = "## candidate_seeds"
        idx = text.find(marker)
        if idx == -1:
            return cls(architecture_summary=summary, candidate_seeds=seeds, raw_response=text)
        summary = text[:idx].rstrip()
        seeds_block = text[idx + len(marker):].strip()
        for line in seeds_block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("-"):
                continue
            entry = line.lstrip("-").strip()
            # Strip a trailing YAML inline comment. We require the `#`
            # to be preceded by whitespace so a `#` inside a value
            # (rare but possible) is not stripped.
            hash_idx = entry.find(" #")
            if hash_idx != -1:
                entry = entry[:hash_idx].rstrip()
            if not entry:
                continue

            # Split into comma-separated parts; identify file and reason
            # by KEY NAME rather than position.
            file_value = ""
            reason_value = ""
            fallback_value = ""  # first part with no `key:` prefix
            for part in (p.strip() for p in entry.split(",")):
                if not part:
                    continue
                if ":" in part:
                    key, _, val = part.partition(":")
                    key_norm = key.strip().lower()
                    val_norm = val.strip()
                    if key_norm == "file":
                        file_value = val_norm
                    elif key_norm == "reason":
                        reason_value = val_norm
                    elif not fallback_value:
                        # Unknown key looks like a colon-bearing value
                        # (e.g. a Windows-ish path). Treat it as a
                        # fallback if no other file value lands.
                        fallback_value = part
                elif not fallback_value:
                    fallback_value = part
            if not file_value:
                file_value = fallback_value
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

        Tolerant of three LLM-output quirks:

        1. Markdown fences (` ```json ... ``` `) at the START of the
           response -- strip the fence pair.
        2. Prose preceding a fenced block (`Here's the JSON: ```json
           [...]``` `) -- search for the fence anywhere in the text,
           strip language tag, take the fenced content.
        3. Prose preceding a bare JSON array (`Sure! [...]`) -- locate
           the first `[` or `{` and treat that as the start of JSON.

        These quirks are common enough in practice that being strict
        means losing a whole Round 2's contracts to a stylistic LLM
        choice. Forgiving parsing trades a small amount of "we
        accidentally parsed something that wasn't meant to be JSON"
        risk for not silently dropping legitimate output.
        """
        cleaned = cls._strip_to_json(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return cls(contracts=[], raw_response=text)
        if isinstance(data, dict) and "contracts" in data:
            data = data["contracts"]
        if not isinstance(data, list):
            return cls(contracts=[], raw_response=text)
        return cls(contracts=data, raw_response=text)

    @staticmethod
    def _strip_to_json(text: str) -> str:
        """Return the substring of `text` most likely to be JSON.

        Algorithm:

        - If a markdown fence exists anywhere, extract the fenced
          content (drop optional `json` language tag on the opener).
        - Otherwise, scan for the first `[` or `{` and return text
          from that index. If neither exists, return the stripped
          input (json.loads will fail loudly enough for the caller).
        """
        s = text.strip()
        fence_idx = s.find("```")
        if fence_idx != -1:
            after = s[fence_idx + 3:]
            # Drop the optional language tag on the fence opener line.
            nl = after.find("\n")
            if nl != -1:
                after = after[nl + 1:]
            # Strip the closing fence if present.
            end = after.rfind("```")
            if end != -1:
                return after[:end].strip()
            return after.strip()
        # No fence; locate the JSON body.
        candidates = [i for i in (s.find("["), s.find("{")) if i != -1]
        if not candidates:
            return s
        start = min(candidates)
        return s[start:]


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

"""
Contract Validator — Stage 1

Validates contract format and reference integrity:
1. type must be one of 5 valid values
2. required fields are complete
3. files in involved_files exist in the source directory
4. at least 1 valid involved_file
5. confidence is in 0-1 range
"""

from pathlib import Path

from oracle_config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    build_file_index,
    extract_contract_paths,
    find_repo_root,
    resolve_file_ref,
    set_contract_paths,
)


VALID_TYPES = {"data_flow", "ordering", "rationale", "thread_safety", "blast_radius"}

# Evidence kind taxonomy (Phase C #8). The first three are "strong" because
# they cite a source the agent can actually look at -- a reference graph
# node, a code comment with file:line, a data-flow trace between
# locations. `design_rationale` is "weak" because it appeals to context
# the agent cannot directly verify (doc, ticket, conversation). The
# quality gate's `require_strong_evidence_for` option uses this split.
VALID_EVIDENCE_KINDS = {
    "static_reference",
    "code_comment",
    "data_flow_trace",
    "design_rationale",
}
STRONG_EVIDENCE_KINDS = {
    "static_reference",
    "code_comment",
    "data_flow_trace",
}

# Scalar required fields. The "paths" requirement (v1 `involved_files` OR v2
# `involved`) is checked separately via the schema bridge so v2-only contracts
# are not silently rejected for missing the legacy key.
REQUIRED_SCALAR_FIELDS = {
    "type", "title", "description",
    "blind_spot", "violation_consequence", "confidence",
}


def _has_involved_paths(contract: dict) -> bool:
    """True iff the contract carries at least one involved path in either schema."""
    return bool(extract_contract_paths(contract, "involved_files"))


class ContractValidator:
    """Contract format and reference validator"""

    def __init__(
        self,
        source_root: str = None,
        repo_root: str = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ):
        """
        Args:
            source_root: Source root directory (for validating involved_files existence)
        """
        self.source_root = source_root
        self.repo_root = Path(repo_root).resolve() if repo_root else find_repo_root(source_root)
        # Defensive copy: aliasing the module-level DEFAULT_INCLUDE/EXCLUDE
        # lists means any caller's `.append(...)` would silently pollute every
        # future instance in the same process.
        self.include = list(include) if include is not None else list(DEFAULT_INCLUDE)
        self.exclude = list(exclude) if exclude is not None else list(DEFAULT_EXCLUDE)
        self._file_cache: dict[str, list[str]] = {}
        if source_root:
            self._build_file_cache(source_root)

    def _build_file_cache(self, root: str):
        """Build path/basename -> repo-relative path cache."""
        self._file_cache = build_file_index(
            [root],
            repo_root=self.repo_root,
            include=self.include,
            exclude=self.exclude,
        )

    def resolve_absolute(self, file_ref: str) -> Path | None:
        """Look up `file_ref` (repo-relative or basename) and return the
        absolute filesystem path, or None when the file does not exist
        under the validator's source root.

        Public because the pipeline's hash step needs to read file content
        for sha256; reusing this resolver avoids walking the source tree
        a second time.
        """
        rel = resolve_file_ref(file_ref, self._file_cache)
        if rel is None:
            return None
        p = (self.repo_root / rel)
        return p if p.exists() else None

    def process(self, contracts: list[dict]) -> list[dict]:
        """
        Validate contract list

        Args:
            contracts: Raw contract list

        Returns:
            List of contracts that passed validation
        """
        valid = []
        for i, c in enumerate(contracts):
            errors = self._validate(c, i)
            # Split: real validation errors (drop-worthy) vs evidence
            # warnings (cosmetic). Evidence-only problems must NOT cause
            # _try_fix to drop the contract.
            structural = [e for e in errors if not e.startswith("evidence[")
                          and not e.startswith("evidence must be a list")]
            evidence_warnings = [e for e in errors if e not in structural]
            for err in structural:
                print(f"  [WARN] Contract #{i}: {err}")
            for w in evidence_warnings:
                print(f"  [WARN] Contract #{i}: {w}")
            if structural:
                original_title = c.get("title", "unknown")
                c = self._try_fix(c)
                if c is None:
                    print(f"  [DROP] Contract #{i}: {original_title}")
                    continue
            valid.append(c)
        return valid

    def _validate(self, contract: dict, index: int) -> list[str]:
        """Validate a single contract, return list of errors"""
        errors = []

        # 1. Type check
        if contract.get("type") not in VALID_TYPES:
            errors.append(f"Invalid type: {contract.get('type')}. Must be one of {VALID_TYPES}")

        # 2. Required scalar fields (schema-version-independent)
        missing = REQUIRED_SCALAR_FIELDS - set(contract.keys())
        if missing:
            errors.append(f"Missing fields: {missing}")

        # 2b. Involved paths: v1 (involved_files) OR v2 (involved) must yield
        # at least one path through the schema bridge. v2-native contracts
        # would previously fail the missing-fields check above; that key is
        # no longer required so this is the authoritative paths check.
        if not _has_involved_paths(contract):
            errors.append(
                "Missing involved paths (need 'involved' (v2) or 'involved_files' (v1))"
            )

        # 3. involved_files existence
        if self.source_root:
            for f in extract_contract_paths(contract, "involved_files"):
                matches = self._file_cache.get(f.replace("\\", "/")) or self._file_cache.get(Path(f).name)
                if not matches:
                    errors.append(f"File not found: {f}")
                elif len(matches) > 1 and f == Path(f).name:
                    errors.append(f"Ambiguous basename, use repo-relative path: {f}")

        # 4. confidence range
        conf = contract.get("confidence")
        if conf is not None and (not isinstance(conf, (int, float)) or conf < 0 or conf > 1):
            errors.append(f"Invalid confidence: {conf}. Must be 0-1")

        # 5. evidence kind taxonomy. Warnings, not fatals: an unrecognised
        # evidence entry indicates LLM drift but does not invalidate the
        # contract -- the gate can still decide based on the remaining
        # evidence entries.
        for warn in self._validate_evidence(contract):
            errors.append(warn)

        return errors

    def _validate_evidence(self, contract: dict) -> list[str]:
        """Check evidence[] for kind enum membership + per-kind shape.

        Returns a list of warning strings (empty when all evidence is
        well-formed). The caller treats these like other validation
        errors but `_try_fix` does NOT drop a contract for evidence
        problems -- evidence is supplemental.
        """
        warnings: list[str] = []
        evidence = contract.get("evidence") or []
        if not isinstance(evidence, list):
            warnings.append(f"evidence must be a list, got {type(evidence).__name__}")
            return warnings
        for i, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                warnings.append(f"evidence[{i}] is not an object")
                continue
            kind = ev.get("kind")
            if kind is None:
                warnings.append(f"evidence[{i}] missing 'kind'")
                continue
            if kind not in VALID_EVIDENCE_KINDS:
                warnings.append(
                    f"evidence[{i}] unknown kind {kind!r}. "
                    f"Valid: {sorted(VALID_EVIDENCE_KINDS)}"
                )
                continue
            # Per-kind shape checks. Keep these soft so a malformed entry
            # is reported but does not block the rest of the pipeline.
            if kind == "code_comment":
                src = ev.get("source", "")
                if not isinstance(src, str) or ":" not in src:
                    warnings.append(
                        f"evidence[{i}] code_comment 'source' should look "
                        f"like 'file:line'; got {src!r}"
                    )
            elif kind == "static_reference":
                tgt = ev.get("target", "")
                if not isinstance(tgt, str) or "#" not in tgt:
                    warnings.append(
                        f"evidence[{i}] static_reference 'target' should look "
                        f"like 'path#symbol'; got {tgt!r}"
                    )
            # data_flow_trace / design_rationale: free-form text on
            # source/target; no extra shape check.
        return warnings

    def _try_fix(self, contract: dict) -> dict | None:
        """Try to fix contract (remove non-existent files)"""
        if not isinstance(contract, dict):
            return None

        # Remove non-existent files
        if self.source_root:
            valid_files = []
            for f in extract_contract_paths(contract, "involved_files"):
                resolved = resolve_file_ref(f, self._file_cache)
                if resolved:
                    valid_files.append(resolved)
            set_contract_paths(contract, valid_files, "involved_files")

        # At least 1 valid path -- check through the bridge so v2 contracts
        # whose paths live under `involved` (not `involved_files`) are kept.
        if not _has_involved_paths(contract):
            return None

        # Re-validate other fields (scalars only; paths already checked above)
        if contract.get("type") not in VALID_TYPES:
            return None
        if REQUIRED_SCALAR_FIELDS - set(contract.keys()):
            return None

        return contract

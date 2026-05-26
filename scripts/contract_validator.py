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
            if errors:
                for err in errors:
                    print(f"  [WARN] Contract #{i}: {err}")
                # Try to fix: remove non-existent files and re-check
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

        return errors

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

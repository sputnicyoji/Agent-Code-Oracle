"""
Contract Validator — Stage 1

Validates contract format and reference integrity:
1. type must be one of 5 valid values
2. required fields are complete
3. files in involved_files exist in the source directory
4. at least 1 valid involved_file
5. confidence is in 0-1 range
"""

import os
from pathlib import Path


VALID_TYPES = {"data_flow", "ordering", "rationale", "thread_safety", "blast_radius"}

REQUIRED_FIELDS = {"type", "title", "description", "blind_spot", "violation_consequence", "involved_files", "confidence"}


class ContractValidator:
    """Contract format and reference validator"""

    def __init__(self, source_root: str = None):
        """
        Args:
            source_root: Source root directory (for validating involved_files existence)
        """
        self.source_root = source_root
        self._file_cache = {}
        if source_root:
            self._build_file_cache(source_root)

    def _build_file_cache(self, root: str):
        """Build filename -> path cache"""
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.endswith(".cs"):
                    self._file_cache[f] = os.path.join(dirpath, f)

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

        # 2. Required fields
        missing = REQUIRED_FIELDS - set(contract.keys())
        if missing:
            errors.append(f"Missing fields: {missing}")

        # 3. involved_files existence
        if self.source_root and "involved_files" in contract:
            for f in contract["involved_files"]:
                if f not in self._file_cache:
                    errors.append(f"File not found: {f}")

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
        if self.source_root and "involved_files" in contract:
            valid_files = [f for f in contract["involved_files"] if f in self._file_cache]
            contract["involved_files"] = valid_files

        # At least 1 valid file
        if not contract.get("involved_files"):
            return None

        # Re-validate other fields
        if contract.get("type") not in VALID_TYPES:
            return None
        if REQUIRED_FIELDS - set(contract.keys()):
            return None

        return contract

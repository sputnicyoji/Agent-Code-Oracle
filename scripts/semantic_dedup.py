"""
Semantic Dedup — Stage 2

Title text similarity-based deduplication using only the Python stdlib:
- Pass 1: same type + title similarity > threshold -> duplicate
- Pass 2: cross-type: involved overlap > 60% + description similarity > 0.5 -> duplicate

Rule discovery is the LLM's job (Round 1-3). This stage only removes
near-duplicates the LLM produced. We deliberately keep this layer
provider-free: no embeddings, no network, no optional dependencies.
"""

import difflib

from oracle_config import extract_contract_paths


# Type priority: lower number = higher priority
TYPE_PRIORITY = {
    "blast_radius": 0,
    "rationale": 1,
    "data_flow": 2,
    "ordering": 3,
    "thread_safety": 4,
}


class SemanticDedup:
    """Two-pass deduplicator over LLM-produced contracts."""

    def __init__(self,
                 threshold: float = 0.8,
                 cross_type_file_overlap: float = 0.6,
                 cross_type_desc_threshold: float = 0.5):
        self.threshold = threshold
        self.cross_type_file_overlap = cross_type_file_overlap
        self.cross_type_desc_threshold = cross_type_desc_threshold

    def process(self, contracts: list[dict]) -> list[dict]:
        # Pass 1: same-type title dedup
        after_same_type = self._dedup_same_type(contracts)

        # Pass 2: cross-type difflib dedup
        return self._dedup_cross_type(after_same_type)

    def _dedup_same_type(self, contracts: list[dict]) -> list[dict]:
        """Same-type deduplication"""
        unique = []
        removed_count = 0

        for c in contracts:
            is_dup = False
            for i, existing in enumerate(unique):
                if self._is_same_type_dup(c, existing):
                    if c.get("confidence", 0) > existing.get("confidence", 0):
                        unique[i] = c
                        print(f"  [REPLACE] '{existing['title'][:50]}' -> '{c['title'][:50]}'")
                    else:
                        print(f"  [SKIP] '{c['title'][:50]}' (dup of '{existing['title'][:50]}')")
                    is_dup = True
                    removed_count += 1
                    break

            if not is_dup:
                unique.append(c)

        if removed_count > 0:
            print(f"  Same-type: removed {removed_count} duplicates")
        return unique

    def _dedup_cross_type(self, contracts: list[dict]) -> list[dict]:
        """Cross-type dedup: high involved overlap + description semantic similarity"""
        unique = []
        removed_count = 0

        for c in contracts:
            is_dup = False
            for i, existing in enumerate(unique):
                if self._is_cross_type_dup(c, existing):
                    # Keep the higher-priority type
                    c_pri = TYPE_PRIORITY.get(c.get("type"), 99)
                    e_pri = TYPE_PRIORITY.get(existing.get("type"), 99)

                    if c_pri < e_pri:
                        print(f"  [CROSS-REPLACE] '{existing['title'][:40]}' ({existing['type']}) "
                              f"-> '{c['title'][:40]}' ({c['type']})")
                        unique[i] = c
                    else:
                        print(f"  [CROSS-SKIP] '{c['title'][:40]}' ({c['type']}) "
                              f"(overlap with '{existing['title'][:40]}' ({existing['type']}))")
                    is_dup = True
                    removed_count += 1
                    break

            if not is_dup:
                unique.append(c)

        if removed_count > 0:
            print(f"  Cross-type: removed {removed_count} duplicates")
        else:
            print(f"  Cross-type: no duplicates found")

        print(f"  Total unique: {len(unique)}")
        return unique

    def _is_same_type_dup(self, a: dict, b: dict) -> bool:
        """Same-type duplicate check"""
        if a.get("type") != b.get("type"):
            return False

        title_a = a.get("title", "").lower().strip()
        title_b = b.get("title", "").lower().strip()

        similarity = difflib.SequenceMatcher(None, title_a, title_b).ratio()
        return similarity > self.threshold

    def _is_cross_type_dup(self, a: dict, b: dict) -> bool:
        """Cross-type duplicate check: file overlap + description similarity"""
        # Same-type goes through same_type logic
        if a.get("type") == b.get("type"):
            return False

        # involved overlap ratio
        files_a = set(extract_contract_paths(a, "involved_files"))
        files_b = set(extract_contract_paths(b, "involved_files"))

        if not files_a or not files_b:
            return False

        intersection = files_a & files_b
        smaller = min(len(files_a), len(files_b))
        overlap = len(intersection) / smaller if smaller > 0 else 0

        if overlap < self.cross_type_file_overlap:
            return False

        # description similarity
        desc_a = a.get("description", "").strip()
        desc_b = b.get("description", "").strip()

        desc_sim = difflib.SequenceMatcher(None, desc_a, desc_b).ratio()
        return desc_sim > self.cross_type_desc_threshold

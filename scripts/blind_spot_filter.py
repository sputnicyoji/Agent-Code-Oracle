"""
Blind Spot Filter — Stage 3

Heuristic filter based on RED Phase observations:
- R1: Thread-safety disguise — rationale contract actually describes IJob/concurrency choice -> DEMOTE
- R2: Single-directory inference — involved_files all in same directory -> WARN tag
- R3: thread_safety global demotion — domain where AI inference is strongest -> DEMOTE

v2.1 fix:
  - R1 keywords include CJK patterns: contract description/blind_spot may be in Chinese,
    English keywords cannot match terms like parallel/race-condition in Chinese
  - CJK keywords do not use \b word boundary (ineffective for CJK)
"""

import re


# R1: Thread-safety keywords (trigger when >= THREAD_SAFETY_MIN_HITS match)
# Bilingual design: title is English (use \b boundary), description/blind_spot may be CJK (no boundary)
THREAD_SAFETY_KEYWORDS = [
    # English keywords (word boundary)
    r"\bIJob\b",
    r"\bIJobParallelFor\b",
    r"\bIJobFor\b",
    r"\bNativeList\b",
    r"\bNativeHashMap\b",
    r"\bNativeReference\b",
    r"\bNativeArray\b",
    r"\bparallel\b",
    r"\bconcurrent\b",
    r"\bthread\b",
    r"\brace\s*condition\b",
    r"\batomic\b",
    # CJK keywords (no word boundary — \b doesn't work for CJK)
    r"\u5e76\u884c",        # matches parallel/parallelize/parallel-processing
    r"\u7ade\u6001",        # matches race condition
    r"\u7ebf\u7a0b\u5b89\u5168",    # matches thread-safety constraint
]

THREAD_SAFETY_MIN_HITS = 3
DEMOTE_CONFIDENCE = 0.4


class BlindSpotFilter:
    """Blind spot filter — detects contracts AI can self-infer"""

    def __init__(self, demote_confidence: float = DEMOTE_CONFIDENCE,
                 thread_safety_min_hits: int = THREAD_SAFETY_MIN_HITS):
        self.demote_confidence = demote_confidence
        self.thread_safety_min_hits = thread_safety_min_hits

    def process(self, contracts: list[dict]) -> list[dict]:
        """
        Filter contract list

        Args:
            contracts: Deduplicated contract list

        Returns:
            Filtered contract list (demoted contracts have confidence lowered)
        """
        result = []
        demoted = 0
        warned = 0

        for c in contracts:
            contract = dict(c)  # shallow copy

            # R1: Thread-safety disguise
            if self._check_r1_thread_safety_disguise(contract):
                old_conf = contract.get("confidence", 0)
                contract["confidence"] = self.demote_confidence
                contract["_filter_tag"] = "R1:thread_safety_disguise"
                print(f"  [DEMOTE] R1: '{contract['title'][:50]}' "
                      f"({old_conf} -> {self.demote_confidence})")
                demoted += 1

            # R2: Single-directory inference (warn only, no demotion)
            elif self._check_r2_single_dir(contract):
                contract["_filter_tag"] = "R2:single_dir_warn"
                print(f"  [WARN]   R2: '{contract['title'][:50]}' "
                      f"(all files in same directory)")
                warned += 1

            # R3: thread_safety global demotion
            elif self._check_r3_thread_safety_type(contract):
                old_conf = contract.get("confidence", 0)
                new_conf = max(0.5, old_conf - 0.2)
                contract["confidence"] = new_conf
                contract["_filter_tag"] = "R3:thread_safety_type"
                print(f"  [DEMOTE] R3: '{contract['title'][:50]}' "
                      f"({old_conf} -> {new_conf})")
                demoted += 1

            result.append(contract)

        print(f"  Demoted: {demoted}, Warned: {warned}, "
              f"Effective (conf > 0.5): {sum(1 for c in result if c.get('confidence', 0) > 0.5)}/{len(result)}")

        return result

    def _check_r1_thread_safety_disguise(self, contract: dict) -> bool:
        """R1: rationale contract actually describes thread-safety choices"""
        if contract.get("type") != "rationale":
            return False

        # Combine title + description + blind_spot as search text
        text = " ".join([
            contract.get("title", ""),
            contract.get("description", ""),
            contract.get("blind_spot", ""),
        ])

        hits = sum(1 for kw in THREAD_SAFETY_KEYWORDS if re.search(kw, text, re.IGNORECASE))
        return hits >= self.thread_safety_min_hits

    def _check_r2_single_dir(self, contract: dict) -> bool:
        """R2: involved_files all in same directory (rationale only)"""
        if contract.get("type") != "rationale":
            return False

        files = contract.get("involved_files", [])
        if len(files) <= 1:
            return False

        # Check if filenames suggest same directory (heuristic when no path info available)
        # Since involved_files only stores filenames without paths, use filename-prefix heuristic
        # e.g.: ActorSyncSystem.cs, ActorInitSystem.cs -> possibly same directory
        # but: ActorSyncSystem.cs, SomeOtherActor.cs -> different directories
        # Simplified rule: if all filenames share same prefix (stripping System/Job/Data suffix) -> possibly same dir
        # This heuristic is weak; only used as WARN not DROP
        return False  # Disabled until path info is available

    def _check_r3_thread_safety_type(self, contract: dict) -> bool:
        """R3: thread_safety type global demotion"""
        return contract.get("type") == "thread_safety"

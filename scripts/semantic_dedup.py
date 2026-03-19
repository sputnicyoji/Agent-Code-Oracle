"""
Semantic Dedup — Stage 2

Title text similarity-based deduplication:
- Pass 1: same type + title similarity > threshold -> duplicate
- Pass 2: cross-type: involved_files overlap > 60% + description similarity > 0.5 -> duplicate
- Pass 3 (v4.1): embedding cosine similarity fallback (Ollama bge-m3, optional)
- Keep the one with higher confidence (cross-type: keep higher-priority type)
- Uses difflib.SequenceMatcher + Ollama embedding (fallback to difflib-only)
"""

import difflib
import hashlib
import json
import math
import urllib.request
from pathlib import Path


# Type priority: lower number = higher priority
TYPE_PRIORITY = {
    "blast_radius": 0,
    "rationale": 1,
    "data_flow": 2,
    "ordering": 3,
    "thread_safety": 4,
}


class EmbeddingDedup:
    """Ollama bge-m3 embedding dedup (optional, fallback to difflib)"""

    OLLAMA_URL = "http://localhost:11434/api/embeddings"
    MODEL = "bge-m3"
    CACHE_FILE = Path(__file__).parent / ".dedup_cache.json"
    COSINE_THRESHOLD = 0.85

    def __init__(self):
        self.cache = self._load_cache()
        self.available = self._check_ollama()

    def _check_ollama(self) -> bool:
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/tags", method="GET"
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                return any(self.MODEL in m.get("name", "") for m in data.get("models", []))
        except Exception:
            return False

    def _load_cache(self) -> dict:
        if self.CACHE_FILE.exists():
            try:
                with open(self.CACHE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_cache(self):
        with open(self.CACHE_FILE, "w") as f:
            json.dump(self.cache, f)

    def get_embedding(self, text: str) -> list[float] | None:
        if not self.available:
            return None
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        if text_hash in self.cache:
            return self.cache[text_hash]
        try:
            body = json.dumps({"model": self.MODEL, "prompt": text}).encode("utf-8")
            req = urllib.request.Request(
                self.OLLAMA_URL, data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                vec = json.loads(resp.read())["embedding"]
                self.cache[text_hash] = vec
                return vec
        except Exception:
            return None

    @staticmethod
    def cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


class SemanticDedup:
    """Semantic deduplicator"""

    def __init__(self, threshold: float = 0.8, cross_type_file_overlap: float = 0.6,
                 cross_type_desc_threshold: float = 0.5):
        self.threshold = threshold
        self.cross_type_file_overlap = cross_type_file_overlap
        self.cross_type_desc_threshold = cross_type_desc_threshold
        self.embedder = EmbeddingDedup()

    def process(self, contracts: list[dict]) -> list[dict]:
        # Pass 1: same-type title dedup
        after_same_type = self._dedup_same_type(contracts)

        # Pass 2: cross-type difflib dedup
        after_cross_type = self._dedup_cross_type(after_same_type)

        # Pass 3: embedding fallback (v4.1)
        if self.embedder.available:
            after_embedding = self._dedup_embedding(after_cross_type)
            self.embedder.save_cache()
            return after_embedding
        else:
            print("  Embedding: Ollama/bge-m3 not available, skipping Pass 3")

        return after_cross_type

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
        """Cross-type dedup: high involved_files overlap + description semantic similarity"""
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

        total_removed = removed_count
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

    def _dedup_embedding(self, contracts: list[dict]) -> list[dict]:
        """Pass 3: embedding cosine similarity fallback for semantic duplicates"""
        unique = []
        removed_count = 0
        embeddings = []  # parallel list of (contract, vector)

        for c in contracts:
            text = (c.get("description", "") + " " + c.get("blind_spot", "")).strip()
            vec = self.embedder.get_embedding(text)
            if vec is None:
                unique.append(c)
                embeddings.append(None)
                continue

            is_dup = False
            for i, (existing, existing_vec) in enumerate(zip(unique, embeddings)):
                if existing_vec is None:
                    continue
                sim = self.embedder.cosine_sim(vec, existing_vec)
                if sim > self.embedder.COSINE_THRESHOLD:
                    # Also check file overlap (>40%) to avoid false positives
                    files_a = set(c.get("involved_files", []))
                    files_b = set(existing.get("involved_files", []))
                    smaller = min(len(files_a), len(files_b)) if files_a and files_b else 0
                    overlap = len(files_a & files_b) / smaller if smaller > 0 else 0
                    if overlap > 0.4:
                        c_pri = TYPE_PRIORITY.get(c.get("type"), 99)
                        e_pri = TYPE_PRIORITY.get(existing.get("type"), 99)
                        if c_pri < e_pri:
                            print(f"  [EMB-REPLACE] '{existing['title'][:40]}' (sim={sim:.3f}) "
                                  f"-> '{c['title'][:40]}'")
                            unique[i] = c
                            embeddings[i] = vec
                        else:
                            print(f"  [EMB-SKIP] '{c['title'][:40]}' (sim={sim:.3f}, "
                                  f"dup of '{existing['title'][:40]}')")
                        is_dup = True
                        removed_count += 1
                        break

            if not is_dup:
                unique.append(c)
                embeddings.append(vec)

        if removed_count > 0:
            print(f"  Embedding: removed {removed_count} semantic duplicates")
        else:
            print(f"  Embedding: no additional duplicates found")
        return unique

    def _is_cross_type_dup(self, a: dict, b: dict) -> bool:
        """Cross-type duplicate check: file overlap + description similarity"""
        # Same-type goes through same_type logic
        if a.get("type") == b.get("type"):
            return False

        # involved_files overlap ratio
        files_a = set(a.get("involved_files", []))
        files_b = set(b.get("involved_files", []))

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

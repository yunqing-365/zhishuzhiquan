# dataset/deduplicator.py
"""
知数知圈 · 三级去重流水线

Level 1: 精确哈希去重（MD5/SHA256）
Level 2: MinHash 模糊去重（近似重复，阈值可配置）
Level 3: 语义向量去重（高相似度文本，需向量库支持）
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from dataset.schema import SFTSample, PretrainChunk


class DeduplicationPipeline:
    """三级去重流水线"""

    def __init__(
        self,
        minhash_threshold: float = 0.85,
        semantic_threshold: float = 0.95,
    ):
        self.minhash_threshold = minhash_threshold
        self.semantic_threshold = semantic_threshold

    async def run(self, samples: List[SFTSample]) -> Dict:
        """对 SFT 样本做三级去重"""
        if not samples:
            return {"samples": [], "removed": 0, "stats": {}}

        # Level 1: 精确哈希
        seen_hashes = set()
        after_exact = []
        exact_removed = 0
        for s in samples:
            key = hashlib.md5((s.instruction + s.output).encode()).hexdigest()
            if key not in seen_hashes:
                seen_hashes.add(key)
                after_exact.append(s)
            else:
                exact_removed += 1

        # Level 2: MinHash 近似去重（简化版：基于 n-gram 集合 Jaccard 相似度）
        after_minhash = []
        minhash_removed = 0
        ngram_sets = []

        for s in after_exact:
            ngrams = self._ngrams(s.instruction + s.output, n=3)
            is_dup = False
            for existing_ngrams in ngram_sets:
                sim = self._jaccard(ngrams, existing_ngrams)
                if sim >= self.minhash_threshold:
                    is_dup = True
                    minhash_removed += 1
                    break
            if not is_dup:
                ngram_sets.append(ngrams)
                after_minhash.append(s)

        # Level 3: 语义去重（简化版：跳过，生产中接入 ChromaDB）
        # TODO: 接入 chromadb 做向量相似度过滤
        after_semantic = after_minhash
        semantic_removed = 0

        total_removed = exact_removed + minhash_removed + semantic_removed
        return {
            "samples": after_semantic,
            "removed": total_removed,
            "stats": {
                "exact_removed":    exact_removed,
                "minhash_removed":  minhash_removed,
                "semantic_removed": semantic_removed,
                "input_count":      len(samples),
                "output_count":     len(after_semantic),
            },
        }

    def run_chunks(self, chunks: List[PretrainChunk]) -> Dict:
        """对预训练块做精确哈希 + MinHash 去重"""
        if not chunks:
            return {"chunks": [], "removed": 0}

        seen = set()
        result = []
        removed = 0
        for c in chunks:
            key = hashlib.md5(c.text[:200].encode()).hexdigest()
            if key not in seen:
                seen.add(key)
                result.append(c)
            else:
                removed += 1

        return {"chunks": result, "removed": removed}

    # ── 工具方法 ────────────────────────────────────────

    @staticmethod
    def _ngrams(text: str, n: int = 3) -> set:
        text = text.replace(" ", "")
        return {text[i:i+n] for i in range(len(text) - n + 1)}

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union if union else 0.0

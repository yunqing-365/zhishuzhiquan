# dataset/deduplicator.py
"""
息壤 · 语义去重引擎

三级去重策略（速度由快到慢，精度由低到高）：
  Level 1 — 精确哈希去重：content_hash 完全相同 → 直接丢弃（O(1)）
  Level 2 — MinHash 模糊去重：Jaccard 相似度 > 0.85 → 近似重复（O(n)）
  Level 3 — 向量语义去重：余弦相似度 > 0.95 → 语义重复（O(n²) → 近似LSH）

业务逻辑：
  - 同一创作者内部去重（保护创作者利益）
  - 跨创作者去重（先上传者优先，避免抄袭）
  - 每次打包数据集时，对包内样本做全量去重
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from typing import Dict, List, Set, Tuple

import numpy as np

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataset.schema import PretrainChunk, SFTSample


# ════════════════════════════════════════════════════════
# Level 1：精确哈希去重
# ════════════════════════════════════════════════════════

class ExactDeduplicator:
    """
    基于 SHA-256 内容指纹的精确去重
    速度：极快（O(n)）
    适用：完全相同的内容副本
    """

    def __init__(self):
        self._seen: Set[str] = set()
        self._duplicates: List[str] = []   # 重复的 sample_id

    def deduplicate(self, samples: List[SFTSample]) -> List[SFTSample]:
        unique = []
        for s in samples:
            h = s.content_hash or s.compute_hash()
            if h in self._seen:
                self._duplicates.append(s.sample_id)
            else:
                self._seen.add(h)
                unique.append(s)
        return unique

    def deduplicate_chunks(self, chunks: List[PretrainChunk]) -> List[PretrainChunk]:
        unique = []
        for c in chunks:
            h = hashlib.sha256(c.text.encode()).hexdigest()
            c.dedup_hash = h
            if h in self._seen:
                self._duplicates.append(c.chunk_id)
            else:
                self._seen.add(h)
                unique.append(c)
        return unique

    @property
    def stats(self):
        return {
            "seen_total": len(self._seen),
            "duplicates_removed": len(self._duplicates),
        }


# ════════════════════════════════════════════════════════
# Level 2：MinHash 模糊去重
# ════════════════════════════════════════════════════════

class MinHashDeduplicator:
    """
    基于 MinHash + LSH 的近似去重
    速度：快（O(n)）
    适用：改写但内容相同的重复，如换了几个词的重复样本
    Jaccard 阈值 0.85 → 词袋重叠 85%+ 视为重复
    """

    def __init__(self, num_perm: int = 128, threshold: float = 0.85):
        self.num_perm = num_perm
        self.threshold = threshold
        self._signatures: Dict[str, np.ndarray] = {}  # sample_id → minhash sig

    def _tokenize(self, text: str) -> Set[str]:
        """简单字符 n-gram 分词（3-gram），不依赖 jieba"""
        n = 3
        tokens = set()
        for i in range(len(text) - n + 1):
            tokens.add(text[i:i+n])
        return tokens

    def _minhash(self, tokens: Set[str]) -> np.ndarray:
        """生成 MinHash 签名"""
        # 使用多个哈希函数模拟（线性 hash：(a*x + b) mod prime）
        prime = (1 << 31) - 1
        rng = np.random.RandomState(42)
        a = rng.randint(1, prime, self.num_perm)
        b = rng.randint(0, prime, self.num_perm)

        sig = np.full(self.num_perm, np.iinfo(np.int64).max, dtype=np.int64)
        for token in tokens:
            h = int(hashlib.md5(token.encode()).hexdigest(), 16) % prime
            hashes = (a * h + b) % prime
            sig = np.minimum(sig, hashes)
        return sig

    def _jaccard(self, sig_a: np.ndarray, sig_b: np.ndarray) -> float:
        return float(np.mean(sig_a == sig_b))

    def deduplicate(self, samples: List[SFTSample]) -> Tuple[List[SFTSample], List[str]]:
        """返回 (去重后样本列表, 被去除的 sample_id 列表)"""
        kept_ids: List[str] = []
        kept_sigs: List[np.ndarray] = []
        removed: List[str] = []

        for s in samples:
            text = f"{s.instruction} {s.input_context} {s.output}"
            tokens = self._tokenize(text)
            sig = self._minhash(tokens)

            is_dup = False
            for prev_sig in kept_sigs:
                if self._jaccard(sig, prev_sig) >= self.threshold:
                    is_dup = True
                    break

            if is_dup:
                removed.append(s.sample_id)
            else:
                kept_ids.append(s.sample_id)
                kept_sigs.append(sig)
                self._signatures[s.sample_id] = sig

        kept_set = set(kept_ids)
        return [s for s in samples if s.sample_id in kept_set], removed

    def deduplicate_chunks(self, chunks: List[PretrainChunk]) -> Tuple[List[PretrainChunk], List[str]]:
        kept: List[PretrainChunk] = []
        kept_sigs: List[np.ndarray] = []
        removed: List[str] = []

        for c in chunks:
            tokens = self._tokenize(c.text)
            sig = self._minhash(tokens)

            is_dup = any(
                self._jaccard(sig, ps) >= self.threshold
                for ps in kept_sigs
            )
            if is_dup:
                removed.append(c.chunk_id)
            else:
                kept.append(c)
                kept_sigs.append(sig)

        return kept, removed


# ════════════════════════════════════════════════════════
# Level 3：向量语义去重
# ════════════════════════════════════════════════════════

class SemanticDeduplicator:
    """
    基于嵌入向量余弦相似度的语义去重
    速度：慢（需要 embedding API 调用）
    适用：语义相同但表述完全不同的重复（最精准）
    建议仅对 QualityTier.GOLD 以上的样本执行
    """

    def __init__(self, similarity_threshold: float = 0.95):
        self.threshold = similarity_threshold
        self._embeddings: List[np.ndarray] = []
        self._ids: List[str] = []

    async def get_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """批量获取文本 embedding"""
        from openai import AsyncOpenAI
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from config import get_settings
        _s = get_settings()
        _c = AsyncOpenAI(api_key=_s.API_KEY, base_url=_s.BASE_URL)

        # 批量调用（每批 100 条）
        all_embeddings = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            try:
                resp = await _c.embeddings.create(
                    model="text-embedding-3-small",
                    input=batch,
                )
                for item in resp.data:
                    all_embeddings.append(np.array(item.embedding))
            except Exception as e:
                print(f"⚠️  Embedding 调用失败（批次 {i}）: {e}")
                # 失败时用零向量占位，后续不会被判重
                all_embeddings.extend([np.zeros(1536)] * len(batch))

        return all_embeddings

    async def deduplicate(
        self,
        samples: List[SFTSample],
    ) -> Tuple[List[SFTSample], List[str]]:
        if not samples:
            return [], []

        texts = [f"{s.instruction} {s.output}" for s in samples]
        embeddings = await self.get_embeddings(texts)

        kept_indices: List[int] = []
        kept_embeddings: List[np.ndarray] = []
        removed: List[str] = []

        for i, (s, emb) in enumerate(zip(samples, embeddings)):
            # 余弦相似度 = dot(a,b) / (||a|| * ||b||)
            is_dup = False
            for prev_emb in kept_embeddings:
                norm_a = np.linalg.norm(emb)
                norm_b = np.linalg.norm(prev_emb)
                if norm_a == 0 or norm_b == 0:
                    continue
                cos_sim = np.dot(emb, prev_emb) / (norm_a * norm_b)
                if cos_sim >= self.threshold:
                    is_dup = True
                    break

            if is_dup:
                removed.append(s.sample_id)
            else:
                kept_indices.append(i)
                kept_embeddings.append(emb)

        return [samples[i] for i in kept_indices], removed


# ════════════════════════════════════════════════════════
# 组合去重流水线
# ════════════════════════════════════════════════════════

class DeduplicationPipeline:
    """
    三级去重流水线
    按速度由快到慢执行，减少后续精排的计算量
    """

    def __init__(
        self,
        minhash_threshold: float = 0.85,
        semantic_threshold: float = 0.95,
        semantic_min_tier: str = "gold",    # 只对黄金及以上的样本做语义去重
    ):
        self.exact = ExactDeduplicator()
        self.minhash = MinHashDeduplicator(threshold=minhash_threshold)
        self.semantic = SemanticDeduplicator(similarity_threshold=semantic_threshold)
        self.semantic_min_tier = semantic_min_tier

    async def run(self, samples: List[SFTSample]) -> dict:
        print(f"🔍 开始去重流水线，输入 {len(samples)} 条样本")
        stats = {"input": len(samples)}

        # Level 1: 精确去重
        samples = self.exact.deduplicate(samples)
        stats["after_exact"] = len(samples)
        stats["exact_removed"] = stats["input"] - stats["after_exact"]
        print(f"  Level 1 精确去重: 剩余 {len(samples)} 条（去除 {stats['exact_removed']}）")

        # Level 2: MinHash 去重
        samples, minhash_removed = self.minhash.deduplicate(samples)
        stats["after_minhash"] = len(samples)
        stats["minhash_removed"] = len(minhash_removed)
        print(f"  Level 2 MinHash 去重: 剩余 {len(samples)} 条（去除 {stats['minhash_removed']}）")

        # Level 3: 语义去重（仅对高质量样本）
        from dataset.schema import QualityTier
        tier_order = [t.value for t in QualityTier]
        min_idx = tier_order.index(self.semantic_min_tier) if self.semantic_min_tier in tier_order else 2

        high_quality = [s for s in samples
                        if tier_order.index(s.quality_tier.value) <= min_idx]
        low_quality = [s for s in samples
                       if tier_order.index(s.quality_tier.value) > min_idx]

        if high_quality:
            high_quality, semantic_removed = await self.semantic.deduplicate(high_quality)
            stats["semantic_removed"] = len(semantic_removed)
            print(f"  Level 3 语义去重: 高质量样本剩余 {len(high_quality)} 条（去除 {stats['semantic_removed']}）")
        else:
            stats["semantic_removed"] = 0

        final = high_quality + low_quality
        stats["output"] = len(final)
        stats["total_removed"] = stats["input"] - stats["output"]
        stats["dedup_rate"] = round(stats["total_removed"] / stats["input"], 3) if stats["input"] else 0

        print(f"✅ 去重完成: {stats['input']} → {stats['output']} "
              f"（总去重率 {stats['dedup_rate']:.1%}）")
        return {"samples": final, "stats": stats}

    def run_chunks(self, chunks: List[PretrainChunk]) -> dict:
        """预训练语料去重（同步版，不需要 semantic 层）"""
        chunks = self.exact.deduplicate_chunks(chunks)
        chunks, _ = self.minhash.deduplicate_chunks(chunks)
        return {"chunks": chunks, "output": len(chunks)}

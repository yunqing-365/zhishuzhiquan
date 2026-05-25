# dataset/deduplicator.py  v2
"""
知数知圈 · 三级去重流水线 v2

Level 1: 精确哈希去重（MD5）           — 毫秒级，O(n)
Level 2: MinHash n-gram 模糊去重       — 秒级，O(n²) 近似
Level 3: ChromaDB 语义向量去重 ★新增★  — 填补 TODO，真实语义相似度过滤

ChromaDB 使用本地持久化模式（无需单独部署），集合名 zszq_dedup_sft。
向量化使用 ChromaDB 内置的 all-MiniLM-L6-v2（首次运行自动下载 ~80MB）。
若 chromadb 不可用，自动降级只跑前两层，流水线不中断。
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataset.schema import SFTSample, DPOSample, PretrainChunk

# ── ChromaDB 初始化（可选依赖）───────────────────────────────────
_CHROMA_OK = False
_chroma_client = None
_CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "chroma_db"
)

def _init_chroma():
    global _CHROMA_OK, _chroma_client
    try:
        import chromadb
        os.makedirs(_CHROMA_DIR, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=_CHROMA_DIR)
        _CHROMA_OK = True
        print(f">> [dedup] ChromaDB 初始化成功 (path={_CHROMA_DIR})")
    except Exception as e:
        print(f"⚠️  [dedup] ChromaDB 不可用，语义去重跳过: {e}")
        _CHROMA_OK = False

_init_chroma()


# ════════════════════════════════════════════════════════════════════
# Level 3 — 语义去重核心
# ════════════════════════════════════════════════════════════════════

class SemanticDeduplicator:
    """
    基于 ChromaDB 的语义向量去重器。

    每个 job 使用独立的临时 collection（job_id 为名），
    去重完成后删除，避免跨任务污染。

    向量化模型：ChromaDB 内置 all-MiniLM-L6-v2
    相似度指标：cosine distance（distance < 1 - threshold 视为重复）
    """

    def __init__(self, threshold: float = 0.92, job_id: str = "default"):
        self.threshold = threshold          # 余弦相似度阈值，超过则视为重复
        self.collection_name = f"dedup_{job_id[:16]}"
        self._collection = None

    def _get_collection(self):
        if not _CHROMA_OK or _chroma_client is None:
            return None
        if self._collection is None:
            try:
                # get_or_create 幂等
                self._collection = _chroma_client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                print(f"⚠️  [dedup] 获取 collection 失败: {e}")
        return self._collection

    def cleanup(self):
        """删除临时 collection（job 结束后调用）"""
        if not _CHROMA_OK or _chroma_client is None:
            return
        try:
            _chroma_client.delete_collection(self.collection_name)
        except Exception:
            pass

    def deduplicate(
        self,
        texts: List[str],
        ids: List[str],
        batch_size: int = 50,
    ) -> List[bool]:
        """
        对 texts 列表做语义去重，返回等长布尔列表。
        True = 保留，False = 重复删除。

        算法：
          逐条查询 collection，若最近邻距离 < (1 - threshold) 视为重复；
          否则将该条加入 collection（作为基准）。
        """
        col = self._get_collection()
        if col is None:
            return [True] * len(texts)   # ChromaDB 不可用，全部保留

        keep = []
        added_count = 0

        for i, (text, doc_id) in enumerate(zip(texts, ids)):
            if not text.strip():
                keep.append(True)
                continue

            truncated = text[:512]   # ChromaDB 建议文本不超过 512 词

            try:
                if added_count == 0:
                    # collection 为空，直接加入
                    col.add(documents=[truncated], ids=[doc_id])
                    added_count += 1
                    keep.append(True)
                    continue

                results = col.query(
                    query_texts=[truncated],
                    n_results=1,
                    include=["distances"],
                )
                distances = results.get("distances", [[]])[0]
                if distances and distances[0] < (1.0 - self.threshold):
                    # 距离小 = 相似度高 = 重复
                    keep.append(False)
                else:
                    col.add(documents=[truncated], ids=[doc_id])
                    added_count += 1
                    keep.append(True)

            except Exception as e:
                print(f"⚠️  [dedup] 语义查询失败 (id={doc_id}): {e}")
                keep.append(True)   # 出错保留，不丢数据

        return keep


# ════════════════════════════════════════════════════════════════════
# 主去重流水线（兼容 v1 接口）
# ════════════════════════════════════════════════════════════════════

class DeduplicationPipeline:
    """
    三级去重流水线 v2
    接口与 v1 完全兼容（run / run_chunks 签名不变）
    """

    def __init__(
        self,
        minhash_threshold:  float = 0.85,
        semantic_threshold: float = 0.92,
    ):
        self.minhash_threshold  = minhash_threshold
        self.semantic_threshold = semantic_threshold

    async def run(
        self,
        samples: List[SFTSample],
        job_id:  str = "default",
    ) -> Dict:
        """对 SFT 样本做三级去重。"""
        if not samples:
            return {"samples": [], "removed": 0, "stats": {}}

        # ── Level 1: 精确哈希 ────────────────────────────────────
        seen_hashes: set = set()
        after_exact: List[SFTSample] = []
        exact_removed = 0

        for s in samples:
            key = hashlib.md5(
                (s.instruction + s.output).encode("utf-8", errors="ignore")
            ).hexdigest()
            if key not in seen_hashes:
                seen_hashes.add(key)
                after_exact.append(s)
            else:
                exact_removed += 1

        # ── Level 2: MinHash n-gram ──────────────────────────────
        after_minhash: List[SFTSample] = []
        minhash_removed = 0
        ngram_sets: List[set] = []

        for s in after_exact:
            ngrams = _ngrams(s.instruction + s.output, n=3)
            is_dup = any(
                _jaccard(ngrams, existing) >= self.minhash_threshold
                for existing in ngram_sets
            )
            if is_dup:
                minhash_removed += 1
            else:
                ngram_sets.append(ngrams)
                after_minhash.append(s)

        # ── Level 3: ChromaDB 语义去重 ★ ────────────────────────
        semantic_removed = 0
        after_semantic = after_minhash

        if _CHROMA_OK and after_minhash:
            sem_dedup = SemanticDeduplicator(
                threshold=self.semantic_threshold,
                job_id=job_id,
            )
            texts = [s.instruction + " " + s.output for s in after_minhash]
            ids   = [s.sample_id for s in after_minhash]
            keep  = sem_dedup.deduplicate(texts, ids)

            after_semantic = [s for s, k in zip(after_minhash, keep) if k]
            semantic_removed = sum(1 for k in keep if not k)
            sem_dedup.cleanup()

            print(
                f"  [dedup L3] 语义去重: {len(after_minhash)} → {len(after_semantic)} "
                f"(阈值={self.semantic_threshold}, 去除={semantic_removed})"
            )
        else:
            if not _CHROMA_OK:
                print("  [dedup L3] ChromaDB 不可用，跳过语义去重")

        total_removed = exact_removed + minhash_removed + semantic_removed
        return {
            "samples": after_semantic,
            "removed": total_removed,
            "stats": {
                "input_count":      len(samples),
                "output_count":     len(after_semantic),
                "exact_removed":    exact_removed,
                "minhash_removed":  minhash_removed,
                "semantic_removed": semantic_removed,
                "chroma_enabled":   _CHROMA_OK,
            },
        }

    def run_chunks(self, chunks: List[PretrainChunk], job_id: str = "default") -> Dict:
        """对预训练块做精确哈希 + MinHash 去重（Pretrain 块不做向量层，性能考量）。"""
        if not chunks:
            return {"chunks": [], "removed": 0, "stats": {}}

        seen: set = set()
        after_exact: List[PretrainChunk] = []
        exact_removed = 0

        for c in chunks:
            key = hashlib.md5(c.text[:200].encode("utf-8", errors="ignore")).hexdigest()
            if key not in seen:
                seen.add(key)
                after_exact.append(c)
            else:
                exact_removed += 1

        # MinHash for pretrain chunks
        after_minhash: List[PretrainChunk] = []
        minhash_removed = 0
        ngram_sets: List[set] = []

        for c in after_exact:
            ngrams = _ngrams(c.text, n=4)
            is_dup = any(
                _jaccard(ngrams, existing) >= self.minhash_threshold
                for existing in ngram_sets
            )
            if is_dup:
                minhash_removed += 1
            else:
                ngram_sets.append(ngrams)
                after_minhash.append(c)

        return {
            "chunks":  after_minhash,
            "removed": exact_removed + minhash_removed,
            "stats": {
                "input_count":     len(chunks),
                "output_count":    len(after_minhash),
                "exact_removed":   exact_removed,
                "minhash_removed": minhash_removed,
            },
        }

    async def run_dpo(
        self,
        samples: List[DPOSample],
        job_id:  str = "default",
    ) -> Dict:
        """
        DPO 样本去重：精确哈希 + 语义去重（基于 prompt 相似度）。
        v1 只做了精确哈希，v2 补上语义层。
        """
        if not samples:
            return {"samples": [], "removed": 0, "stats": {}}

        seen: set = set()
        after_exact: List[DPOSample] = []
        exact_removed = 0

        for s in samples:
            key = hashlib.md5(
                (s.prompt + s.chosen).encode("utf-8", errors="ignore")
            ).hexdigest()
            if key not in seen:
                seen.add(key)
                after_exact.append(s)
            else:
                exact_removed += 1

        # 语义去重（仅对 prompt 向量化，chosen 差异交给质检器判断）
        semantic_removed = 0
        after_semantic = after_exact

        if _CHROMA_OK and after_exact:
            sem_dedup = SemanticDeduplicator(
                threshold=self.semantic_threshold,
                job_id=f"{job_id}_dpo",
            )
            texts = [s.prompt for s in after_exact]
            ids   = [s.sample_id for s in after_exact]
            keep  = sem_dedup.deduplicate(texts, ids)
            after_semantic = [s for s, k in zip(after_exact, keep) if k]
            semantic_removed = sum(1 for k in keep if not k)
            sem_dedup.cleanup()

        return {
            "samples": after_semantic,
            "removed": exact_removed + semantic_removed,
            "stats": {
                "input_count":      len(samples),
                "output_count":     len(after_semantic),
                "exact_removed":    exact_removed,
                "semantic_removed": semantic_removed,
            },
        }


# ════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════

def _ngrams(text: str, n: int = 3) -> set:
    text = text.replace(" ", "")[:2000]   # 截断超长文本
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

"""
collision_detector.py — 相似资产碰撞检测模块
================================================
基于 ChromaDB ANN（近似最近邻）检索，判断待注册资产是否与已有资产高度相似，
从而提前发现版权碰撞风险，阻止重复资产上链。

核心逻辑：
  1. 用同一套 SentenceTransformer embedding 对输入资产向量化
  2. 在 ChromaDB 中做 top-K cosine 相似检索（距离越小 = 越相似）
  3. 按阈值分三档：SAFE / WARNING / COLLISION
  4. 每个碰撞结果附带 similarity_score、modality、scene、asset_hash 摘要

阈值（ChromaDB cosine distance ∈ [0, 2]，值越小越相似）：
  distance < 0.15  → COLLISION（高度重复，建议拒绝上链）
  0.15 ≤ dist < 0.40 → WARNING （内容相似，需人工审核）
  distance ≥ 0.40  → SAFE    （内容独特，可以上链）

被 oracle_engine.py 的 POST /api/detect_collision 端点调用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# ── 阈值常量 ─────────────────────────────────────────────────────────
COLLISION_THRESHOLD = 0.15   # cosine distance < 此值 → 判定碰撞
WARNING_THRESHOLD   = 0.40   # cosine distance < 此值 → 警告
TOP_K               = 8      # 每次查询返回最多 K 个候选


# ─────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────

@dataclass
class SimilarAsset:
    """单条相似资产检索结果"""
    asset_hash:       str
    distance:         float          # ChromaDB cosine distance ∈ [0, 2]
    similarity_score: float          # 1 - distance/2，∈ [0, 1]，越大越相似
    risk_level:       str            # "COLLISION" | "WARNING" | "SAFE"
    modality:         str
    scene:            str
    audio_scene:      Optional[str]  # 音频/视频细粒度场景（可为空）


@dataclass
class CollisionReport:
    """完整碰撞检测报告，作为 API 响应体直接序列化"""
    verdict:          str            # "COLLISION" | "WARNING" | "SAFE" | "EMPTY_CORPUS"
    risk_score:       float          # 最高相似度（0~1），0 = 无碰撞
    top_matches:      list[SimilarAsset] = field(default_factory=list)
    total_checked:    int   = 0      # 本次检索范围（ChromaDB 库大小）
    collision_count:  int   = 0      # distance < COLLISION_THRESHOLD 的数量
    warning_count:    int   = 0      # WARNING 档的数量
    message:          str   = ""     # 人类可读结论
    latency_ms:       float = 0.0    # 检索耗时（毫秒）

    def to_dict(self) -> dict:
        return {
            "verdict":         self.verdict,
            "risk_score":      round(self.risk_score, 4),
            "collision_count": self.collision_count,
            "warning_count":   self.warning_count,
            "total_checked":   self.total_checked,
            "message":         self.message,
            "latency_ms":      round(self.latency_ms, 1),
            "top_matches": [
                {
                    "asset_hash":       m.asset_hash,
                    "distance":         round(m.distance, 4),
                    "similarity_score": round(m.similarity_score, 4),
                    "risk_level":       m.risk_level,
                    "modality":         m.modality,
                    "scene":            m.scene,
                    "audio_scene":      m.audio_scene,
                }
                for m in self.top_matches
            ],
        }


# ─────────────────────────────────────────────────────────────────────
# 核心检测函数
# ─────────────────────────────────────────────────────────────────────

def detect_collision(
    query_embedding: list[float],
    collection,                       # chromadb.Collection 实例（由 oracle_engine 传入）
    exclude_hash: Optional[str] = None,  # 排除自身（资产刚估值完后立即检测用）
    top_k: int = TOP_K,
) -> CollisionReport:
    """
    对已向量化的资产执行 ANN 碰撞检测。

    参数：
      query_embedding  已经过 SentenceTransformer 编码的浮点向量
      collection       chromadb.Collection，来自 oracle_engine._collection
      exclude_hash     若非空，过滤掉与自身哈希完全相同的结果（避免自我碰撞）
      top_k            返回最近邻数量

    返回：
      CollisionReport  含结论、风险分、Top-K 相似资产列表
    """
    t0 = time.monotonic()

    corpus_size = collection.count()
    if corpus_size == 0:
        return CollisionReport(
            verdict       = "EMPTY_CORPUS",
            risk_score    = 0.0,
            total_checked = 0,
            message       = "向量库为空，首个资产直接上链",
            latency_ms    = (time.monotonic() - t0) * 1000,
        )

    # ChromaDB query
    n_results = min(top_k + 1, corpus_size)   # +1 为了应对 exclude_hash 后仍有 top_k 结果
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results       = n_results,
            include         = ["distances", "metadatas", "documents"],
        )
    except Exception as e:
        return CollisionReport(
            verdict   = "SAFE",
            risk_score = 0.0,
            message   = f"ChromaDB 查询失败（降级为 SAFE）: {e}",
            latency_ms = (time.monotonic() - t0) * 1000,
        )

    ids       = results["ids"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    # 构建 SimilarAsset 列表
    matches: list[SimilarAsset] = []
    for asset_id, dist, meta in zip(ids, distances, metadatas):
        if exclude_hash and asset_id == exclude_hash:
            continue

        sim   = max(0.0, 1.0 - dist / 2.0)   # cosine dist ∈ [0,2] → similarity ∈ [0,1]
        level = (
            "COLLISION" if dist < COLLISION_THRESHOLD else
            "WARNING"   if dist < WARNING_THRESHOLD   else
            "SAFE"
        )
        matches.append(SimilarAsset(
            asset_hash       = asset_id,
            distance         = dist,
            similarity_score = sim,
            risk_level       = level,
            modality         = meta.get("modality",    "unknown"),
            scene            = meta.get("scene",       "unknown"),
            audio_scene      = meta.get("audio_scene") or None,
        ))

    # 截取前 top_k
    matches = matches[:top_k]

    # 聚合统计
    collision_count = sum(1 for m in matches if m.risk_level == "COLLISION")
    warning_count   = sum(1 for m in matches if m.risk_level == "WARNING")
    risk_score      = matches[0].similarity_score if matches else 0.0

    # 最终判决：只要有任何 COLLISION 就判定碰撞
    if collision_count > 0:
        verdict = "COLLISION"
        message = (
            f"检测到 {collision_count} 个高度相似资产（相似度 ≥ "
            f"{round((1 - COLLISION_THRESHOLD/2)*100)}%），建议拒绝上链或补充差异证明"
        )
    elif warning_count > 0:
        verdict = "WARNING"
        message = (
            f"检测到 {warning_count} 个相似资产，建议人工审核后决定是否上链"
        )
    else:
        verdict = "SAFE"
        message = "未检测到相似资产，内容独特，可安全上链"

    latency = (time.monotonic() - t0) * 1000

    return CollisionReport(
        verdict         = verdict,
        risk_score      = risk_score,
        top_matches     = matches,
        total_checked   = corpus_size,
        collision_count = collision_count,
        warning_count   = warning_count,
        message         = message,
        latency_ms      = latency,
    )


# ─────────────────────────────────────────────────────────────────────
# 便捷函数：直接用文本描述检测（不需要预计算 embedding）
# ─────────────────────────────────────────────────────────────────────

def detect_collision_from_text(
    description:  str,
    embed_fn,                          # SentenceTransformerEmbeddingFunction
    collection,
    exclude_hash: Optional[str] = None,
    top_k:        int = TOP_K,
) -> CollisionReport:
    """
    从原始文本描述直接检测碰撞（embedding 内部计算）。
    用于不需要完整估值流程、只想快速查重的场景。
    """
    try:
        embedding = embed_fn([description])[0]
    except Exception as e:
        return CollisionReport(
            verdict   = "SAFE",
            risk_score = 0.0,
            message   = f"Embedding 计算失败（降级为 SAFE）: {e}",
        )

    return detect_collision(
        query_embedding = embedding,
        collection      = collection,
        exclude_hash    = exclude_hash,
        top_k           = top_k,
    )

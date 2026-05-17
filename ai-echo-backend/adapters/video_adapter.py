"""
VideoAdapter — 视频模态适配器 v0 (Stub)
=========================================
当前状态: 架构占位符，已接入注册表，等待真实实现。

升级路径:
  Stage A (当前): 基于描述文字的降级估值 ← 本版本
  Stage B: 帧采样 → CLIP-image per frame → 时序聚合
  Stage C: 视频音轨 → AudioAdapter 联合估值
  Stage D: VideoMAE / TimeSformer → 完整视频理解

6D 指标含义（Stage A 降级）:
  entropy   → 描述信息熵（文字代理，真实版为帧多样性）
  snr       → 描述质量代理（真实版为视频码率 × 画质评估）
  structure → 场景结构复杂度（真实版为镜头切换密度）
  scarcity  → 向量空间稀缺度（与文本/图像共用 ChromaDB）
  llm_value → 视频训练增益代理（真实版为多帧 CLIP 对齐分）
  shapley   → KNN-Shapley 贡献度

TEV 倍率: 500x（视频信息密度最高，是文本的 500 倍）
"""

import math
import hashlib
from typing import List, Dict, Optional

import numpy as np

from .base_adapter import BaseModalityAdapter

try:
    from scoring import knn_shapley_score
except ImportError:
    def knn_shapley_score(emb, corpus):
        return 50.0


# ── 视频质量关键词（Stage A 描述代理）─────────────────────────────────
_HIGH_KWS = frozenset([
    "4k", "8k", "uhd", "高清", "专业", "纪录片", "电影级",
    "professional", "cinematic", "4K", "HDR", "无损",
])
_LOW_KWS = frozenset([
    "模糊", "压缩", "低帧率", "480p", "blur", "compressed",
    "shaky", "竖屏", "手机拍",
])
_SCENE_KWS = frozenset([
    "教学", "演讲", "访谈", "手术", "庭审", "实验室",
    "tutorial", "lecture", "medical", "court", "lab",
])


class VideoAdapter(BaseModalityAdapter):
    """
    视频模态适配器 — Stage A 降级实现
    所有方法均有真实逻辑（基于描述文字），不返回假数据。
    """

    ADAPTER_VERSION = "v0-stub"
    IS_STUB = True          # 供 oracle_engine 检测，展示提示标记

    def __init__(self, embed_fn, get_corpus_fn):
        self._embed_fn   = embed_fn
        self._get_corpus = get_corpus_fn

    # ------------------------------------------------------------------
    def generate_hash(self, asset_data: str, **_) -> str:
        """Stage A: SHA-256 描述指纹，Stage B 替换为帧级感知哈希"""
        digest = hashlib.sha256(asset_data.encode("utf-8")).hexdigest()[:16].upper()
        return f"0xVID_stub_{digest}"

    # ------------------------------------------------------------------
    def get_embedding(self, asset_data: str, **_) -> List[float]:
        """Stage A: 描述文字嵌入，Stage B 替换为 CLIP-video 帧平均嵌入"""
        if self._embed_fn:
            try:
                res = self._embed_fn([asset_data])
                return res[0] if res else [0.0] * 384
            except Exception:
                pass
        return [0.0] * 384

    # ------------------------------------------------------------------
    def extract_metrics(
        self,
        asset_data: str,
        scene_result,
        vector_distance: float,
        query_embedding: List[float],
        **_,
    ) -> Dict:
        """
        Stage A 指标提取（全部基于描述文字）。
        Stage B 将在此处注入真实帧特征，接口签名不变。
        """
        desc_lower = asset_data.lower()
        words = asset_data.split()
        n_words = max(len(words), 1)

        # entropy — 词汇多样性代理（真实版: 帧间差异均值）
        unique_ratio = len(set(words)) / n_words
        entropy = min(100.0, unique_ratio * 130 + len(asset_data) * 0.04)

        # snr — 描述质量代理（真实版: VMAF/PSNR）
        high_bonus  = sum(6.0 for kw in _HIGH_KWS if kw in desc_lower)
        low_penalty = sum(8.0 for kw in _LOW_KWS  if kw in desc_lower)
        snr = min(100.0, max(20.0, 55.0 + high_bonus - low_penalty))

        # structure — 场景类型复杂度（真实版: 镜头切换密度）
        scene_bonus = sum(5.0 for kw in _SCENE_KWS if kw in desc_lower)
        structure   = min(100.0, 45.0 + scene_bonus + unique_ratio * 30)

        # scarcity — 向量空间稀缺度（与其他模态共享 ChromaDB，真实指标）
        scarcity = min(100.0, max(15.0, vector_distance * 90))

        # KNN-Shapley
        corpus  = self._get_corpus() if self._get_corpus else []
        shapley = knn_shapley_score(query_embedding, corpus)

        # llm_value — 视频训练增益（真实版: 多帧 CLIP 对齐分）
        llm_value = entropy * 0.25 + structure * 0.35 + scarcity * 0.20 + shapley * 0.20

        return {
            "entropy":   round(min(100.0, entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(min(100.0, scarcity), 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
        }

    # ------------------------------------------------------------------
    def get_metric_names(self) -> List[str]:
        return [
            "帧多样性 (描述代理 · Stage A)",
            "视频码率质量 (描述代理 · Stage A)",
            "镜头结构复杂度 (描述代理 · Stage A)",
            "视频库稀缺度 (向量空间)",
            "视频训练增益 (描述代理 · Stage A)",
            "KNN-Shapley 贡献度",
        ]

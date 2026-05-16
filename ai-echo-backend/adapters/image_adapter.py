"""
ImageAdapter — 图像模态适配器 v2
===================================
升级变更 v1→v2:
  - extract_metrics 接受 scene_result，场景自适应打分
  - 真实 PIL 指标: 灰度直方图熵 / FIND_EDGES 边缘复杂度 / 色彩多样性
  - generate_hash: PIL 可用时使用 pHash (DCT 感知哈希)
  - 支持 base64 图像输入 (从 AssetData.image_data 注入)
  - 通过构造器注入 embed_fn / get_corpus_fn，无循环依赖

升级路径 (接口不变):
  Stage A: PIL 代理指标        ← 当前版本
  Stage B: CLIP image encoder  → 替换 get_embedding，512-dim 对齐
  Stage C: LAION-Aesthetics    → 替换 structure 维度打分
"""

import io
import math
import base64
import hashlib
from typing import Optional, List, Dict

import numpy as np
from PIL import Image, ImageFilter

try:
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

from .base_adapter import BaseModalityAdapter
from scoring import knn_shapley_score

# 稀有画风 — illustration 场景额外加分
RARE_STYLES = frozenset([
    "赛博朋克", "蒸汽朋克", "洛可可", "包豪斯", "装饰艺术",
    "cyberpunk", "steampunk", "art nouveau", "brutalism", "bauhaus",
])
QUALITY_KWS = frozenset([
    "精细", "细节", "光影", "构图", "原创", "手绘", "专业",
    "detailed", "masterpiece", "4k", "8k", "intricate", "professional",
])
PENALTY_KWS = frozenset([
    "截图", "随手拍", "普通", "screenshot", "casual", "blur", "meme",
])

# 场景基础美学分 (LAION-Aesthetics 代理)
SCENE_AESTHETIC_BASE = {
    "illustration": 82.0,
    "photo": 62.0,
    "diagram": 50.0,
    "screenshot": 22.0,
    "noise": 12.0,
}


def _decode_b64_image(b64_str: str) -> Optional[Image.Image]:
    """解码 base64 图像 (支持 data-URI 和裸 base64)"""
    try:
        if b64_str.startswith("data:"):
            b64_str = b64_str.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_str)
        return Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None


def _arr_entropy(arr: np.ndarray) -> float:
    total = arr.sum()
    if total == 0:
        return 0.0
    p = arr[arr > 0] / total
    return float(-np.sum(p * np.log2(p)))


def _pil_metrics(img: Image.Image) -> dict:
    """
    基于 PIL 的真实图像统计指标
    CLIP / LAION-Aesthetics 的当前代理；接口不变，后续直接替换实现。

    返回字段:
      hist_entropy    — 灰度直方图熵 [0,100]  → entropy 维度
      edge_complexity — FIND_EDGES 边缘均值   → structure 代理
      color_diversity — RGB 三通道熵均值      → 画风多样性
    """
    # ① 灰度直方图熵 (8-bit 最大熵 = 8.0 bits)
    gray = img.convert("L")
    hist = np.array(gray.histogram(), dtype=float)
    hist_entropy = min(100.0, (_arr_entropy(hist) / 8.0) * 100)

    # ② 边缘复杂度 (FIND_EDGES → 高频细节丰富度)
    edge_arr = np.array(gray.filter(ImageFilter.FIND_EDGES), dtype=float)
    edge_complexity = min(100.0, float(np.mean(edge_arr)) * 1.6)

    # ③ 色彩多样性 (R/G/B 各 32-bin 直方图熵的均值)
    rgb = np.array(img, dtype=np.uint8)
    channel_entropies = [
        _arr_entropy(np.histogram(rgb[:, :, c], bins=32)[0])
        for c in range(3)
    ]
    color_diversity = min(100.0, (sum(channel_entropies) / 3.0 / 5.0) * 100)

    return {
        "hist_entropy": hist_entropy,
        "edge_complexity": edge_complexity,
        "color_diversity": color_diversity,
    }


class ImageAdapter(BaseModalityAdapter):
    """
    图像模态适配器 — 构造器注入依赖
    """

    def __init__(self, embed_fn, get_corpus_fn):
        self._embed_fn = embed_fn
        self._get_corpus = get_corpus_fn

    # ------------------------------------------------------------------
    def generate_hash(self, asset_data: str, image_data: Optional[str] = None) -> str:
        """
        感知哈希 (pHash, DCT-based) — 抗重绘 / 抗缩放
        若无图像像素 → 降级为 SHA-256 描述指纹
        """
        if image_data:
            img = _decode_b64_image(image_data)
            if img and HAS_IMAGEHASH:
                return f"0xpHash_{imagehash.phash(img)}"
            if img:
                # 无 imagehash: 用像素 MD5 近似
                arr = np.array(img.resize((32, 32)).convert("L"))
                return "0xDCT_" + hashlib.md5(arr.tobytes()).hexdigest()[:12].upper()
        return "0xDESC_" + hashlib.sha256(asset_data.encode()).hexdigest()[:12].upper()

    # ------------------------------------------------------------------
    def get_embedding(self, asset_data: str, image_data: Optional[str] = None) -> List[float]:
        """
        图像特征向量
        当前: PIL 颜色直方图 (384-dim，对齐 ChromaDB 维度)
        升级: CLIP image encoder → 512-dim + 跨模态对齐
        """
        if image_data:
            img = _decode_b64_image(image_data)
            if img:
                rgb = np.array(img.resize((64, 64)).convert("RGB"), dtype=float)
                # 颜色直方图展平为 384 维 (3 通道 × 128 bins)
                hist = np.concatenate([
                    np.histogram(rgb[:, :, c], bins=128, range=(0, 256))[0]
                    for c in range(3)
                ]).astype(float)
                norm = np.linalg.norm(hist) + 1e-10
                return (hist / norm).tolist()
        # 降级：用描述文字 embedding
        if self._embed_fn:
            res = self._embed_fn([asset_data])
            return res[0] if res else [0.0] * 384
        return [0.0] * 384

    # ------------------------------------------------------------------
    def extract_metrics(
        self,
        asset_data: str,
        scene_result,
        vector_distance: float,
        query_embedding: List[float],
        image_data: Optional[str] = None,
    ) -> Dict:
        scene = scene_result.scene
        desc_lower = asset_data.lower()

        # ── 尝试真实 PIL 指标 ──
        pil = None
        if image_data:
            pil = _decode_b64_image(image_data)

        if pil:
            m = _pil_metrics(pil)
            clip_entropy  = m["hist_entropy"]
            edge_struct   = m["edge_complexity"]
            color_div     = m["color_diversity"]
        else:
            # 无像素 → 描述文字代理
            words = asset_data.split()
            clip_entropy = min(100.0,
                (len(set(words)) / max(len(words), 1)) * 150 + len(asset_data) / 3
            )
            edge_struct  = 0.0   # 无像素无法计算，后续由 aesthetic 填充
            color_div    = 0.0

        # ── snr: DWT 水印鲁棒性代理 ──
        # 真实版本: 提取 DWT 低频分量，计算水印提取 SNR
        # 当前代理: edge 复杂度 + 描述长度
        if pil:
            dwt_snr = min(100.0, 50.0 + edge_struct * 0.5)
        else:
            dwt_snr = min(100.0, 55.0 + len(asset_data) * 0.18)

        # ── structure: LAION-Aesthetics 美学打分代理 ──
        scene_base = SCENE_AESTHETIC_BASE.get(scene, 50.0)
        bonus   = sum(3.0 for kw in QUALITY_KWS  if kw in desc_lower)
        penalty = sum(6.0 for kw in PENALTY_KWS  if kw in desc_lower)
        if pil:
            # 像素信息提升美学代理
            pixel_bonus = edge_struct * 0.15 + color_div * 0.10
        else:
            pixel_bonus = 0.0
        aesthetic = min(100.0, max(5.0, scene_base + bonus - penalty + pixel_bonus))

        # ── scarcity: 画派风格稀缺度 ──
        style_bonus = sum(8.0 for kw in RARE_STYLES if kw in desc_lower)
        style_mult  = 1.5 if scene == "illustration" else 0.6
        base_scarcity = min(100.0, max(15.0, vector_distance * 85))
        style_scarcity = min(100.0, base_scarcity + style_bonus * style_mult)

        # ── KNN-Shapley ──
        corpus  = self._get_corpus() if self._get_corpus else []
        shapley = knn_shapley_score(query_embedding, corpus)

        # ── llm_value: LoRA 微调增益 ──
        lora_val = aesthetic * 0.50 + style_scarcity * 0.30 + shapley * 0.20

        return {
            "entropy":   round(min(100.0, clip_entropy), 1),
            "snr":       round(min(100.0, dwt_snr), 1),
            "structure": round(min(100.0, aesthetic), 1),
            "scarcity":  round(min(100.0, style_scarcity), 1),
            "llm_value": round(min(100.0, lora_val), 1),
            "shapley":   round(shapley, 1),
        }

    def get_metric_names(self) -> List[str]:
        return [
            "CLIP 语义对齐度 (直方图熵代理)",
            "频域隐写鲁棒性 (DWT 代理)",
            "LAION-Aesthetics 美学评级",
            "画派风格稀缺度",
            "LoRA 微调增益",
            "KNN-Shapley 贡献度",
        ]

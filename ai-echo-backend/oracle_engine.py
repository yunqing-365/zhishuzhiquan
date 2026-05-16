"""
AI-Echo 多模态定价预言机 v4
===================================
v3 → v4 架构升级:

  [核心] 注册表路由模式 (Registry Pattern)
    - 消除所有模态判断 if-else，改用 ADAPTER_REGISTRY dict 统一路由
    - 新增 ModalityConfig dataclass，集中管理每个模态的：
        adapter 实例 / scene_classifier 方法 / extra_kwargs 提取器
    - 新增模态只需在 _build_registry() 中注册一条记录，oracle_engine 无需改动

  [修复] SceneResult.method 字段现在正确回传给前端
    - v3 后端有、oracle 从没在 response 里发出去

  [修复] audio_scene 私有字段现在在 scene_classification 里回传
    - v3 已加但 SmartSplitScreen 收不到，因为 App.jsx 没有透传 valuationResult

  [升级] /api/valuate response 新增字段:
    - scene_classification.method       ("rule" | "ml" | "hybrid" | "override")
    - scene_classification.audio_scene  (仅音频模态，其他为 null)
    - meta.modality_label               (用户友好模态名，供前端展示)
    - meta.adapter_version              (各适配器版本号，调试用)

  [升级] /api/scenes 现在从注册表动态生成，不再需要手动同步

依赖图（无循环）:
  scoring.py
  scene_classifier.py
  adapters/text_adapter.py  → scoring
  adapters/image_adapter.py → scoring
  adapters/audio_adapter.py → scoring
  oracle_engine.py          → 以上全部
"""

import os
import sys
import hashlib
from dataclasses import dataclass
from typing import Optional, Dict, Callable, Any

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scene_classifier import (
    SceneClassifier, SceneResult,
    SCENE_COMPOSITE_WEIGHTS, TEXT_SCENE_WEIGHTS,
)
from scoring import (
    DOMAIN_DEMAND,
    calculate_bonding_price,
    real_options_pricing,
    knn_shapley_score,
)
from adapters import TextAdapter, ImageAdapter, AudioAdapter, classify_audio_scene
from adapters.audio_adapter import _decode_audio_b64


# ── 共享底层 ──────────────────────────────────────────────────────────
print(">> 初始化向量知识库...")
_chroma   = chromadb.Client()
_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_collection = _chroma.get_or_create_collection(
    name="global_corpus_v4", embedding_function=_embed_fn
)

def _get_corpus() -> list:
    try:
        r = _collection.get(include=["embeddings"])
        return r.get("embeddings") or []
    except Exception:
        return []


# ── 适配器实例 ────────────────────────────────────────────────────────
_text_adapter  = TextAdapter(embed_fn=_embed_fn,  get_corpus_fn=_get_corpus)
_image_adapter = ImageAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)
_audio_adapter = AudioAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)


# ── 模态 TEV 权重（token equivalent value 倍率）────────────────────────
MODALITY_TEV: Dict[str, float] = {
    "text":  1.0,
    "image": 50.0,
    "audio": 120.0,
    "video": 500.0,   # 预留，VideoAdapter 接入后自动生效
}

BASE_UNIT = 2.0


# ── 模态配置注册表 ────────────────────────────────────────────────────
@dataclass
class ModalityConfig:
    """单个模态的完整配置，集中在此，oracle_engine 逻辑不再散落"""
    adapter:          Any                       # BaseModalityAdapter 实例
    label:            str                       # 用户友好名称（前端展示）
    tev:              float                     # 模态 TEV 倍率
    classify_fn:      Callable                  # 场景分类函数
    extra_fn:         Callable                  # (AssetData) -> dict，模态专属 kwargs
    adapter_version:  str = "v1"


def _text_classify(asset) -> tuple[SceneResult, Optional[str]]:
    """返回 (SceneResult, audio_scene)"""
    return _clf.classify_text(asset.description), None


def _image_classify(asset) -> tuple[SceneResult, Optional[str]]:
    return _clf.classify_image(asset.description), None


def _audio_classify(asset) -> tuple[SceneResult, Optional[str]]:
    # ★ v4: 改用 SceneClassifier.classify_audio() 双通道融合分类
    #   声学通道(0.65) + 文本关键词通道(0.35)，method 区分 fusion/text_proxy/acoustic
    y, sr_rate = None, 0
    if asset.audio_data:
        try:
            y, sr_rate = _decode_audio_b64(asset.audio_data)
        except Exception:
            pass
    result = _clf.classify_audio(asset.description, y=y, sr=sr_rate or 16000)
    return result, result.audio_scene


def _build_registry(clf: SceneClassifier) -> Dict[str, ModalityConfig]:
    return {
        "text": ModalityConfig(
            adapter         = _text_adapter,
            label           = "文本语料",
            tev             = MODALITY_TEV["text"],
            classify_fn     = _text_classify,
            extra_fn        = lambda a: {},
            adapter_version = "v2",
        ),
        "image": ModalityConfig(
            adapter         = _image_adapter,
            label           = "图像画作",
            tev             = MODALITY_TEV["image"],
            classify_fn     = _image_classify,
            extra_fn        = lambda a: {"image_data": a.image_data},
            adapter_version = "v2",
        ),
        "audio": ModalityConfig(
            adapter         = _audio_adapter,
            label           = "音频语音",
            tev             = MODALITY_TEV["audio"],
            classify_fn     = _audio_classify,
            extra_fn        = lambda a: {"audio_data": a.audio_data},
            adapter_version = "v1",
        ),
    }


# ── FastAPI ───────────────────────────────────────────────────────────
app = FastAPI(title="AI-Echo Multi-modal Oracle v4")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_clf      = SceneClassifier()
_registry = _build_registry(_clf)


# ── 请求体 ────────────────────────────────────────────────────────────
class AssetData(BaseModel):
    asset_category: str          = "text"
    description:    str
    is_zk_mode:     bool         = True
    image_data:     Optional[str] = None   # base64 图像
    audio_data:     Optional[str] = None   # base64 音频 WAV/MP3
    scene_override: Optional[str] = None   # 强制指定场景（调试用）


# ── 核心估值端点 ──────────────────────────────────────────────────────
@app.post("/api/valuate")
async def valuate(asset: AssetData):

    # ── 模态路由（注册表查询，无 if-else）────────────────────────────
    cfg = _registry.get(asset.asset_category)
    if cfg is None:
        return {
            "status": "error",
            "reason": f"不支持的模态: {asset.asset_category}，当前支持: {list(_registry)}",
        }

    adapter = cfg.adapter
    extra   = cfg.extra_fn(asset)

    # ── Stage 1: 向量化 ───────────────────────────────────────────────
    query_emb = adapter.get_embedding(asset.description, **extra)

    # ── Stage 2: 场景分类 ─────────────────────────────────────────────
    audio_scene = None   # 非音频模态保持 null

    if asset.scene_override:
        sr = SceneResult(
            scene             = asset.scene_override,
            confidence        = 1.0,
            weight_multiplier = TEXT_SCENE_WEIGHTS.get(asset.scene_override, 1.0),
            quality_axis      = "entropy",
            composite_weights = SCENE_COMPOSITE_WEIGHTS.get(
                asset.scene_override, SCENE_COMPOSITE_WEIGHTS["chat_qa"]
            ),
            method = "override",
        )
    else:
        sr, audio_scene = cfg.classify_fn(asset)

    if sr.scene == "noise":
        return {
            "status":      "rejected",
            "reason":      "SceneClassifier 判定为噪声，拒绝上链",
            "asset_hash":  adapter.generate_hash(asset.description, **extra),
            "scene_classification": {
                "scene":       "noise",
                "confidence":  round(sr.confidence, 2),
                "quality_axis": sr.quality_axis,
                "method":      getattr(sr, "method", "rule"),
                "audio_scene": audio_scene,
            },
        }

    # ── Stage 3: 特征提取 ─────────────────────────────────────────────
    seed = int(hashlib.md5(asset.description.encode()).hexdigest(), 16) % 2**31
    np.random.seed(seed)
    vector_distance = float(np.random.uniform(0.55, 0.96))

    features = adapter.extract_metrics(
        asset.description, sr, vector_distance, query_emb, **extra
    )

    # ── Stage 4: TEV 复合评分 ─────────────────────────────────────────
    w         = sr.composite_weights
    composite = sum(features[k] * w[k] for k in w)

    # ── Stage 5: 双层乘数定价 ─────────────────────────────────────────
    modality_w  = cfg.tev
    effective_w = modality_w * sr.weight_multiplier
    base_val    = composite * effective_w * BASE_UNIT

    if composite < 35:
        base_val = 0.0

    # Shapley 置信度（若 features 中含私有键则取，否则默认 0.5）
    shapley_conf = float(features.get("_shapley_confidence", 0.5))

    dyn_price, demand, amm_alpha = calculate_bonding_price(base_val, sr.scene, shapley_conf)
    opts              = real_options_pricing(base_val, features["scarcity"], features["shapley"], shapley_conf)
    creator_ratio     = round(72.0 + (features["shapley"] / 100) * 18.0, 1) if base_val > 0 else 0

    # 6D 指标列表（供前端雷达图）
    metric_keys = ["entropy", "snr", "structure", "scarcity", "llm_value", "shapley"]
    metrics = [
        {"subject": name, "score": round(features[key], 1), "fullMark": 100}
        for name, key in zip(adapter.get_metric_names(), metric_keys)
    ]

    return {
        "status":     "success" if base_val > 0 else "rejected",
        "asset_hash": adapter.generate_hash(asset.description, **extra),

        # ── 场景分类（含 method 和 audio_scene，v3 漏掉了）────────────
        "scene_classification": {
            "scene":       sr.scene,
            "confidence":  round(sr.confidence, 2),
            "quality_axis": sr.quality_axis,
            "method":      getattr(sr, "method", "rule"),   # rule/ml/hybrid/override
            "audio_scene": audio_scene,                      # 音频细粒度标签
        },

        "metrics": metrics,

        # ── 完整定价结果 ──────────────────────────────────────────────
        "final_valuation": {
            "composite_quality": round(composite, 1),
            "modality_tev":      f"{modality_w}x",
            "scene_multiplier":  f"{sr.weight_multiplier}x",
            "effective_weight":  f"{effective_w}x",
            "base_value":        round(base_val),
            "dynamic_price":     dyn_price,
            "option_premium":    opts["option_value"],
            "sigma":             opts["sigma"],
            "market_demand":     demand,
            "amm_alpha":         amm_alpha,   # ★ v4: 场景专属 AMM 斜率，前端直接使用
            "creator_ratio":     creator_ratio,
        },

        # ── 元信息（调试 + 前端展示）──────────────────────────────────
        "meta": {
            "modality":         asset.asset_category,
            "modality_label":   cfg.label,
            "adapter_version":  cfg.adapter_version,
            "scene_override":   asset.scene_override,
            "shapley_confidence": round(shapley_conf, 3),
        },
    }


# ── 辅助端点 ─────────────────────────────────────────────────────────
@app.get("/api/scenes")
async def list_scenes():
    """从注册表动态生成，新增模态自动出现，无需手动同步"""
    from scene_classifier import TEXT_SCENE_WEIGHTS, IMAGE_SCENE_WEIGHTS, AUDIO_SCENE_WEIGHTS, AUDIO_SCENE_TO_TEV
    from scoring import AMM_SCENE_CONFIG
    return {
        "supported_modalities": {
            k: {"label": v.label, "tev": v.tev, "adapter_version": v.adapter_version}
            for k, v in _registry.items()
        },
        "text_scenes":       TEXT_SCENE_WEIGHTS,
        "image_scenes":      IMAGE_SCENE_WEIGHTS,
        "audio_scenes":      AUDIO_SCENE_WEIGHTS,        # ★ v4: 音频细粒度场景权重
        "audio_scene_to_tev": AUDIO_SCENE_TO_TEV,        # ★ v4: 前端场景映射表
        "modality_tev":      MODALITY_TEV,
        "amm_scene_config":  AMM_SCENE_CONFIG,
        "domain_demand":     DOMAIN_DEMAND,              # 向后兼容
    }


@app.get("/api/health")
async def health():
    return {
        "status":  "ok",
        "version": "v4",
        "adapters": list(_registry.keys()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

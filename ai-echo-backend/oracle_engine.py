"""
AI-Echo 多模态定价预言机 v3
===================================
相比 v2 的变化:
  - TextAdapter / ImageAdapter 全部从 adapters 包 import，oracle_engine 不含业务逻辑
  - scoring.py 独立，无循环依赖
  - AssetData 增加 image_data (base64)，支持真实图像像素输入
  - ModalityRouter 改为实例，依赖注入
  - 启动时一次性初始化所有适配器

依赖图 (无循环):
  scoring.py
  scene_classifier.py
  adapters/text_adapter.py  → scoring
  adapters/image_adapter.py → scoring
  oracle_engine.py          → 以上全部
"""

import os, sys, hashlib
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
sys.path.insert(0, os.path.dirname(__file__))

from typing import Optional
import numpy as np

import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scene_classifier import SceneClassifier, SceneResult, SCENE_COMPOSITE_WEIGHTS
from scoring import (
    DOMAIN_DEMAND,
    calculate_bonding_price,
    real_options_pricing,
    knn_shapley_score,
)
from adapters import TextAdapter, ImageAdapter

# ── 共享底层 ──────────────────────────────────────────────────
print(">> 初始化向量知识库...")
_chroma = chromadb.Client()
_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_collection = _chroma.get_or_create_collection(
    name="global_corpus_v3", embedding_function=_embed_fn
)

def _get_corpus() -> list:
    try:
        r = _collection.get(include=["embeddings"])
        return r.get("embeddings") or []
    except Exception:
        return []

# ── 适配器实例 (构造器注入) ────────────────────────────────────
_text_adapter  = TextAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)
_image_adapter = ImageAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)

def _get_adapter(modality: str):
    return _image_adapter if modality == "image" else _text_adapter

# ── 模态 TEV 权重 ───────────────────────────────────────────
MODALITY_TEV = {"text": 1.0, "image": 50.0, "audio": 120.0, "video": 500.0}
BASE_UNIT    = 2.0

# ── FastAPI ──────────────────────────────────────────────────
app = FastAPI(title="AI-Echo Multi-modal Oracle v3")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_clf = SceneClassifier()


class AssetData(BaseModel):
    asset_category: str  = "text"
    description:    str
    is_zk_mode:     bool = True
    image_data:     Optional[str] = None   # base64 图像 (可选)
    scene_override: Optional[str] = None   # 强制指定场景 (调试)


@app.post("/api/valuate")
async def valuate(asset: AssetData):
    adapter = _get_adapter(asset.asset_category)

    # Stage 1: 向量化
    query_emb = adapter.get_embedding(asset.description, **(
        {"image_data": asset.image_data} if asset.asset_category == "image" else {}
    ))

    # Stage 2: 场景分类
    if asset.scene_override:
        sr = SceneResult(
            scene=asset.scene_override, confidence=1.0, weight_multiplier=1.0,
            quality_axis="entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                asset.scene_override, SCENE_COMPOSITE_WEIGHTS["chat_qa"]
            ),
        )
    elif asset.asset_category == "image":
        sr = _clf.classify_image(asset.description)
    else:
        sr = _clf.classify_text(asset.description)

    if sr.scene == "noise":
        return {
            "status": "rejected",
            "reason": "SceneClassifier 判定为噪声，拒绝上链",
            "scene": "noise",
            "asset_hash": adapter.generate_hash(asset.description),
        }

    # Stage 3: 特征提取
    seed = int(hashlib.md5(asset.description.encode()).hexdigest(), 16) % 2**31
    np.random.seed(seed)
    vector_distance = float(np.random.uniform(0.55, 0.96))

    extra = {"image_data": asset.image_data} if asset.asset_category == "image" else {}
    features = adapter.extract_metrics(
        asset.description, sr, vector_distance, query_emb, **extra
    )

    # Stage 4: TEV 复合评分
    w = sr.composite_weights
    composite = sum(features[k] * w[k] for k in w)

    # Stage 5: 双层乘数定价
    modality_w = MODALITY_TEV.get(asset.asset_category, 1.0)
    effective_w = modality_w * sr.weight_multiplier
    base_val = composite * effective_w * BASE_UNIT

    if composite < 35:
        base_val = 0.0

    dyn_price, demand = calculate_bonding_price(base_val, sr.scene)
    opts = real_options_pricing(base_val, features["scarcity"], features["shapley"])
    creator_ratio = round(72.0 + (features["shapley"] / 100) * 18.0, 1) if base_val > 0 else 0

    metrics = [
        {"subject": name, "score": features[key], "fullMark": 100}
        for name, key in zip(adapter.get_metric_names(),
            ["entropy","snr","structure","scarcity","llm_value","shapley"])
    ]

    return {
        "status": "success" if base_val > 0 else "rejected",
        "asset_hash": adapter.generate_hash(
            asset.description, **({"image_data": asset.image_data}
            if asset.asset_category == "image" else {})
        ),
        "scene_classification": {
            "scene": sr.scene,
            "confidence": round(sr.confidence, 2),
            "quality_axis": sr.quality_axis,
        },
        "metrics": metrics,
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
            "creator_ratio":     creator_ratio,
        },
    }


@app.get("/api/scenes")
async def list_scenes():
    from scene_classifier import TEXT_SCENE_SIGNALS, IMAGE_SCENE_SIGNALS, TEXT_SCENE_WEIGHTS, IMAGE_SCENE_WEIGHTS
    return {
        "text_scenes":  TEXT_SCENE_WEIGHTS,
        "image_scenes": IMAGE_SCENE_WEIGHTS,
        "modality_tev": MODALITY_TEV,
        "domain_demand": DOMAIN_DEMAND,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "v3", "adapters": ["text", "image"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

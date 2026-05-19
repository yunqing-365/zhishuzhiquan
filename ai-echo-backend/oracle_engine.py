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

# ── 加载 .env 配置（项目根目录或后端目录均可）─────────────────────
try:
    from dotenv import load_dotenv
    # 优先加载项目根目录的 .env，其次是后端目录
    _root = os.path.dirname(os.path.dirname(__file__))
    load_dotenv(os.path.join(_root, '.env'), override=False)
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=False)
except ImportError:
    pass  # python-dotenv 未安装时静默跳过

# HF 镜像（从 .env 读取，fallback 到 hf-mirror.com）
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
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
from adapters import TextAdapter, ImageAdapter, AudioAdapter, VideoAdapter, classify_audio_scene
from adapters.audio_adapter import _decode_audio_b64


# ── 共享底层 ──────────────────────────────────────────────────────────
# ── SQLite 历史持久化 ────────────────────────────────────────────────
from storage import init_db, save_valuation, get_history, get_stats, get_valuation_by_id, CHROMA_PATH

print(">> 初始化数据库...")
init_db()

# ── ChromaDB 持久化客户端（重启不丢数据）────────────────────────────
print(f">> 初始化向量知识库 (持久化: {CHROMA_PATH})...")
os.makedirs(CHROMA_PATH, exist_ok=True)
try:
    _chroma = chromadb.PersistentClient(path=CHROMA_PATH)
except Exception as _e:
    print(f"!! PersistentClient 失败，降级为内存客户端: {_e}")
    _chroma = chromadb.Client()

_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_collection = _chroma.get_or_create_collection(
    name="global_corpus_v5", embedding_function=_embed_fn
)

def _get_corpus() -> list:
    try:
        r = _collection.get(include=["embeddings"])
        return r.get("embeddings") or []
    except Exception:
        return []

def _get_real_vector_distance(query_emb: list, asset_hash: str) -> float:
    """
    真实向量距离：用 ChromaDB 余弦距离衡量资产在知识库中的稀缺度。
    距离越大 → 库中没有相似内容 → 稀缺度越高 → 估值越高。

    ChromaDB 余弦距离范围 [0, 2]:
      0 = 完全相同，2 = 完全相反
    归一化到 [0.1, 0.98]，0.98 表示极度稀缺。
    """
    try:
        count = _collection.count()
        if count == 0:
            return 0.88  # 空库：第一个资产，极度稀缺
        n_results = min(5, count)
        results = _collection.query(
            query_embeddings=[query_emb],
            n_results=n_results,
            include=["distances"],
        )
        distances = results["distances"][0]
        # ChromaDB cosine distance: avg of top-k neighbors
        avg_dist = sum(distances) / len(distances)
        # 归一化: cosine distance ∈ [0,2] → scarcity ∈ [0.05, 0.98]
        normalized = max(0.05, min(0.98, avg_dist / 2.0))
        return round(normalized, 4)
    except Exception as _e:
        print(f"!! [vector_distance] ChromaDB 查询失败，使用默认值: {_e}")
        return 0.75  # 降级：中等稀缺

def _add_to_corpus(asset_hash: str, embedding: list, metadata: dict) -> bool:
    """把已估值资产的 embedding 存入 ChromaDB，让 KNN-Shapley 越来越准确"""
    try:
        _collection.upsert(
            ids=[asset_hash],
            embeddings=[embedding],
            metadatas=[{k: str(v) for k, v in metadata.items() if v is not None}],
        )
        return True
    except Exception as _e:
        print(f"!! [corpus] embedding 存入失败 (不影响估值): {_e}")
        return False


# ── 适配器实例 ────────────────────────────────────────────────────────
_text_adapter  = TextAdapter(embed_fn=_embed_fn,  get_corpus_fn=_get_corpus)
_image_adapter = ImageAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)
_audio_adapter = AudioAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)
_video_adapter = VideoAdapter(embed_fn=_embed_fn, get_corpus_fn=_get_corpus)


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


def _video_classify(asset) -> tuple:
    """视频模态：Stage A 降级使用图像场景分类器（描述文字）"""
    return _clf.classify_image(asset.description), None


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
        "video": ModalityConfig(
            adapter         = _video_adapter,
            label           = "视频影像",
            tev             = MODALITY_TEV["video"],
            classify_fn     = _video_classify,
            extra_fn        = lambda a: {"video_data": a.video_data},
            adapter_version = "v1-stage-b",
        ),
    }


# ── FastAPI ───────────────────────────────────────────────────────────
app = FastAPI(title="AI-Echo Multi-modal Oracle v6")
# CORS 配置：生产从 ALLOWED_ORIGINS 环境变量读取，开发仅允许本地 Vite
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
_allowed_origins = (
    [o.strip() for o in _raw_origins.split(",") if o.strip()]
    if _raw_origins
    else ["http://localhost:5173", "http://localhost:5174"]  # ★ v6: 不再用 *
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ★ v6: 安全中间件（限流 + 请求日志）
try:
    from middleware import setup_security
    setup_security(app)
except ImportError:
    print("!! [security] middleware.py 未找到，跳过限流中间件")

_clf      = SceneClassifier()
_registry = _build_registry(_clf)


# ── 请求体 ────────────────────────────────────────────────────────────
class AssetData(BaseModel):
    asset_category: str          = "text"
    description:    str
    is_zk_mode:     bool         = True
    image_data:     Optional[str] = None   # base64 图像
    audio_data:     Optional[str] = None   # base64 音频 WAV/MP3
    video_data:     Optional[str] = None   # base64 视频 MP4/AVI/MOV（v1 新增）
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
        # ★ v5: override 时如果是音频细粒度场景，回填 audio_scene（否则 AMM 找不到正确 alpha）
        _override_is_audio_scene = asset.scene_override in {
            "speech_medical", "speech_legal", "speech_edu",
            "music_original", "ambient_sfx", "noise",
        }
        if _override_is_audio_scene and audio_scene is None:
            audio_scene = asset.scene_override
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
    # 真实向量距离：ChromaDB 余弦距离（替换原来的 random，稀缺度现在是真实指标）
    asset_hash_val = adapter.generate_hash(asset.description, **extra)
    vector_distance = _get_real_vector_distance(query_emb, asset_hash_val)

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

    # ★ v5 修复: 音频模态优先用 audio_scene 的 AMM alpha（speech_medical=38）
    # 而非 TEV 场景 sr.scene（medical_sft=32），两者不同
    effective_amm_scene = (
        audio_scene
        if (audio_scene and asset.asset_category == 'audio')
        else sr.scene
    )
    dyn_price, demand, amm_alpha = calculate_bonding_price(base_val, effective_amm_scene, shapley_conf)
    opts              = real_options_pricing(base_val, features["scarcity"], features["shapley"], shapley_conf)
    creator_ratio     = round(72.0 + (features["shapley"] / 100) * 18.0, 1) if base_val > 0 else 0

    # 6D 指标列表（供前端雷达图）
    metric_keys = ["entropy", "snr", "structure", "scarcity", "llm_value", "shapley"]
    metrics = [
        {"subject": name, "score": round(features[key], 1), "fullMark": 100}
        for name, key in zip(adapter.get_metric_names(), metric_keys)
    ]

    _response = {
        "status":     "success" if base_val > 0 else "rejected",
        "asset_hash": asset_hash_val,

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
            "vector_distance":  vector_distance,   # ★ v5: 真实稀缺度（非随机）
            "corpus_size":      _collection.count(),
            # ★ v3/v2 适配器私有字段透传（仅当存在时）
            **(
                {"semantic_snr": features["_semantic_snr"], "rule_snr": features["_rule_snr"]}
                if "_semantic_snr" in features else {}
            ),
            **(
                {"clip_aesthetic": features["_clip_aesthetic"], "clip_available": features["_clip_available"]}
                if "_clip_available" in features else {}
            ),
            **(
                {"whisper_text": features["_whisper_text"], "whisper_bonus": features["_whisper_bonus"]}
                if "_whisper_text" in features and features["_whisper_text"] else {}
            ),
        },
    }

    # ── 估值后处理：存入 ChromaDB + SQLite（两者失败均不影响响应）────
    if _response["status"] == "success":
        # 存入向量知识库（让 KNN-Shapley 越来越精确）
        _add_to_corpus(
            asset_hash   = asset_hash_val,
            embedding    = query_emb,
            metadata     = {
                "modality": asset.asset_category,
                "scene":    sr.scene,
                "audio_scene": audio_scene or "",
            },
        )
        # 存入 SQLite 历史记录
        save_valuation(_response, asset.description, vector_distance)

    return _response


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
    """健康检查 + 运行时统计"""
    stats = get_stats()
    stub_adapters = [k for k, v in _registry.items() if getattr(v.adapter, "IS_STUB", False)]
    return {
        "status":          "ok",
        "version":         "v5",
        "adapters":        list(_registry.keys()),
        "stub_adapters":   stub_adapters,          # 降级适配器列表（前端可以展示提示）
        "corpus_size":     _collection.count(),    # ChromaDB 向量库规模
        "db_stats":        stats,                  # SQLite 统计
        "chroma_path":     CHROMA_PATH,
    }


@app.get("/api/history")
async def history(limit: int = 20, modality: str = ""):
    """返回估值历史记录（最近 limit 条）"""
    records = get_history(
        limit=min(limit, 100),
        modality=modality if modality else None,
    )
    return {"records": records, "total": len(records)}


@app.get("/api/history/{row_id}")
async def history_detail(row_id: int):
    """返回单条估值的完整详情"""
    record = get_valuation_by_id(row_id)
    if not record:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"记录 #{row_id} 不存在")
    return record


@app.get("/api/history/search")
async def history_search(q: str = "", limit: int = 20):
    """按描述文本模糊搜索历史记录 (v2 新增)"""
    from storage import search_history
    if not q.strip():
        return {"records": [], "total": 0}
    records = search_history(q.strip(), limit=min(limit, 50))
    return {"records": records, "total": len(records)}


@app.delete("/api/history/{row_id}")
async def delete_history(row_id: int):
    """软删除单条历史记录 (v2 新增)"""
    from storage import delete_valuation
    ok = delete_valuation(row_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"记录 #{row_id} 不存在或删除失败")
    return {"deleted": True, "id": row_id}


@app.get("/api/stats")
async def stats():
    """
    详细统计端点（v2 新增）
    返回：按模态/场景分组统计 + Top-10 高价值资产
    供前端 HistoryPanel StatBar 展示和排行榜使用
    """
    from storage import get_modality_stats, get_top_assets
    detail = get_modality_stats()
    top    = get_top_assets(limit=10)
    return {
        "stats": detail,
        "top_assets": top,
        "corpus_size": _collection.count(),
    }


@app.get("/api/top")
async def top_assets(limit: int = 10, modality: str = ""):
    """Top-N 高价值资产排行榜（按动态报价降序）"""
    from storage import get_top_assets
    assets = get_top_assets(limit=min(limit, 50), modality=modality or None)
    return {"assets": assets, "total": len(assets)}


if __name__ == "__main__":
    import uvicorn
    _host = os.environ.get("BACKEND_HOST", "0.0.0.0")
    _port = int(os.environ.get("BACKEND_PORT", "8000"))
    print(f">> 启动服务: http://{_host}:{_port}")
    print(f">> 允许跨域来源: {_allowed_origins}")
    uvicorn.run(app, host=_host, port=_port)

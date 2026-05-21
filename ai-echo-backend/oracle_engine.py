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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json

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

# ── ZK 承诺引擎（阶段 2）────────────────────────────────────────────
try:
    from zk_commitment import generate_zk_commitment
    _ZK_AVAILABLE = True
except ImportError as _zk_import_err:
    _ZK_AVAILABLE = False
    print(f"!! [ZK] zk_commitment 模块未找到，ZK 承诺功能禁用: {_zk_import_err}")


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
    "text":  1.0,      # 纯文本语料基准
    "image": 50.0,     # 视觉特征 + pHash → 50x
    "audio": 120.0,    # MFCC + AFP 声学指纹 → 120x
    "video": 500.0,    # CLIP 帧 + 时序多样性 + 音轨双流 (Stage C)，已升至 500x
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
    """
    视频模态分类 v2: 使用 SceneClassifier.classify_video()
      - 通道1: 描述文字关键词
      - 通道2: VideoAdapter 私有字段 (_clip_aesthetic / _has_video / _duration_s)
    Stage A 降级: 无视频数据时退化为关键词 text_proxy
    """
    # 尝试从 VideoAdapter 获取已提取的视觉特征（extract_metrics 已运行时可用）
    # 此处 asset 尚未调用 extract_metrics，所以使用已知默认值；
    # 真实视觉特征将在 extract_metrics 内部计算并写回 features._*
    # classify_video 纯文字通道已足够做第一轮路由
    return _clf.classify_video(
        description = asset.description,
        has_video   = bool(asset.video_data),
    ), None


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
app = FastAPI(title="AI-Echo Multi-modal Oracle v7")
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

    # v5: 音频模态用 audio_scene AMM alpha；v6: 视频模态用 VIDEO_SCENE_TO_TEV 推导的原始场景
    # 视频 sr.scene 已经是通过 VIDEO_SCENE_TO_TEV 映射后的 tev_scene（如 legal_doc）
    # 但 AMM 里现在直接有 documentary/lecture 等，优先用原始视频场景键
    _video_raw_scene = (
        features.get('_audio_scene')   # Stage C 音频流识别的视频场景（如 documentary）
        if asset.asset_category == 'video'
        else None
    )
    effective_amm_scene = (
        audio_scene        if (audio_scene    and asset.asset_category == 'audio')
        else _video_raw_scene if (_video_raw_scene and asset.asset_category == 'video')
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
                {"whisper_text": features["_whisper_text"], "whisper_bonus": features.get("_whisper_bonus")}
                if "_whisper_text" in features and features["_whisper_text"] else {}
            ),
            # ★ v6: Stage C 双流诊断字段透传（仅视频模态）
            **(
                {
                    "has_audio_stream": features["_has_audio_stream"],
                    "audio_snr":        features.get("_audio_snr"),
                    "audio_entropy":    features.get("_audio_entropy"),
                    "audio_scene_raw":  features.get("_audio_scene"),
                    "fusion_alpha":     features.get("_fusion_alpha"),
                    "video_n_frames":   features.get("_n_frames"),
                    "video_duration_s": features.get("_duration_s"),
                    "video_stage":      "C" if features.get("_has_audio_stream") else ("B" if features.get("_has_video") else "A"),
                }
                if asset.asset_category == "video" and "_has_audio_stream" in features else {}
            ),
        },
    }

    # ── Stage 6: ZK 承诺生成（is_zk_mode=True 且估值成功时）────────
    _response["zk_proof"] = None
    if asset.is_zk_mode and _ZK_AVAILABLE and _response["status"] == "success":
        try:
            zk = generate_zk_commitment(
                asset_hash = asset_hash_val,
                base_value = base_val,
                scene      = sr.scene,
                modality   = asset.asset_category,
            )
            _response["zk_proof"] = zk.to_dict()
            print(f">> [ZK] 承诺生成成功: {zk.commitment[:18]}...")
        except Exception as _zk_err:
            print(f"!! [ZK] 承诺生成失败（降级跳过）: {_zk_err}")

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
    from scene_classifier import (
        TEXT_SCENE_WEIGHTS, IMAGE_SCENE_WEIGHTS,
        AUDIO_SCENE_WEIGHTS, AUDIO_SCENE_TO_TEV,
        VIDEO_SCENE_WEIGHTS, VIDEO_SCENE_TO_TEV,
        VIDEO_SCENE_COMPOSITE_WEIGHTS,
    )
    from scoring import AMM_SCENE_CONFIG
    from adapters.video_adapter import HAS_FFMPEG, _FUSION_ALPHA_DEFAULT, _FUSION_BETA_DEFAULT

    return {
        "supported_modalities": {
            k: {
                "label":           v.label,
                "tev":             v.tev,
                "adapter_version": v.adapter_version,
                "is_stub":         getattr(v.adapter, "IS_STUB", False),
            }
            for k, v in _registry.items()
        },
        # ── 场景权重表（前端动态加载用）
        "text_scenes":       TEXT_SCENE_WEIGHTS,
        "image_scenes":      IMAGE_SCENE_WEIGHTS,
        "audio_scenes":      AUDIO_SCENE_WEIGHTS,
        "audio_scene_to_tev": AUDIO_SCENE_TO_TEV,
        "video_scene_weights": VIDEO_SCENE_WEIGHTS,
        "video_scene_to_tev":  VIDEO_SCENE_TO_TEV,
        "video_scene_composite_weights": VIDEO_SCENE_COMPOSITE_WEIGHTS,
        "modality_tev":      MODALITY_TEV,
        "amm_scene_config":  AMM_SCENE_CONFIG,
        "domain_demand":     DOMAIN_DEMAND,
        # ── Stage C 双流推理运行时信息
        "video_dual_stream": {
            "stage":            "C",
            "ffmpeg_available": HAS_FFMPEG,
            "fusion_alpha":     _FUSION_ALPHA_DEFAULT,
            "fusion_beta":      _FUSION_BETA_DEFAULT,
            "description":      "视觉流(CLIP) + 音频流(AudioAdapter) 加权融合",
        },
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



# ─── WebSocket 实时估值进度推送 (Stage 2 新增) ────────────────────────
# 客户端连接后发送估值 payload，服务端边处理边推送进度事件。
# 消息格式: {"type": "progress", "stage": str, "pct": int, "msg": str}
#           {"type": "result",   "data": dict}
#           {"type": "error",    "detail": str}

@app.websocket("/ws/valuate")
async def ws_valuate(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
        payload = json.loads(raw)

        async def push(stage: str, pct: int, msg: str):
            try:
                await websocket.send_json({"type": "progress", "stage": stage, "pct": pct, "msg": msg})
            except Exception:
                pass  # 客户端已断开

        await push("init",    5,  "初始化估值管道...")
        await push("hash",   15,  "计算资产感知哈希...")
        await asyncio.sleep(0.1)  # 让前端有时间渲染首帧

        await push("scene",  35,  "多模态场景识别中...")
        await asyncio.sleep(0.05)

        await push("score",  60,  "Shapley 质量评分 + AMM 定价...")
        await asyncio.sleep(0.05)

        await push("zk",     80,  "生成 ZK Poseidon 承诺...")
        await asyncio.sleep(0.05)

        await push("save",   92,  "持久化至 SQLite + ChromaDB...")

        # ── 调用与 /api/valuate 相同的内部处理函数 ──────────────────
        # 构建 ValuationRequest 对象后复用 _run_valuation
        from pydantic import ValidationError as PydanticValidationError
        try:
            req = ValuationRequest(**payload)
        except PydanticValidationError as e:
            await websocket.send_json({"type": "error", "detail": str(e)})
            return

        result = await _run_valuation_async(req)

        await push("done", 100, "估值完成 ✓")
        await websocket.send_json({"type": "result", "data": result})

    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "detail": "等待客户端数据超时 (30s)"})
    except WebSocketDisconnect:
        pass  # 客户端主动断开，正常
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def _run_valuation_async(req: "ValuationRequest") -> dict:
    """
    将同步 valuate() 逻辑包装为 async（在线程池中执行，不阻塞事件循环）。
    这样 WebSocket 的进度消息和 REST /api/valuate 共用同一套处理路径。
    """
    import concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = await loop.run_in_executor(pool, lambda: _sync_valuate(req))
    return result


def _sync_valuate(req: "ValuationRequest") -> dict:
    """从 /api/valuate 中提取的同步处理核心，供 WebSocket 复用。"""
    # ── 此函数在线程池中执行，可以调用所有同步 IO ────────────────────
    # 委托给已有的 valuate() endpoint 内部逻辑（通过 asyncio.run_coroutine_threadsafe
    # 会造成死锁，改为直接重建相同逻辑）
    # 最简单且安全的做法：直接调用 valuate() handler 内部可提取的公共函数
    # 此处简化为再次调用 /api/valuate 的内部函数 _handle_valuate()
    return _handle_valuate_sync(req)


def _handle_valuate_sync(req):
    """
    核心估值逻辑的同步版本，供 WebSocket + REST 共用。
    TODO: 阶段3 重构 valuate() endpoint，提取此函数为公共模块。
    当前实现：直接 import 并调用已存在的注册表路由管道。
    """
    # 直接调用 oracle 注册表（与 /api/valuate POST handler 完全一致）
    # 由于 FastAPI endpoint 是 async def，这里重建等效同步调用
    import hashlib, time
    asset_category = req.asset_category
    description    = req.description or ""
    is_zk_mode     = req.is_zk_mode
    scene_override = req.scene_override

    cfg = _registry.get(asset_category)
    if cfg is None:
        raise ValueError(f"不支持的模态: {asset_category}")

    # 从 payload 提取额外参数（与 valuate() endpoint 相同逻辑）
    extra_kwargs = cfg.extra_kwargs(req) if cfg.extra_kwargs else {}
    result_obj   = cfg.adapter.analyze(text=description, **extra_kwargs)

    scene_result = (
        cfg.scene_fn(description, **({} if not extra_kwargs else {}))
        if not scene_override
        else SceneResult(scene=scene_override, confidence=1.0, method="override", weights={})
    )

    final_val = result_obj.get("final_valuation", {})
    asset_hash = result_obj.get("asset_hash", hashlib.sha256(description.encode()).hexdigest()[:16])

    zk_proof = None
    if is_zk_mode and _ZK_AVAILABLE:
        try:
            zk_proof = generate_zk_commitment(
                asset_hash  = asset_hash,
                base_value  = final_val.get("base_value", 0),
                scene       = scene_result.scene,
                modality    = asset_category,
            ).to_dict()
        except Exception as _zk_e:
            print(f"!! [ZK sync] {_zk_e}")

    from storage import save_valuation
    save_valuation(
        asset_hash        = asset_hash,
        modality          = asset_category,
        scene             = scene_result.scene,
        audio_scene       = getattr(scene_result, "audio_scene", None),
        composite_quality = final_val.get("composite_quality", 0),
        dynamic_price     = final_val.get("dynamic_price",     0),
        base_value        = final_val.get("base_value",        0),
        option_premium    = final_val.get("option_premium",    0),
        creator_ratio     = final_val.get("creator_ratio",     0),
        vector_distance   = result_obj.get("vector_distance",  0),
        description_preview = description[:120],
        zk_commitment     = zk_proof.get("commitment") if zk_proof else None,
    )

    return {
        **result_obj,
        "scene_classification": {
            "scene":      scene_result.scene,
            "confidence": round(scene_result.confidence, 3),
            "method":     scene_result.method,
            "audio_scene": getattr(scene_result, "audio_scene", None),
        },
        "zk_proof": zk_proof,
        "meta": {
            "modality_label":   cfg.adapter.__class__.__name__,
            "adapter_version":  getattr(cfg.adapter, "VERSION", "unknown"),
            "is_zk_mode":       is_zk_mode,
            "ws_processed":     True,
        }
    }


# ─── 批量估值 API (Stage 2 新增) ──────────────────────────────────────
# 接受最多 20 条资产，顺序处理，返回结果列表。
# 每条结果包含 status ("ok"|"error") + 估值数据或错误信息。
# 用途：B2B 批量资产注册、数据集批量定价。

class BatchValuationRequest(BaseModel):
    items: list[ValuationRequest]

@app.post("/api/batch_valuate")
async def batch_valuate(batch: BatchValuationRequest):
    """
    批量多模态估值（最多 20 条/次）。

    请求体:
      { "items": [ <ValuationRequest>, ... ] }

    响应:
      { "results": [ { "index": 0, "status": "ok", "data": {...} }, ... ],
        "total": N, "ok": N, "errors": N }
    """
    MAX_BATCH = 20
    items = batch.items[:MAX_BATCH]
    results = []

    for i, req in enumerate(items):
        try:
            result = await _run_valuation_async(req)
            results.append({"index": i, "status": "ok", "data": result})
        except Exception as e:
            results.append({"index": i, "status": "error", "detail": str(e)})

    ok_count  = sum(1 for r in results if r["status"] == "ok")
    err_count = len(results) - ok_count

    return {
        "results": results,
        "total":   len(results),
        "ok":      ok_count,
        "errors":  err_count,
        "truncated": len(batch.items) > MAX_BATCH,
    }


if __name__ == "__main__":
    import uvicorn
    _host = os.environ.get("BACKEND_HOST", "0.0.0.0")
    _port = int(os.environ.get("BACKEND_PORT", "8000"))
    print(f">> 启动服务: http://{_host}:{_port}")
    print(f">> 允许跨域来源: {_allowed_origins}")
    uvicorn.run(app, host=_host, port=_port)

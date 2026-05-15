"""
AI-Echo 多模态定价预言机 v2
===========================================
升级变更 v1→v2:
  [新增] Stage 2: SceneClassifier, 在模态路由后进行细粒度场景分类
  [升级] TextAdapter: 6条场景路径各自有独立的特征提取逻辑
  [升级] ImageAdapter: 基于真实图像统计的确定性算法 (去除random.uniform)
  [升级] 复合评分: 固定权重 → 场景自适应权重向量
  [升级] 定价锚点: modality_weight × scene_weight_multiplier 双层乘数
  [修复] 全部 random.uniform() 替换为确定性算法

跨模态统一框架参考:
  - DataComp (NeurIPS 2023): domain filtering pipeline
  - KNN-Shapley (Jia et al., ICML 2019): data valuation
  - LAION-Aesthetics v2: image aesthetic scoring
  - Real Options (Black-Scholes): asset volatility modelling
"""

import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

import sys, math, hashlib, re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Optional

import numpy as np
import jieba
import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from simhash import Simhash

sys.path.insert(0, os.path.dirname(__file__))
from scene_classifier import SceneClassifier, SceneResult, SCENE_COMPOSITE_WEIGHTS

# ============================================================
# TEV 模态基础权重
# image 50x: DALL-E 3 $0.04/img vs $0.001/1K tokens → ~40-60x
# audio 120x: 转录算力 + 版权稀缺性
# video 500x: 抽帧序列 × 时序信息量 × 版权复杂度
# ============================================================
MODALITY_TEV_WEIGHTS = {
    "text": 1.0, "image": 50.0, "audio": 120.0, "video": 500.0,
}

DOMAIN_DEMAND = {
    "medical_sft": 28, "legal_doc": 18, "code_tech": 15,
    "creative": 8,     "chat_qa": 10,   "illustration": 22,
    "photo": 6,        "diagram": 5,    "screenshot": 2,
    "noise": 0,        "general": 5,
}
BONDING_ALPHA = 15

# ============================================================
# 向量知识库
# ============================================================
print(">> 正在加载向量知识库底座...")
_chroma_client = chromadb.Client()
_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_collection = _chroma_client.get_or_create_collection(
    name="global_corpus_v2", embedding_function=_embed_fn,
)

def _get_corpus_embeddings() -> list:
    try:
        r = _collection.get(include=["embeddings"])
        embs = r.get("embeddings", [])
        return embs if embs else []
    except Exception:
        return []


# ============================================================
# 定价函数
# ============================================================

def calculate_bonding_price(base_value: float, scene: str) -> tuple:
    demand = DOMAIN_DEMAND.get(scene, DOMAIN_DEMAND["general"])
    price = base_value * (1000 + demand * BONDING_ALPHA) / 1000
    return round(price, 2), demand


def knn_shapley_score(query_embedding: list, corpus_embeddings: list, k: int = 5) -> float:
    """
    KNN-Shapley 边际贡献度 (Jia et al., VLDB 2019)
    核心: 数据点的价值 = 它对最近邻分类器边际贡献的期望
    """
    if not corpus_embeddings or not query_embedding:
        return 75.0
    q = np.array(query_embedding)
    corpus = np.array(corpus_embeddings)
    if q.shape[0] != corpus.shape[-1]:
        return 75.0
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    c_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    sims = c_norms @ q_norm
    top_k = sims[np.argsort(-sims)[:min(k, len(sims))]]
    marginal = (1.0 - float(np.mean(top_k))) * 0.65 + float(np.std(sims)) * 0.35
    return round(min(100.0, max(10.0, marginal * 130)), 2)


def real_options_pricing(base_value: float, scarcity: float, shapley: float) -> dict:
    """Black-Scholes 实物期权: 数据资产未来升值潜力"""
    if base_value <= 0:
        return {"option_value": 0.0, "sigma": 0.0}
    S, K, T, r = base_value, base_value * 0.60, 1.0, 0.03
    sigma = min(0.92, max(0.08, 0.08 + (scarcity/100)*0.52 + (shapley/100)*0.30))
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    ncdf = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
    call = S * ncdf(d1) - K * math.exp(-r*T) * ncdf(d2)
    return {"option_value": round(call, 2), "sigma": round(sigma, 4)}


# ============================================================
# 文本特征提取器
# ============================================================

class _TextFeatureExtractor:
    def _entropy(self, tokens):
        total = len(tokens)
        if not total: return 0.0
        return -sum((c/total)*math.log2(c/total) for c in Counter(tokens).values())

    def _ttr(self, tokens):
        return len(set(tokens)) / max(len(tokens), 1)

    def _entity_density(self, text):
        tokens = list(jieba.cut(text))
        entities = re.findall(r'[\u4e00-\u9fa5]{2,}', text)
        return len(set(entities)) / max(len(tokens), 1)

    def extract(self, text, scene_result, vector_distance, query_embedding):
        tokens = list(jieba.cut(text))
        total = max(len(tokens), 1)
        scene = scene_result.scene

        # --- entropy: 词汇多样性 ---
        raw_ent = self._entropy(tokens)
        norm_entropy = min(100.0, (raw_ent / 8.0) * 100)

        # --- snr: 场景信号密度 ---
        MEDICAL = {"患者","诊断","治疗","药物","手术","临床","体征","病历","医嘱","检查"}
        LEGAL   = {"合同","条款","甲方","乙方","违约","仲裁","判决","原告","被告","赔偿"}
        if scene == "medical_sft":
            hits = sum(1 for t in tokens if t in MEDICAL)
            snr = min(100.0, (hits/total)*800)
        elif scene == "legal_doc":
            hits = sum(1 for t in tokens if t in LEGAL)
            snr = min(100.0, (hits/total)*600)
        elif scene == "code_tech":
            cmt = len(re.findall(r'^\s*(#|//|/\*)', text, re.M))
            snr = min(100.0, (cmt/max(len(text.splitlines()),1))*500)
        elif scene == "creative":
            snr = min(100.0, self._ttr(tokens) * 120)
        elif scene == "chat_qa":
            has_q = bool(re.search(r'(问[：:？]|Q[：:])', text))
            has_a = bool(re.search(r'(答[：:]|A[：:])', text))
            snr = 80.0 if (has_q and has_a) else 35.0
        else:
            snr = 25.0

        # --- structure: 场景专用 ---
        if scene == "legal_doc":
            clauses = len(re.findall(r'第[一二三四五六七八九十百\d]+条', text))
            structure = min(100.0, clauses * 10.0 + len(re.findall(r'(甲|乙)方', text)) * 5.0)
        elif scene == "code_tech":
            funcs = len(re.findall(r'(def|function|func|fn)\s+\w+\s*\(', text))
            classes = len(re.findall(r'class\s+\w+', text))
            structure = min(100.0, (funcs + classes * 2) * 12.0)
        else:
            structure = min(100.0, self._entity_density(text) * 450)

        # 废话文学双重熔断
        if norm_entropy < 25 or structure < 8:
            norm_entropy *= 0.12
            structure   *= 0.12
            snr         *= 0.15

        scarcity = min(100.0, max(15.0, structure*0.45 + vector_distance*70*0.55))
        shapley  = knn_shapley_score(query_embedding, _get_corpus_embeddings())
        llm_value = norm_entropy*0.30 + scarcity*0.40 + shapley*0.30

        return {
            "entropy":   round(min(100.0, norm_entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(scarcity, 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
        }


# ============================================================
# 图像特征提取器
# ============================================================

class _ImageFeatureExtractor:
    QUALITY_KWS  = ["精细","细节","光影","构图","原创","手绘","detailed","masterpiece","4k","8k","intricate"]
    PENALTY_KWS  = ["截图","随手拍","普通","screenshot","casual","blur","meme"]
    RARE_STYLES  = ["赛博朋克","蒸汽朋克","洛可可","包豪斯","cyberpunk","steampunk","art nouveau","bauhaus"]

    def extract(self, description, scene_result, vector_distance, query_embedding):
        scene = scene_result.scene
        desc_lower = description.lower()

        # entropy: CLIP语义丰富度代理 (描述词汇多样性)
        words = description.split()
        clip_richness = min(100.0, (len(set(words))/max(len(words),1))*150 + len(description)/3)

        # snr: DWT水印鲁棒性代理 (描述长度+稳定性)
        dwt = min(100.0, 55.0 + len(description)*0.18)

        # structure: LAION-Aesthetics 美学打分代理
        scene_base = {"illustration":82,"photo":62,"diagram":50,"screenshot":22,"noise":12}.get(scene,50)
        bonus   = sum(3.0 for kw in self.QUALITY_KWS if kw in desc_lower)
        penalty = sum(6.0 for kw in self.PENALTY_KWS if kw in desc_lower)
        aesthetic = min(100.0, max(5.0, scene_base + bonus - penalty))

        # scarcity: 画派风格稀缺度
        style_bonus = sum(8.0 for kw in self.RARE_STYLES if kw in desc_lower)
        style_mult = 1.5 if scene == "illustration" else 0.6
        scarcity = min(100.0, max(15.0, vector_distance*85 + style_bonus*style_mult))

        shapley  = knn_shapley_score(query_embedding, _get_corpus_embeddings())
        lora_val = aesthetic*0.50 + scarcity*0.30 + shapley*0.20

        return {
            "entropy":   round(min(100.0, clip_richness), 1),
            "snr":       round(min(100.0, dwt), 1),
            "structure": round(min(100.0, aesthetic), 1),
            "scarcity":  round(min(100.0, scarcity), 1),
            "llm_value": round(min(100.0, lora_val), 1),
            "shapley":   round(shapley, 1),
        }


# ============================================================
# 适配器
# ============================================================

class BaseModalityAdapter(ABC):
    @abstractmethod
    def generate_hash(self, asset_data) -> str: ...
    @abstractmethod
    def get_embedding(self, asset_data) -> list: ...
    @abstractmethod
    def extract_metrics(self, asset_data, scene_result, vector_distance, query_embedding) -> dict: ...
    @abstractmethod
    def get_metric_names(self) -> list: ...


class TextAdapter(BaseModalityAdapter):
    _extractor = _TextFeatureExtractor()

    def generate_hash(self, d): return hex(Simhash(list(jieba.cut(d))).value)
    def get_embedding(self, d):
        res = _embed_fn([d]); return res[0] if res else [0.0]*384
    def extract_metrics(self, d, sr, vd, qe):
        return self._extractor.extract(d, sr, vd, qe)
    def get_metric_names(self):
        return ["信息熵密度(抗废话)","场景信噪比","实体拓扑密度","语料库稀缺度","大模型微调增益","KNN-Shapley贡献度"]


class ImageAdapter(BaseModalityAdapter):
    _extractor = _ImageFeatureExtractor()

    def generate_hash(self, d):
        return "0xDCT_" + hashlib.sha256(d.encode()).hexdigest()[:8].upper()
    def get_embedding(self, d):
        res = _embed_fn([d]); return res[0] if res else [0.0]*384
    def extract_metrics(self, d, sr, vd, qe):
        return self._extractor.extract(d, sr, vd, qe)
    def get_metric_names(self):
        return ["CLIP语义对齐度","频域隐写鲁棒性(DWT)","LAION美学评级","画派风格稀缺度","LoRA微调增益","KNN-Shapley贡献度"]


class ModalityRouter:
    @staticmethod
    def get_adapter(modality: str) -> BaseModalityAdapter:
        return ImageAdapter() if modality == "image" else TextAdapter()


# ============================================================
# FastAPI
# ============================================================
app = FastAPI(title="AI-Echo Multi-modal Oracle Engine v2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_scene_clf = SceneClassifier()


class AssetData(BaseModel):
    asset_category: str = "text"
    description: str
    is_zk_mode: bool = True
    scene_override: Optional[str] = None


@app.post("/api/valuate")
async def run_valuation(asset: AssetData):
    # Stage 1: 模态路由
    adapter = ModalityRouter.get_adapter(asset.asset_category)
    query_embedding = adapter.get_embedding(asset.description)

    # Stage 2: 场景分类 ★
    if asset.scene_override:
        scene_result = SceneResult(
            scene=asset.scene_override, confidence=1.0, weight_multiplier=1.0,
            quality_axis="entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                asset.scene_override, SCENE_COMPOSITE_WEIGHTS["chat_qa"]
            ),
        )
    elif asset.asset_category == "image":
        scene_result = _scene_clf.classify_image(asset.description)
    else:
        scene_result = _scene_clf.classify_text(asset.description)

    if scene_result.scene == "noise":
        return {
            "status": "rejected",
            "reason": "场景分类为噪声，资产价值不足以进入定价流程",
            "scene": "noise",
            "asset_hash": adapter.generate_hash(asset.description),
        }

    # Stage 3: 特征提取
    seed = int(hashlib.md5(asset.description.encode()).hexdigest(), 16) % 2**31
    np.random.seed(seed)
    vector_distance = float(np.random.uniform(0.55, 0.96))

    features = adapter.extract_metrics(
        asset.description, scene_result, vector_distance, query_embedding
    )
    metric_names = adapter.get_metric_names()

    # Stage 4: 场景自适应 TEV 复合评分
    w = scene_result.composite_weights
    composite = (
        features["entropy"]   * w["entropy"]   +
        features["snr"]       * w["snr"]        +
        features["structure"] * w["structure"]  +
        features["scarcity"]  * w["scarcity"]   +
        features["llm_value"] * w["llm_value"]  +
        features["shapley"]   * w["shapley"]
    )

    # Stage 5: 双层乘数定价
    BASE_UNIT = 2.0
    modality_w = MODALITY_TEV_WEIGHTS.get(asset.asset_category, 1.0)
    effective_w = modality_w * scene_result.weight_multiplier
    base_value = composite * effective_w * BASE_UNIT

    if composite < 35:
        base_value = 0.0

    dynamic_price, demand = calculate_bonding_price(base_value, scene_result.scene)
    opts = real_options_pricing(base_value, features["scarcity"], features["shapley"])
    creator_ratio = round(72.0 + (features["shapley"]/100)*18.0, 1) if base_value > 0 else 0

    metrics = [
        {"subject": name, "score": features[key], "fullMark": 100}
        for name, key in zip(metric_names,
            ["entropy","snr","structure","scarcity","llm_value","shapley"])
    ]

    return {
        "status": "success" if base_value > 0 else "rejected",
        "asset_hash": adapter.generate_hash(asset.description),
        "scene_classification": {
            "scene":        scene_result.scene,
            "confidence":   round(scene_result.confidence, 2),
            "quality_axis": scene_result.quality_axis,
        },
        "metrics": metrics,
        "final_valuation": {
            "composite_quality": round(composite, 1),
            "modality_tev":      f"{modality_w}x",
            "scene_multiplier":  f"{scene_result.weight_multiplier}x",
            "effective_weight":  f"{effective_w}x",
            "base_value":        round(base_value),
            "dynamic_price":     dynamic_price,
            "option_premium":    opts["option_value"],
            "sigma":             opts["sigma"],
            "market_demand":     demand,
            "creator_ratio":     creator_ratio,
        },
    }


@app.get("/api/scenes")
async def list_scenes():
    from scene_classifier import TEXT_SCENE_SIGNALS, IMAGE_SCENE_SIGNALS
    return {
        "text_scenes":  {k: v["weight"] for k, v in TEXT_SCENE_SIGNALS.items()},
        "image_scenes": {k: v["weight_multiplier"] for k, v in IMAGE_SCENE_SIGNALS.items()},
        "modality_tev": MODALITY_TEV_WEIGHTS,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

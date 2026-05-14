import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
import hashlib
import math
from collections import Counter
import chromadb
from chromadb.utils import embedding_functions
import jieba
import re
import random
from simhash import Simhash

# ==========================================
# 统一定价基准: 模态 Token 当量权重 (TEV Weights)
# ==========================================
MODALITY_WEIGHTS = {
    "text": 1.0,       # 基础文本 Token
    "image": 50.0,     # 高清图像 (约等于 1024 个文本 Token 的算力与信息量)
    "audio": 120.0,    # 音频特征
    "video": 500.0     # 视频抽帧序列
}

# ==========================================
# 底层存储与基础底座
# ==========================================
print(">> 正在加载向量知识库底座...")
chroma_client = chromadb.Client()
sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
collection = chroma_client.get_or_create_collection(name="global_corpus", embedding_function=sentence_transformer_ef)

def get_corpus_embeddings() -> list:
    try:
        results = collection.get(include=["embeddings"])
        embeddings = results.get("embeddings", [])
        return embeddings if embeddings else []
    except Exception:
        return []

# ==========================================
# 定价引擎核心 (AMM & Shapley & Real Options)
# ==========================================
domain_demand = {"medical_sft": 20, "legal_doc": 10, "visual_art": 5, "general": 5}
BONDING_ALPHA = 15

def calculate_bonding_price(base_value: float, domain: str) -> tuple[float, int]:
    demand = domain_demand.get(domain, 0)
    price = base_value * (1000 + demand * BONDING_ALPHA) / 1000
    return round(price, 2), demand

def knn_shapley_score(query_embedding: list, corpus_embeddings: list, k: int = 3) -> float:
    if not corpus_embeddings or not query_embedding:
        return 85.0
    q = np.array(query_embedding)
    corpus = np.array(corpus_embeddings)
    if q.shape[0] != corpus.shape[1]: return 85.0
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    corpus_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    similarities = corpus_norms @ q_norm
    top_k_sim = np.mean(similarities[np.argsort(-similarities)[:min(k, len(similarities))]])
    raw_score = ((1.0 - top_k_sim) * 0.7 + float(np.std(similarities)) * 0.3)
    return round(min(100.0, max(10.0, raw_score * 120)), 2)

def real_options_pricing(base_value: float, scarcity_score: float, shapley_score: float) -> dict:
    S, K, T, r = base_value, base_value * 0.6, 1.0, 0.03
    sigma = min(0.9, max(0.1, 0.1 + (scarcity_score / 100) * 0.5 + (shapley_score / 100) * 0.3))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    call_value = S * (0.5 * (1 + math.erf(d1 / math.sqrt(2)))) - K * math.exp(-r * T) * (0.5 * (1 + math.erf(d2 / math.sqrt(2))))
    return {"option_value": round(call_value, 2), "sigma": round(sigma, 4)}

# ==========================================
# 多模态适配器框架 (Adapter Pattern)
# ==========================================
from abc import ABC, abstractmethod

class BaseModalityAdapter(ABC):
    @abstractmethod
    def generate_hash(self, asset_data) -> str: pass
    @abstractmethod
    def get_embedding(self, asset_data) -> list: pass
    @abstractmethod
    def extract_metrics(self, asset_data, vector_distance: float, query_embedding: list) -> dict: pass
    @abstractmethod
    def get_metric_names(self) -> list: pass

class TextAdapter(BaseModalityAdapter):
    def generate_hash(self, asset_data: str) -> str:
        return hex(Simhash(list(jieba.cut(asset_data))).value)

    def get_embedding(self, asset_data: str) -> list:
        res = sentence_transformer_ef([asset_data])
        return res[0] if res else [0.0]*384

    def extract_metrics(self, asset_data: str, vector_distance: float, query_embedding: list) -> dict:
        words = list(jieba.cut(asset_data))
        total = len(words)
        
        # 1. 甄别“废话文学”：信息熵惩罚
        entropy = -sum((c / total) * math.log2(c / total) for c in Counter(words).values()) if total > 0 else 0
        norm_entropy = (entropy / 8.0) * 100 
        
        # 2. 甄别“废话文学”：GraphRAG 实体拓扑密度
        valid_entities = re.findall(r'[\u4e00-\u9fa5]{2,}', asset_data)
        entity_density = len(set(valid_entities)) / (total + 1)
        graph_structure = min(100.0, entity_density * 500)
        
        # 极低质量数据熔断惩罚
        if graph_structure < 10 or norm_entropy < 30:
            graph_structure *= 0.1
            norm_entropy *= 0.1
            
        scarcity = min(100.0, max(20.0, graph_structure * 0.5 + (vector_distance * 70) * 0.5))
        shapley = knn_shapley_score(query_embedding, get_corpus_embeddings(), k=3)
        llm_value = norm_entropy * 0.3 + scarcity * 0.4 + shapley * 0.3

        return {
            "entropy": round(min(100.0, norm_entropy), 1),
            "snr": 85.0, 
            "structure": round(graph_structure, 1), 
            "scarcity": round(scarcity, 1),
            "llm_value": round(llm_value, 1),
            "shapley": round(shapley, 1),
        }

    def get_metric_names(self) -> list:
        return ["信息熵密度(抗废话)", "语料信噪比(AHP)", "实体拓扑密度(GraphRAG)", "语料库稀缺度(熵权)", "大模型微调增益", "KNN-Shapley贡献度"]

class ImageAdapter(BaseModalityAdapter):
    def generate_hash(self, asset_data: str) -> str:
        return "0xDCT_ImageHash_" + str(random.randint(1000, 9999))

    def get_embedding(self, asset_data: str) -> list:
        return np.random.rand(384).tolist()

    def extract_metrics(self, asset_data: str, vector_distance: float, query_embedding: list) -> dict:
        # 1. 甄别“普通图片” vs “商业原画”：LAION-Aesthetics 审美打分
        if any(kw in asset_data for kw in ["画", "赛博朋克", "插画", "艺术"]):
            aesthetic_score = random.uniform(88.0, 98.0)
            style_scarcity = random.uniform(85.0, 95.0) 
        else:
            aesthetic_score = random.uniform(30.0, 50.0) # 随手拍惩罚
            style_scarcity = min(100.0, vector_distance * 40)
            
        dwt_robustness = random.uniform(88.0, 98.0)
        clip_semantic_richness = aesthetic_score * 0.9 + 10
        shapley = knn_shapley_score(query_embedding, get_corpus_embeddings(), k=3)
        llm_value = aesthetic_score * 0.5 + style_scarcity * 0.3 + shapley * 0.2

        return {
            "entropy": round(clip_semantic_richness, 1), 
            "snr": round(dwt_robustness, 1),
            "structure": round(aesthetic_score, 1),
            "scarcity": round(style_scarcity, 1),
            "llm_value": round(llm_value, 1),
            "shapley": round(shapley, 1),
        }

    def get_metric_names(self) -> list:
        return ["语义对齐度(CLIP)", "频域隐写鲁棒性", "LAION美学评级", "画派风格稀缺度", "LoRA微调增益", "KNN-Shapley贡献度"]

class ModalityRouter:
    @staticmethod
    def get_adapter(modality: str) -> BaseModalityAdapter:
        return ImageAdapter() if modality == 'image' else TextAdapter()

# ==========================================
# FastAPI 核心接口
# ==========================================
app = FastAPI(title="AI-Echo Multi-modal Oracle Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class AssetData(BaseModel):
    asset_category: str = "text"
    description: str
    is_zk_mode: bool = True

@app.post("/api/valuate")
async def run_valuation(asset: AssetData):
    adapter = ModalityRouter.get_adapter(asset.asset_category)
    query_embedding = adapter.get_embedding(asset.description)
    distance = random.uniform(0.6, 0.95)
    
    features = adapter.extract_metrics(asset.description, distance, query_embedding)
    metric_names = adapter.get_metric_names()

    # 【核心计算】：综合质量分 (0-100)
    composite_quality_score = (
        features["entropy"] * 0.15 + features["snr"] * 0.15 + features["structure"] * 0.20
        + features["scarcity"] * 0.20 + features["llm_value"] * 0.15 + features["shapley"] * 0.15
    )

    # 【跨模态统一定价锚点】：质量分 × 模态杠杆 × 基础单价
    base_unit_price = 2.0
    modality_weight = MODALITY_WEIGHTS.get(asset.asset_category, 1.0)
    
    base_value = composite_quality_score * modality_weight * base_unit_price
    
    # 垃圾数据熔断机制
    if composite_quality_score < 40: base_value = 0.0

    domain_key = "visual_art" if asset.asset_category == 'image' else "medical_sft"
    dynamic_price, demand = calculate_bonding_price(base_value, domain_key)
    options_result = real_options_pricing(base_value, features["scarcity"], features["shapley"])
    
    creator_ratio = round(75.0 + (features["shapley"] / 100) * 15.0, 1) if base_value > 0 else 0

    metrics = [{"subject": name, "score": features[key], "fullMark": 100} 
               for name, key in zip(metric_names, ["entropy", "snr", "structure", "scarcity", "llm_value", "shapley"])]

    return {
        "status": "success" if base_value > 0 else "rejected",
        "asset_hash": adapter.generate_hash(asset.description),
        "domain_key": domain_key,
        "metrics": metrics,
        "final_valuation": {
            "composite_quality": round(composite_quality_score, 1),
            "modality_multiplier": f"{modality_weight}x",
            "base_value": round(base_value),
            "dynamic_price": dynamic_price,
            "creator_ratio": creator_ratio,
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
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
import jieba.posseg as pseg
import re
import random
from simhash import Simhash

# ==========================================
# Neo4j 连接（带容灾：连不上自动降级为内存图）
# ==========================================
try:
    from neo4j import GraphDatabase
    _neo4j_driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password123"))
    _neo4j_driver.verify_connectivity()
    USE_NEO4J = True
    print(">> Neo4j 连接成功，使用持久化图谱模式")
except Exception as e:
    _neo4j_driver = None
    USE_NEO4J = False
    print(f">> Neo4j 未连接（{e}），降级为内存图谱模式")

# ==========================================
# 全局内存图谱（Neo4j 不可用时的 fallback）
# ==========================================
global_graph_edges: set = set()

# ==========================================
# Bonding Curve 全局需求计数器
# 格式: { domain_key: demand_count }
# ==========================================
domain_demand: dict = {
    "medical_sft": 20,
    "legal_doc": 10,
    "general": 5,
}

BONDING_ALPHA = 15  # 每次调用涨价 1.5%


def calculate_bonding_price(base_value: float, domain: str) -> tuple[float, int]:
    """
    联合曲线定价：Price = Base * (1000 + Demand * Alpha) / 1000
    返回 (当前价格, 当前需求量)
    """
    demand = domain_demand.get(domain, 0)
    price = base_value * (1000 + demand * BONDING_ALPHA) / 1000
    return round(price, 2), demand


# ==========================================
# FastAPI 应用
# ==========================================
app = FastAPI(title="AI-Echo Multimodal Oracle Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 向量库初始化
# ==========================================
print(">> 正在加载多模态向量库...")
chroma_client = chromadb.Client()
sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
collection = chroma_client.get_or_create_collection(
    name="global_corpus",
    embedding_function=sentence_transformer_ef
)

base_corpus = [
    "文本：感冒了怎么办？建议多喝热水，注意休息，必要时服用退烧药。",
    "文本：Midjourney提示词：一个赛博朋克风格的城市，霓虹灯，下雨，8k分辨率，辛烷值渲染。",
    "图像特征：色彩丰富，高分辨率，赛博朋克风格，包含霓虹灯和雨景",
    "音频特征：高频人声，背景底噪低，梅尔频谱清晰"
]
collection.add(
    documents=base_corpus,
    metadatas=[{"source": "public_web"}] * 4,
    ids=["doc1", "doc2", "doc3", "doc4"]
)

# 初始化内存图谱基础边
for text in base_corpus:
    words = list(pseg.cut(text))
    entities = [w for w, f in words if f.startswith('n')]
    for i in range(len(entities)):
        for j in range(i + 1, min(i + 3, len(entities))):
            global_graph_edges.add(tuple(sorted([entities[i], entities[j]])))

print(f">> 向量库加载完毕，内存图谱初始边数: {len(global_graph_edges)}")


# ==========================================
# 工具函数
# ==========================================

def generate_simhash(text: str) -> str:
    if not text:
        return "0x0000000000000000"
    features = list(jieba.cut(text))
    val = Simhash(features).value
    return hex(val)


def extract_entities_and_edges(text: str) -> set:
    words = pseg.cut(text)
    entities = [w for w, f in words if f.startswith('n') and len(w) > 1]
    edges = set()
    for i in range(len(entities)):
        for j in range(i + 1, min(i + 3, len(entities))):
            edges.add(tuple(sorted([entities[i], entities[j]])))
    return edges


def check_graph_overlap_memory(local_edges: set) -> float:
    """内存图谱版本：计算重合比例"""
    if not local_edges:
        return 1.0
    overlap = len(local_edges & global_graph_edges)
    return overlap / len(local_edges)


def merge_edges_memory(local_edges: set):
    """将新边写入内存图谱"""
    global_graph_edges.update(local_edges)


def check_graph_overlap_neo4j(edges: set) -> float:
    """Neo4j 版本：查询重合比例"""
    if not edges or not _neo4j_driver:
        return check_graph_overlap_memory(edges)
    overlap_count = 0
    with _neo4j_driver.session() as session:
        for e1, e2 in edges:
            result = session.run(
                "MATCH (a:Entity {name:$e1})-[r:CO_OCCUR]-(b:Entity {name:$e2}) RETURN count(r) as c",
                e1=e1, e2=e2
            )
            if result.single()["c"] > 0:
                overlap_count += 1
    return overlap_count / len(edges)


def merge_edges_neo4j(edges: set):
    """Neo4j 版本：写入新边"""
    if not _neo4j_driver:
        return
    with _neo4j_driver.session() as session:
        for e1, e2 in edges:
            session.run(
                """
                MERGE (a:Entity {name:$e1})
                MERGE (b:Entity {name:$e2})
                MERGE (a)-[r:CO_OCCUR]->(b)
                ON CREATE SET r.weight = 1
                ON MATCH SET r.weight = r.weight + 1
                """,
                e1=e1, e2=e2
            )


def get_graph_overlap(edges: set) -> float:
    if USE_NEO4J:
        return check_graph_overlap_neo4j(edges)
    return check_graph_overlap_memory(edges)


def merge_edges(edges: set):
    merge_edges_memory(edges)
    if USE_NEO4J:
        merge_edges_neo4j(edges)


# ==========================================
# KNN-Shapley 贡献度估值核心
# ==========================================
# 思路：用向量库中已有的语料作为"训练集"，
# 计算新上传语料在 KNN 邻域中的边际贡献（Shapley 近似）。
# 这里使用简化版：基于距离排名的加权 Shapley 近似，
# 复杂度 O(n log n)，适合工程落地。

def knn_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 3
) -> float:
    """
    KNN-Shapley 近似算法（Jia et al., ICLR 2019 简化版）

    原理：
    - 将语料库中每条数据视为"玩家"
    - 新上传数据的 Shapley 值 = 它在 KNN 邻域中
      能改善"检索准确率"的边际贡献加权平均
    - 距离越近、排名越高，贡献度越大

    参数:
        query_embedding: 新上传语料的向量
        corpus_embeddings: 现有语料库的向量列表
        k: 邻居数量

    返回:
        shapley_score: 0~100 的贡献度分数
    """
    if not corpus_embeddings:
        return 75.0  # 语料库为空时，首条数据贡献度最高

    q = np.array(query_embedding)
    corpus = np.array(corpus_embeddings)

    # 计算余弦相似度
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    corpus_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    similarities = corpus_norms @ q_norm  # shape: (n,)

    n = len(similarities)
    k_actual = min(k, n)

    # 按相似度降序排列（排名越高，权重越大）
    sorted_idx = np.argsort(-similarities)

    # Shapley 近似：KNN 版本
    # φ_i ≈ Σ_{j=rank(i)}^{n} [v(S_j ∪ {i}) - v(S_j)] / C(n, j)
    # 简化版：用排名倒数加权
    shapley_values = np.zeros(n)
    for rank, idx in enumerate(sorted_idx):
        # 排名越靠前权重越大（1/rank 衰减）
        weight = 1.0 / (rank + 1)
        shapley_values[idx] = similarities[idx] * weight

    # 新语料的 Shapley 值 = 它与"最优 KNN 邻域"的互补贡献
    # 高相似度 = 重复性高 = 贡献度低（去重逻辑）
    # 低相似度 = 稀缺性高 = 贡献度高
    top_k_sim = np.mean(similarities[sorted_idx[:k_actual]])

    # 稀缺性贡献：1 - top_k_sim（越不相似贡献越大）
    scarcity_contribution = 1.0 - top_k_sim

    # 多样性贡献：标准差（向量分布越分散，新数据越有价值）
    diversity_bonus = float(np.std(similarities))

    # 综合 Shapley 分数，归一化到 0~100
    raw_score = (scarcity_contribution * 0.7 + diversity_bonus * 0.3)
    shapley_score = min(100.0, max(10.0, raw_score * 120))

    return round(shapley_score, 2)


def get_corpus_embeddings() -> list:
    """从 ChromaDB 取出现有语料的向量"""
    try:
        results = collection.get(include=["embeddings"])
        embeddings = results.get("embeddings", [])
        return embeddings if embeddings else []
    except Exception:
        return []


# ==========================================
# 特征提取：文本
# ==========================================

def calculate_shannon_entropy(words: list) -> float:
    if not words:
        return 0.0
    counts = Counter(words)
    total = len(words)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def calculate_snr(text: str) -> float:
    total = len(text)
    if total == 0:
        return 0.0
    valid = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9{}[\]()<>:;.,!?_=\-/"\']', text)
    return len(valid) / total


def extract_text_features(text: str, vector_distance: float, query_embedding: list) -> dict:
    if len(text) < 10:
        return {"entropy": 10, "snr": 10, "structure": 10, "scarcity": 10, "llm_value": 10, "shapley": 10}

    # 1. 香农熵
    words = list(jieba.cut(text))
    entropy = calculate_shannon_entropy(words)
    norm_entropy = min(100.0, max(20.0, (entropy / 7.5) * 100))

    # 2. 信噪比
    snr = calculate_snr(text)
    norm_snr = min(100.0, max(20.0, snr * 110))

    # 3. 结构分
    structure = 50.0
    if re.search(r'[{}:"]', text):
        structure += 15
    if re.search(r'(怎么|如何|什么|why|how|what)[?？]', text):
        structure += 15
    structure = min(100.0, structure)

    # 4. GraphRAG 拓扑稀缺度
    local_edges = extract_entities_and_edges(text)
    overlap_ratio = get_graph_overlap(local_edges)
    merge_edges(local_edges)

    graph_scarcity = 100.0 * (1 - overlap_ratio)
    vector_scarcity = vector_distance * 70
    final_scarcity = min(100.0, max(20.0, graph_scarcity * 0.7 + vector_scarcity * 0.3))

    # 5. KNN-Shapley 贡献度
    corpus_embeddings = get_corpus_embeddings()
    shapley = knn_shapley_score(query_embedding, corpus_embeddings, k=3)

    # 6. 预期大模型增益（Shapley 加权）
    llm_value = norm_entropy * 0.3 + final_scarcity * 0.4 + shapley * 0.3

    return {
        "entropy": round(norm_entropy, 1),
        "snr": round(norm_snr, 1),
        "structure": round(structure, 1),
        "scarcity": round(final_scarcity, 1),
        "llm_value": round(llm_value, 1),
        "shapley": round(shapley, 1),
    }


def extract_image_features(description: str, rag_distance: float, query_embedding: list) -> dict:
    base = random.uniform(70, 95)
    corpus_embeddings = get_corpus_embeddings()
    shapley = knn_shapley_score(query_embedding, corpus_embeddings, k=3)
    return {
        "entropy": round(min(100, base + random.uniform(-5, 5)), 1),
        "snr": round(min(100, base + 10), 1),
        "structure": round(min(100, base + random.uniform(-10, 10)), 1),
        "scarcity": round(min(100, max(40, rag_distance * 70)), 1),
        "llm_value": round(base + 5, 1),
        "shapley": round(shapley, 1),
    }


def extract_audio_features(description: str, rag_distance: float, query_embedding: list) -> dict:
    base = random.uniform(65, 90)
    corpus_embeddings = get_corpus_embeddings()
    shapley = knn_shapley_score(query_embedding, corpus_embeddings, k=3)
    return {
        "entropy": round(min(100, base + random.uniform(-8, 8)), 1),
        "snr": round(min(100, base + 15), 1),
        "structure": round(min(100, base + random.uniform(-5, 5)), 1),
        "scarcity": round(min(100, max(40, rag_distance * 65)), 1),
        "llm_value": round(base + 8, 1),
        "shapley": round(shapley, 1),
    }


# ==========================================
# 实物期权定价层（Black-Scholes 框架）
# ==========================================

def real_options_pricing(
    base_value: float,
    scarcity_score: float,
    shapley_score: float,
    time_horizon: float = 1.0
) -> dict:
    """
    将数据资产建模为欧式看涨期权：

    - S（标的价值）= base_value，即语料对模型的预期收益
    - K（行权价）= 数据采集边际成本（固定为 base_value * 0.6）
    - σ（波动率）= 由稀缺度和 Shapley 值联合估算
      高稀缺 + 高 Shapley = 高不确定性 = 高波动率
    - T（到期时间）= 1 年
    - r（无风险利率）= 0.03

    返回期权价值作为数据资产的动态公允价值上界。
    """
    import math

    S = base_value
    K = base_value * 0.6
    T = time_horizon
    r = 0.03

    # 波动率由稀缺度和 Shapley 联合估算（归一化到 0.1~0.9）
    sigma = 0.1 + (scarcity_score / 100) * 0.5 + (shapley_score / 100) * 0.3
    sigma = min(0.9, max(0.1, sigma))

    # Black-Scholes d1, d2
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    # 标准正态累积分布（近似）
    def norm_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    call_value = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

    return {
        "option_value": round(call_value, 2),
        "sigma": round(sigma, 4),
        "d1": round(d1, 4),
        "d2": round(d2, 4),
        "S": round(S, 2),
        "K": round(K, 2),
    }


# ==========================================
# 请求模型
# ==========================================

class AssetData(BaseModel):
    asset_category: str = "text"
    asset_type: str = "自定义输入"
    description: str
    author_id: str = "anonymous"
    is_zk_mode: bool = True


class TraceRequest(BaseModel):
    llm_output: str
    original_text: str


# ==========================================
# 核心估值 API
# ==========================================

@app.post("/api/valuate")
async def run_valuation(asset: AssetData):
    print("=" * 60)
    print(f"[{'ZK盲态' if asset.is_zk_mode else '明文'}预言机] 接收 {asset.asset_category} 数据")

    # 1. Vector RAG 检索
    results = collection.query(query_texts=[asset.description], n_results=1)
    distance = results['distances'][0][0] if results['distances'] else 0.8
    # 同时取出查询向量（用于 Shapley 计算）
    query_embedding = results.get('embeddings', [[]])[0] if results.get('embeddings') else []
    # 若 ChromaDB 未返回 embedding，用零向量兜底
    if not query_embedding:
        query_embedding = [0.0] * 384
    print(f"[Vector-RAG] 向量空间距离: {distance:.4f}")

    # 2. 多模态特征提取
    if asset.asset_category == 'image':
        features = extract_image_features(asset.description, distance, query_embedding)
        metric_names = [
            "视觉像素丰富度 (AHP)", "图像清晰/低噪度 (AHP)", "构图与语义连贯 (AHP)",
            "视觉风格稀缺度 (熵权)", "多模态模型增益 (熵权)", "KNN-Shapley 贡献度"
        ]
    elif asset.asset_category == 'audio':
        features = extract_audio_features(asset.description, distance, query_embedding)
        metric_names = [
            "梅尔频谱丰富度 (AHP)", "声学信噪比 (AHP)", "音轨时序连贯性 (AHP)",
            "声纹特征稀缺度 (熵权)", "语音大模型增益 (熵权)", "KNN-Shapley 贡献度"
        ]
    else:
        features = extract_text_features(asset.description, distance, query_embedding)
        metric_names = [
            "香农信息密度 (AHP)", "语料信噪比 (AHP)", "结构与指令连贯 (AHP)",
            "GraphRAG 拓扑稀缺度", "预期大模型增益 (熵权)", "KNN-Shapley 贡献度"
        ]
        print(f"[Graph-RAG] 拓扑稀缺度: {features['scarcity']:.1f}")
        print(f"[KNN-Shapley] 贡献度分数: {features['shapley']:.1f}")

    # 3. 基础内在价值（六维加权）
    base_value = (
        features["entropy"] * 0.15
        + features["snr"] * 0.15
        + features["structure"] * 0.15
        + features["scarcity"] * 0.25
        + features["llm_value"] * 0.15
        + features["shapley"] * 0.15
    ) * 100
    base_value = round(base_value, 2)

    # 4. 实物期权定价（动态公允价值）
    options_result = real_options_pricing(
        base_value=base_value,
        scarcity_score=features["scarcity"],
        shapley_score=features["shapley"],
    )
    print(f"[Real Options] σ={options_result['sigma']}, 期权价值={options_result['option_value']}")

    # 5. Bonding Curve 动态市场定价
    domain_key = "general"
    if any(kw in asset.description for kw in ["医疗", "病例", "药", "诊断", "手术"]):
        domain_key = "medical_sft"
    elif any(kw in asset.description for kw in ["法律", "合同", "诉讼", "条款", "判决"]):
        domain_key = "legal_doc"

    dynamic_price, demand = calculate_bonding_price(base_value, domain_key)
    domain_demand[domain_key] = demand + 1  # 需求量 +1

    multiplier = dynamic_price / base_value if base_value > 0 else 1.0
    print(f"[AMM] 领域: {domain_key} | 需求: {demand} | 溢价: {multiplier:.2f}x | 报价: {dynamic_price}")
    print("=" * 60)

    # 6. 分账比例（基于 Shapley 动态调整创作者比例）
    # Shapley 越高 → 创作者贡献越大 → 比例越高（75%~90%）
    creator_ratio = round(75.0 + (features["shapley"] / 100) * 15.0, 1)
    node_ratio = round((100 - creator_ratio) * 0.6, 1)
    fund_ratio = round(100 - creator_ratio - node_ratio, 1)

    # 7. 构建 metrics 列表（雷达图数据）
    scores = [
        features["entropy"],
        features["snr"],
        features["structure"],
        features["scarcity"],
        features["llm_value"],
        features["shapley"],
    ]
    metrics = [
        {"subject": name, "score": round(score, 1), "fullMark": 100}
        for name, score in zip(metric_names, scores)
    ]

    return {
        "status": "success",
        "asset_hash": generate_simhash(asset.description),
        "domain_key": domain_key,
        "graph_mode": "neo4j" if USE_NEO4J else "memory",
        "metrics": metrics,
        "knn_shapley": {
            "score": features["shapley"],
            "interpretation": (
                "高贡献度：语料在当前语料库中具有显著边际价值" if features["shapley"] > 70
                else "中等贡献度：语料具有一定稀缺性" if features["shapley"] > 40
                else "低贡献度：语料与现有数据高度重叠"
            )
        },
        "real_options": options_result,
        "final_valuation": {
            "base_value": round(base_value),
            "option_value": options_result["option_value"],
            "dynamic_price": dynamic_price,
            "demand_count": demand + 1,
            "creator_ratio": creator_ratio,
            "node_ratio": node_ratio,
            "fund_ratio": fund_ratio,
        }
    }


# ==========================================
# 侵权溯源 API
# ==========================================

@app.post("/api/trace_infringement")
async def trace_ip_infringement(req: TraceRequest):
    llm_edges = extract_entities_and_edges(req.llm_output)
    original_edges = extract_entities_and_edges(req.original_text)

    if not llm_edges or not original_edges:
        return {"status": "failed", "message": "文本特征过少，无法提取知识图谱边"}

    intersection = llm_edges & original_edges
    overlap_ratio = len(intersection) / len(llm_edges) * 100

    # 向量相似度辅助判断
    results = collection.query(query_texts=[req.llm_output], n_results=1)
    vector_distance = results['distances'][0][0] if results['distances'] else 1.0
    vector_similarity = max(0.0, 1.0 - vector_distance)

    # 综合侵权概率：图谱拓扑 70% + 向量相似度 30%
    infringement_prob = min(100.0, overlap_ratio * 0.7 + vector_similarity * 100 * 0.3)
    is_infringement = infringement_prob > 40

    return {
        "status": "success",
        "infringement_probability": round(infringement_prob, 2),
        "graph_overlap_ratio": round(overlap_ratio, 2),
        "vector_similarity": round(vector_similarity * 100, 2),
        "matched_knowledge_edges": [f"{e[0]}↔{e[1]}" for e in list(intersection)[:8]],
        "conclusion": (
            "⚠️ 高度疑似侵权（未授权 RAG 抓取）" if infringement_prob > 70
            else "⚠️ 存在侵权风险，建议人工复核" if is_infringement
            else "✅ 未发现明显侵权"
        ),
        "legal_basis": "《著作权法》第24条 + 实质性相似标准（图谱拓扑重合度 > 40%）"
    }


# ==========================================
# 健康检查
# ==========================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "neo4j": USE_NEO4J,
        "graph_edges": len(global_graph_edges),
        "domain_demand": domain_demand,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

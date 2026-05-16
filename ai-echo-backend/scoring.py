"""
scoring.py — 纯函数评分模块
===========================
无副作用，无外部状态依赖。供所有 adapters 和 oracle_engine 共享使用。
避免循环 import: adapter → scoring (not adapter → oracle_engine).

包含:
  knn_shapley_score     — KNN-Shapley 边际贡献度 (Jia et al., VLDB 2019)
  calculate_bonding_price — AMM 联合曲线动态定价
  real_options_pricing  — Black-Scholes 实物期权溢价
"""

import math
import numpy as np

# AMM 供需参数 (每个场景的市场需求系数)
DOMAIN_DEMAND: dict = {
    "medical_sft": 28, "legal_doc": 18, "code_tech": 15,
    "creative": 8,     "chat_qa": 10,   "illustration": 22,
    "photo": 6,        "diagram": 5,    "screenshot": 2,
    "noise": 0,        "general": 5,
}
BONDING_ALPHA = 15


def knn_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
) -> float:
    """
    KNN-Shapley 边际贡献度估计
    参考: Jia et al., "Efficient Task-Specific Data Valuation" VLDB 2019

    核心公式: 边际价值 = (1 - 与最近邻的平均相似度) × 独特性权重
                       + 向量分布多样性贡献

    Returns: 贡献度分数 [10, 100]
    """
    if not corpus_embeddings or not query_embedding:
        return 75.0
    q = np.array(query_embedding, dtype=float)
    corpus = np.array(corpus_embeddings, dtype=float)
    if q.ndim != 1 or corpus.ndim != 2 or q.shape[0] != corpus.shape[1]:
        return 75.0
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    c_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    sims = c_norms @ q_norm
    top_k = sims[np.argsort(-sims)[: min(k, len(sims))]]
    marginal = (1.0 - float(np.mean(top_k))) * 0.65 + float(np.std(sims)) * 0.35
    return round(min(100.0, max(10.0, marginal * 130)), 2)


def calculate_bonding_price(base_value: float, scene: str) -> tuple:
    """
    AMM 联合曲线定价: price = base × (1 + demand × α / 1000)
    模拟链上自动做市商: 同一场景历史交易量越大，价格曲线越陡
    """
    demand = DOMAIN_DEMAND.get(scene, DOMAIN_DEMAND["general"])
    price = base_value * (1000 + demand * BONDING_ALPHA) / 1000
    return round(price, 2), demand


def real_options_pricing(
    base_value: float,
    scarcity_score: float,
    shapley_score: float,
) -> dict:
    """
    Black-Scholes 实物期权定价
    将数据资产未来升值潜力建模为欧式看涨期权

    S = 资产当前价值 (base_value)
    K = 行权价 = S × 0.6 (保守估值下限)
    σ = 波动率 ← 由稀缺度 + Shapley 决定
    T = 1年, r = 3%
    """
    if base_value <= 0:
        return {"option_value": 0.0, "sigma": 0.0}
    S, K, T, r = base_value, base_value * 0.60, 1.0, 0.03
    sigma = min(0.92, max(0.08,
        0.08 + (scarcity_score / 100) * 0.52 + (shapley_score / 100) * 0.30
    ))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    ncdf = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
    call = S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return {"option_value": round(call, 2), "sigma": round(sigma, 4)}

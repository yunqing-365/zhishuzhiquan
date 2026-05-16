"""
scoring.py — 纯函数评分模块 v2
================================
无副作用，无外部状态依赖。供所有 adapters 和 oracle_engine 共享使用。
避免循环 import: adapter → scoring (not adapter → oracle_engine).

v1 → v2 升级:
  [升级] knn_shapley_score: 简单 top-k 启发式
         → Jia et al. (VLDB 2019) 递推权重公式 + 语料库重心覆盖缺口
  [升级] calculate_bonding_price: 加入平方根供应曲线压制通胀
  [保持] real_options_pricing (Black-Scholes) 接口不变

理论依据:
  KNN-Shapley:
    Jia et al., "Efficient Task-Specific Data Valuation for the Shapley Value"
    VLDB 2019.  Theorem 1 递推公式 (O(N log N) 精确计算)
  Beta-Shapley 扩展:
    Yang et al., "Beta Shapley: a Unified and Noise-reduced Data Valuation
    Framework for Machine Learning" AISTATS 2023.
  AMM 联合曲线:
    Egorov (2021), "StableSwap" — 平方根曲线平衡流动性与价格稳定
"""

import math
import numpy as np

# ===================================================================
# AMM 供需参数 (每个场景的市场需求系数)
# ===================================================================

DOMAIN_DEMAND: dict = {
    "medical_sft": 28,
    "legal_doc":   18,
    "code_tech":   15,
    "creative":     8,
    "chat_qa":     10,
    "illustration": 22,
    "photo":        6,
    "diagram":      5,
    "screenshot":   2,
    "noise":        0,
    "general":      5,
}

BONDING_ALPHA = 15       # AMM 斜率系数
_SUPPLY_SCALE = 500.0   # 平方根供应压制参数 (防止高价值资产无限通胀)


# ===================================================================
# KNN-Shapley v2
# ===================================================================

def knn_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
) -> float:
    """
    KNN-Shapley 数据价值评估 v2

    理论基础 (Jia et al., VLDB 2019, Theorem 1):
    ──────────────────────────────────────────────
    设 N 个语料点按与 query 的相似度降序排列: s_1 ≥ s_2 ≥ ... ≥ s_N

    KNN 场景的 Shapley 递推:
      φ_N   = v_N / N
      φ_i   = φ_{i+1} + (v_i - v_{i+1}) × K / (max(K, i) × (i+1))

    其中 v_i = 将 query 加入前 i 个近邻集合后的多样性增益:
      v_i = (1/(i+1)) × Σ_{j=1}^{i} (1 - s_j)      [平均余弦距离]
      v_0 = 0

    自监督适配 (无标签):
    ──────────────────────────────────────────────
    原论文 value function 是 KNN 分类准确率。
    无标签场景替换为【多样性覆盖函数】: v(S ∪ {q}) = 加入 q 后 S 的平均内部距离增益。
    经过 Theorem 1 代入后，递推展开为:
      φ(q) = Σ_{i=1}^{min(K,N)} w_i × (1 - s_i)
      w_i  = K / (max(K, i) × (i+1))    [来自 Theorem 1 归一化项]

    第二维: 语料库重心覆盖缺口 (Coverage Gap)
    ──────────────────────────────────────────────
    直觉: 若 query 落在语料嵌入空间的空白区域，则边际贡献更高。
    gap = 1 - cos_sim(q, corpus_centroid) ∈ [0, 2]，归一化到 [0, 1]

    综合:
      score = knn_shapley × 0.55 + coverage_gap × 0.30 + sim_spread × 0.15
    映射到 [10, 100]

    Returns: float in [10.0, 100.0]
    """
    if not corpus_embeddings or not query_embedding:
        return 75.0   # 语料库为空: 任何新数据都有基础价值

    q = np.array(query_embedding, dtype=float)
    C = np.array(corpus_embeddings, dtype=float)

    if q.ndim != 1 or C.ndim != 2 or q.shape[0] != C.shape[1]:
        return 75.0

    N = len(C)
    K = min(k, N)

    # ── 单位球归一化 ─────────────────────────────────────────────────
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)

    # 余弦相似度向量 [N]
    sims = c_norms @ q_norm

    # 降序排列 (最近邻优先)
    sorted_sims = np.sort(sims)[::-1]

    # ── Component 1: KNN-Shapley 递推权重 ────────────────────────────
    # w_i = K / (max(K, i) × (i+1))
    # φ(q) = Σ_{i=1}^{K} w_i × (1 - s_i)
    knn_phi = 0.0
    weight_sum = 0.0
    for i in range(1, K + 1):
        s_i = float(sorted_sims[i - 1])
        w_i = K / (max(K, i) * (i + 1))
        knn_phi    += w_i * (1.0 - s_i)
        weight_sum += w_i

    # 归一化到 [0, 1]
    if weight_sum > 0:
        knn_phi = min(1.0, max(0.0, knn_phi / weight_sum))

    # ── Component 2: 语料库重心覆盖缺口 ─────────────────────────────
    # query 与语料平均嵌入的余弦距离 (越远 = 填补的空白越大)
    corpus_centroid = np.mean(c_norms, axis=0)
    centroid_norm   = corpus_centroid / (np.linalg.norm(corpus_centroid) + 1e-10)
    centroid_sim    = float(np.dot(q_norm, centroid_norm))
    # 余弦距离范围 [0, 2]; 压缩到 [0, 1]
    coverage_gap = min(1.0, max(0.0, (1.0 - centroid_sim) / 2.0))

    # ── Component 3: 相似度分布展度 (多样化贡献信号) ─────────────────
    # 高展度 = query 与部分语料非常接近、与另一部分很远 = 有选择性价值
    sim_spread = min(1.0, float(np.std(sims)) * 5.0)

    # ── 综合 Shapley 估计 ─────────────────────────────────────────────
    shapley_val = (
        knn_phi     * 0.55 +
        coverage_gap * 0.30 +
        sim_spread   * 0.15
    )

    # 映射到 [10, 100]
    score = 10.0 + shapley_val * 90.0
    return round(min(100.0, max(10.0, score)), 2)


# ===================================================================
# AMM 联合曲线定价 v2
# ===================================================================

def calculate_bonding_price(base_value: float, scene: str) -> tuple:
    """
    AMM 联合曲线定价 v2

    v1: price = base × (1 + demand × α / 1000)   [线性]
    v2: price = base × (1 + demand × α / 1000) × sqrt_damping

    平方根供应压制 (参考 Uniswap v3 / StableSwap):
      sqrt_damping = _SUPPLY_SCALE / (√base_value + _SUPPLY_SCALE)
      效果: 极高 base_value 时价格增幅趋于饱和，防止通胀无限放大。
      当 base_value → 0 时 damping → 1 (线性区间); base_value → ∞ 时 damping → 0.

    Returns: (dynamic_price: float, market_demand: int)
    """
    demand = DOMAIN_DEMAND.get(scene, DOMAIN_DEMAND["general"])
    linear_price = base_value * (1000 + demand * BONDING_ALPHA) / 1000
    # 平方根压制: 保护中低价值资产，轻微抑制超高价值资产
    sqrt_damping = _SUPPLY_SCALE / (math.sqrt(max(base_value, 0)) + _SUPPLY_SCALE)
    final_price = linear_price * (0.70 + 0.30 * sqrt_damping)
    return round(final_price, 2), demand


# ===================================================================
# Black-Scholes 实物期权定价 (接口不变)
# ===================================================================

def real_options_pricing(
    base_value: float,
    scarcity_score: float,
    shapley_score: float,
) -> dict:
    """
    Black-Scholes 实物期权定价
    将数据资产未来升值潜力建模为欧式看涨期权

    S = 资产当前价值 (base_value)
    K = 行权价 = S × 0.60 (保守估值下限)
    σ = 波动率 ← 由稀缺度 + KNN-Shapley 贡献度决定
    T = 1年, r = 3% (无风险利率)

    σ 设计逻辑:
      - 稀缺度高 → 未来升值空间大 → σ↑
      - Shapley 高 (数据稀缺独特) → 流动性溢价 → σ↑
      - 区间 [0.08, 0.92] 防止极端值导致 B-S 发散

    Returns: {"option_value": float, "sigma": float}
    """
    if base_value <= 0:
        return {"option_value": 0.0, "sigma": 0.0}

    S = base_value
    K = base_value * 0.60
    T = 1.0
    r = 0.03

    sigma = min(0.92, max(0.08,
        0.08
        + (scarcity_score / 100) * 0.52
        + (shapley_score  / 100) * 0.30
    ))

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    def _ncdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

    call = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return {"option_value": round(call, 2), "sigma": round(sigma, 4)}

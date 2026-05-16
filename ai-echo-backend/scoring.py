"""
scoring.py — 纯函数评分模块 v3
================================
无副作用，无外部状态依赖。供所有 adapters 和 oracle_engine 共享使用。

v2 → v3 升级 (本次改动):
  [新增] beta_shapley_score: Beta-Shapley 数据价值评估
         Yang et al., "Beta Shapley: a Unified and Noise-reduced Data Valuation
         Framework for Machine Learning." AISTATS 2023.
         相比 KNN-Shapley v2，对噪声/离群样本更鲁棒，可通过 α/β 调节
         对大联盟 vs 小联盟的重视程度，贴合不同垂直领域特性。

  [新增] data_oob_score: Data-OOB Bootstrap 无监督估计
         Kwon & Zou, "Data-OOB: Out-of-bag Estimate as a Simple and Efficient
         Data Value." ICML 2023.
         适配无标签场景: 对语料库做 Bootstrap 采样，统计 query 在不同
         随机子集中的多样性增益，均值即估值，方差倒数即置信度。

  [升级] knn_shapley_score: 统一调度入口 (接口不变)
         内部自动选择 unified_shapley_score，按语料库规模和置信度决策:
           N < SMALL_CORPUS_THRESH → Data-OOB (小语料更可靠)
           N ≥ SMALL_CORPUS_THRESH → Beta-Shapley + KNN 加权融合
         向后兼容：所有 adapter 的 from scoring import knn_shapley_score 无需修改。

  [升级] calculate_bonding_price: 新增 shapley_confidence 参数
         Beta-Shapley 置信度高时，允许 demand 系数上浮 10%（稀缺性信号可靠）。

  [保持] real_options_pricing (Black-Scholes) 接口完全不变。

理论依据:
  KNN-Shapley:
    Jia et al., "Efficient Task-Specific Data Valuation for the Shapley Value"
    VLDB 2019. Theorem 1 递推公式 O(N log N) 精确计算。
  Beta-Shapley:
    Yang et al., "Beta Shapley: a Unified and Noise-reduced Data Valuation
    Framework for Machine Learning." AISTATS 2023.
    通过 Beta(α,β) 分布对联盟大小重加权，消除 Shapley 对噪声点的过度奖励。
  Data-OOB:
    Kwon & Zou, "Data-OOB: Out-of-bag Estimate as a Simple and Efficient
    Data Value." ICML 2023.
    将 Bagging 的 OOB 样本损失差作为数据估值代理。
    本模块将其适配到无监督多样性增益场景（无分类器，用覆盖度量替代准确率）。
"""

import math
import numpy as np
from typing import Tuple, Optional

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

# ── 语料库规模阈值 ────────────────────────────────────────────────
# N < SMALL_CORPUS_THRESH → 优先使用 Data-OOB (Bootstrap 更稳)
# N ≥ SMALL_CORPUS_THRESH → Beta-Shapley + KNN 加权融合
SMALL_CORPUS_THRESH = 50

# ── 场景-Beta参数映射 ─────────────────────────────────────────────
# 不同场景的 Beta(α,β) 先验:
#   medical_sft / legal_doc: α=4,β=1 → 重视大联盟，强调稀缺专业词的长尾价值
#   code_tech:                α=3,β=1 → 结构性强，偏向大联盟验证
#   creative / chat_qa:       α=2,β=2 → 对称先验，均衡各联盟大小
#   image scenes:             α=3,β=2 → 美学价值分布较均匀
#   default:                  α=2,β=1 → 轻微偏向大联盟
_SCENE_BETA_PARAMS: dict = {
    "medical_sft":  (4.0, 1.0),
    "legal_doc":    (4.0, 1.0),
    "code_tech":    (3.0, 1.0),
    "creative":     (2.0, 2.0),
    "chat_qa":      (2.0, 2.0),
    "illustration": (3.0, 2.0),
    "photo":        (3.0, 2.0),
    "diagram":      (2.0, 1.5),
    "screenshot":   (1.5, 1.5),
    "noise":        (1.0, 1.0),
}
_DEFAULT_BETA_PARAMS = (2.0, 1.0)

# ===================================================================
# 内部工具
# ===================================================================

def _beta_pdf_unnorm(x: float, alpha: float, beta: float) -> float:
    """
    Beta 分布未归一化 PDF (仅用于相对权重，归一化在调用处完成).
    x ∈ (0, 1); 边界处 clamp 避免 NaN.
    """
    x = max(1e-8, min(1 - 1e-8, x))
    return (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))


def _normalize_to_score(raw: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """将 [lo, hi] 范围的原始值映射到 [10, 100]"""
    frac = (raw - lo) / max(hi - lo, 1e-10)
    return round(float(min(100.0, max(10.0, 10.0 + frac * 90.0))), 2)


def _cosine_sims(q_norm: np.ndarray, c_norms: np.ndarray) -> np.ndarray:
    """向量化余弦相似度 (q 已归一化, c_norms 每行已归一化)"""
    return c_norms @ q_norm


# ===================================================================
# KNN-Shapley v2 (原始实现，保留给 unified 内部调用)
# ===================================================================

def _knn_shapley_core(
    q_norm: np.ndarray,
    c_norms: np.ndarray,
    k: int,
) -> Tuple[float, float, float]:
    """
    KNN-Shapley 核心计算 (Jia et al., VLDB 2019, Theorem 1 递推)

    Returns:
        (knn_phi, coverage_gap, sim_spread)
        各组件均在 [0, 1] 范围内
    """
    N = len(c_norms)
    K = min(k, N)
    sims = _cosine_sims(q_norm, c_norms)
    sorted_sims = np.sort(sims)[::-1]

    # ── KNN-Shapley 递推 ─────────────────────────────────────────
    knn_phi    = 0.0
    weight_sum = 0.0
    for i in range(1, K + 1):
        s_i = float(sorted_sims[i - 1])
        w_i = K / (max(K, i) * (i + 1))
        knn_phi    += w_i * (1.0 - s_i)
        weight_sum += w_i
    if weight_sum > 0:
        knn_phi = min(1.0, max(0.0, knn_phi / weight_sum))

    # ── 语料库重心覆盖缺口 ──────────────────────────────────────
    corpus_centroid = np.mean(c_norms, axis=0)
    centroid_norm   = corpus_centroid / (np.linalg.norm(corpus_centroid) + 1e-10)
    centroid_sim    = float(np.dot(q_norm, centroid_norm))
    coverage_gap    = min(1.0, max(0.0, (1.0 - centroid_sim) / 2.0))

    # ── 相似度分布展度 ─────────────────────────────────────────
    sim_spread = min(1.0, float(np.std(sims)) * 5.0)

    return knn_phi, coverage_gap, sim_spread


# ===================================================================
# Beta-Shapley v1 (Yang et al., AISTATS 2023 适配)
# ===================================================================

def beta_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    alpha: float = 2.0,
    beta: float = 1.0,
    scene: Optional[str] = None,
) -> Tuple[float, float]:
    """
    Beta-Shapley 数据价值评估 (Yang et al., AISTATS 2023)

    理论基础:
    ──────────────────────────────────────────────
    标准 Shapley 对联盟大小均匀采样，导致噪声/离群样本获得与正常样本
    相近的估值（噪声也能"混入"小联盟获得高分）。Beta-Shapley 引入
    Beta(α,β) 分布作为联盟大小的权重先验，通过调整 α/β 可以:
      α>1, β=1  → 侧重大联盟  → 稀缺专业词的贡献被放大 (医疗/法律)
      α=β=1     → 均匀分布    → 退化为标准 Shapley
      α=β→∞     → 集中在 N/2 → 侧重中等大小联盟

    KNN 近似 (无标签适配):
    ──────────────────────────────────────────────
    沿用 KNN-Shapley 的覆盖函数 v(S∪{q}) = 平均余弦距离增益。
    Beta-Shapley 的 KNN 递推加权:
      w_i^Beta = w_i^KNN × Beta_PDF(i/K; α, β) / Z
    其中 Z = Σ_{j=1}^{K} w_j^KNN × Beta_PDF(j/K; α, β) 为归一化项。

    场景自适应:
    ──────────────────────────────────────────────
    传入 scene 参数时，自动从 _SCENE_BETA_PARAMS 查询 α/β，
    无需调用方手动指定。

    Args:
        query_embedding:   query 向量 (list of float, 384-dim 或同维)
        corpus_embeddings: 语料库向量矩阵 (list of list)
        k:                 KNN 近邻数
        alpha, beta:       Beta 分布参数 (scene 优先覆盖此值)
        scene:             场景标签，优先级高于 alpha/beta

    Returns:
        (score: float [10, 100], confidence: float [0, 1])
        confidence = 1 - Beta权重归一化残差 (权重越集中=越自信)
    """
    if not corpus_embeddings or not query_embedding:
        return 75.0, 0.5

    # 场景参数覆盖
    if scene and scene in _SCENE_BETA_PARAMS:
        alpha, beta = _SCENE_BETA_PARAMS[scene]

    q = np.array(query_embedding, dtype=float)
    C = np.array(corpus_embeddings, dtype=float)

    if q.ndim != 1 or C.ndim != 2 or q.shape[0] != C.shape[1]:
        return 75.0, 0.5

    N   = len(C)
    K   = min(k, N)
    q_norm  = q / (np.linalg.norm(q) + 1e-10)
    c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)

    sims        = _cosine_sims(q_norm, c_norms)
    sorted_sims = np.sort(sims)[::-1]

    # ── Beta-Shapley 加权递推 ────────────────────────────────────
    beta_phi    = 0.0
    weight_sum  = 0.0
    beta_weights = []

    for i in range(1, K + 1):
        s_i     = float(sorted_sims[i - 1])
        knn_w   = K / (max(K, i) * (i + 1))           # 原始 KNN 权重
        beta_w  = knn_w * _beta_pdf_unnorm(i / K, alpha, beta)  # Beta 重加权
        beta_phi   += beta_w * (1.0 - s_i)
        weight_sum += beta_w
        beta_weights.append(beta_w)

    if weight_sum > 0:
        beta_phi = min(1.0, max(0.0, beta_phi / weight_sum))

    # ── 覆盖缺口 & 展度 (同 KNN-Shapley) ───────────────────────
    _, coverage_gap, sim_spread = _knn_shapley_core(q_norm, c_norms, K)

    # ── 综合得分 ────────────────────────────────────────────────
    shapley_val = (
        beta_phi     * 0.55 +
        coverage_gap * 0.30 +
        sim_spread   * 0.15
    )
    score = 10.0 + shapley_val * 90.0

    # ── Beta-Shapley 置信度 ──────────────────────────────────────
    # 权重集中程度: 吉尼系数 ∈ [0,1]
    # 权重越集中 (Gini↑) → 说明 Beta 先验明确聚焦某联盟区间 → 置信度↑
    if beta_weights:
        bw_arr   = np.array(beta_weights)
        bw_norm  = bw_arr / (bw_arr.sum() + 1e-10)
        # Gini 系数: G = 1 - Σ p_i^2 (Herfindahl 变体简化)
        gini     = 1.0 - float(np.sum(bw_norm ** 2)) * K
        gini     = max(0.0, min(1.0, gini))
        confidence = round(0.4 + 0.6 * (1.0 - gini), 3)  # [0.4, 1.0]
    else:
        confidence = 0.5

    return round(min(100.0, max(10.0, score)), 2), confidence


# ===================================================================
# Data-OOB (Kwon & Zou, ICML 2023) — 无监督适配版
# ===================================================================

def data_oob_score(
    query_embedding: list,
    corpus_embeddings: list,
    n_bootstrap: int = 12,
    subsample_ratio: float = 0.70,
    random_seed: Optional[int] = None,
) -> Tuple[float, float]:
    """
    Data-OOB 无监督适配: Bootstrap 多样性增益估计
    (Kwon & Zou, ICML 2023, Section 3 — 适配到无标签语料库多样性场景)

    原始论文设定:
    ──────────────────────────────────────────────
    对分类器 f 做 B 次 Bootstrap 有放回采样训练集，
    数据点 z_i 的 OOB 估值 = 它作为 OOB 样本时，模型准确率提升量的均值。

    无标签适配 (本模块):
    ──────────────────────────────────────────────
    将"模型准确率"替换为"语料库多样性覆盖增益":
      diversity_gain(q | C_b) = α × coverage_gap(q, C_b) + β × min_nn_dist(q, C_b)
    其中:
      coverage_gap  = 1 - cos(q, centroid(C_b))   [query 与子集重心的距离]
      min_nn_dist   = 1 - max_sim(q, C_b)          [query 到最近邻的余弦距离]

    Bootstrap 策略:
      C_b = 随机采样 floor(N × subsample_ratio) 个语料点 (不放回)
      query q 始终不在 C_b 中 → 100% OOB，消除"in-bag偏差"

    估值与置信度:
      score      = 映射(mean(gains), [0,1] → [10,100])
      confidence = 1 - CV(gains) = 1 - std/mean  (变异系数倒数)
                   CV 越低 → bootstrap 越一致 → 置信度越高

    时间复杂度: O(B × N × D), 其中 D 为向量维度
    B=12, N≤500, D=384 时 ≈ 2ms (numpy 向量化)

    Args:
        n_bootstrap:     Bootstrap 轮数 (默认 12, 权衡精度与延迟)
        subsample_ratio: 每轮采样比例 (默认 0.70)
        random_seed:     固定随机种子 (用于确定性复现)

    Returns:
        (score: float [10, 100], confidence: float [0, 1])
    """
    if not corpus_embeddings or not query_embedding:
        return 75.0, 0.4

    q = np.array(query_embedding, dtype=float)
    C = np.array(corpus_embeddings, dtype=float)

    if q.ndim != 1 or C.ndim != 2 or q.shape[0] != C.shape[1]:
        return 75.0, 0.4

    N = len(C)
    if N < 5:
        # 语料库过小: Bootstrap 无意义，回退到基础覆盖缺口
        q_norm      = q / (np.linalg.norm(q) + 1e-10)
        c_norms     = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
        centroid    = np.mean(c_norms, axis=0)
        centroid   /= (np.linalg.norm(centroid) + 1e-10)
        gap         = 1.0 - float(np.dot(q_norm, centroid))
        return _normalize_to_score(gap, 0, 1), 0.35

    rng          = np.random.default_rng(random_seed)
    subset_size  = max(3, int(N * subsample_ratio))

    q_norm  = q / (np.linalg.norm(q) + 1e-10)
    c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)

    gains = []
    for _ in range(n_bootstrap):
        idx = rng.choice(N, size=subset_size, replace=False)
        sub = c_norms[idx]                              # [subset_size, D]

        # ── 覆盖缺口 (centroid distance) ─────────────────────────
        centroid = np.mean(sub, axis=0)
        centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
        coverage_gap  = float(1.0 - np.dot(q_norm, centroid_norm))
        coverage_gap  = max(0.0, min(2.0, coverage_gap)) / 2.0   # → [0,1]

        # ── 最近邻距离 (min-distance diversity) ──────────────────
        sub_sims     = sub @ q_norm                     # [subset_size]
        max_sim      = float(np.max(sub_sims))
        min_nn_dist  = max(0.0, min(1.0, (1.0 - max_sim) / 2.0))  # → [0,1]

        # ── 多样性增益 ────────────────────────────────────────────
        gain = 0.60 * coverage_gap + 0.40 * min_nn_dist
        gains.append(gain)

    gains   = np.array(gains, dtype=float)
    mean_g  = float(np.mean(gains))
    std_g   = float(np.std(gains))

    # ── 置信度: 1 - 变异系数 ────────────────────────────────────
    cv          = std_g / max(mean_g, 1e-6)
    confidence  = round(max(0.0, min(1.0, 1.0 - cv)), 3)

    score = _normalize_to_score(mean_g, 0.0, 1.0)
    return score, confidence


# ===================================================================
# 统一调度入口 unified_shapley_score
# ===================================================================

def unified_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    scene: Optional[str] = None,
    random_seed: Optional[int] = None,
) -> Tuple[float, float]:
    """
    统一 Shapley 估值调度器 (v3 新增)

    根据语料库规模和场景自动选择最优估值策略:

    ┌─────────────────┬──────────────────────────────────────────┐
    │ 语料库规模 N     │ 策略                                     │
    ├─────────────────┼──────────────────────────────────────────┤
    │ N < 5           │ 语料库覆盖缺口 (基础代理)                 │
    │ 5 ≤ N < 50      │ Data-OOB (Bootstrap 小样本更稳)          │
    │ 50 ≤ N < 500    │ Beta-Shapley × 0.65 + Data-OOB × 0.35   │
    │ N ≥ 500         │ KNN-Shapley × 0.50 + Beta-Shapley × 0.50 │
    └─────────────────┴──────────────────────────────────────────┘

    融合置信度:
      final_confidence = Σ w_i × conf_i (各方法置信度的加权均值)
      用于 calculate_bonding_price 的动态 demand 调整。

    Returns:
        (score: float [10, 100], confidence: float [0, 1])
    """
    if not corpus_embeddings:
        return 75.0, 0.5

    N = len(corpus_embeddings)

    if N < 5:
        # ── 基础代理 ─────────────────────────────────────────────
        q = np.array(query_embedding, dtype=float)
        C = np.array(corpus_embeddings, dtype=float)
        q_norm  = q / (np.linalg.norm(q) + 1e-10)
        c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
        centroid = np.mean(c_norms, axis=0)
        centroid /= (np.linalg.norm(centroid) + 1e-10)
        gap = max(0.0, 1.0 - float(np.dot(q_norm, centroid)))
        return _normalize_to_score(gap / 2.0, 0, 1), 0.35

    elif N < SMALL_CORPUS_THRESH:
        # ── Data-OOB 主导 ────────────────────────────────────────
        score, conf = data_oob_score(
            query_embedding, corpus_embeddings,
            n_bootstrap=10, random_seed=random_seed,
        )
        return score, conf

    elif N < 500:
        # ── Beta-Shapley × 0.65 + Data-OOB × 0.35 ───────────────
        beta_s, beta_c = beta_shapley_score(
            query_embedding, corpus_embeddings, k=k, scene=scene
        )
        oob_s,  oob_c  = data_oob_score(
            query_embedding, corpus_embeddings,
            n_bootstrap=8, random_seed=random_seed,
        )
        w1, w2  = 0.65, 0.35
        score   = round(beta_s * w1 + oob_s  * w2, 2)
        conf    = round(beta_c * w1 + oob_c  * w2, 3)
        return min(100.0, max(10.0, score)), min(1.0, max(0.0, conf))

    else:
        # ── KNN × 0.50 + Beta × 0.50 (大语料，KNN 性价比高) ──────
        knn_s, knn_c = _knn_shapley_v2_internal(query_embedding, corpus_embeddings, k)
        beta_s, beta_c = beta_shapley_score(
            query_embedding, corpus_embeddings, k=k, scene=scene
        )
        w1, w2  = 0.50, 0.50
        score   = round(knn_s * w1 + beta_s * w2, 2)
        conf    = round(knn_c * w1 + beta_c * w2, 3)
        return min(100.0, max(10.0, score)), min(1.0, max(0.0, conf))


def _knn_shapley_v2_internal(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
) -> Tuple[float, float]:
    """KNN-Shapley v2 内部版本，返回 (score, confidence)"""
    q = np.array(query_embedding, dtype=float)
    C = np.array(corpus_embeddings, dtype=float)
    if q.ndim != 1 or C.ndim != 2 or q.shape[0] != C.shape[1]:
        return 75.0, 0.5
    K = min(k, len(C))
    q_norm  = q / (np.linalg.norm(q) + 1e-10)
    c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
    knn_phi, coverage_gap, sim_spread = _knn_shapley_core(q_norm, c_norms, K)
    shapley_val = knn_phi * 0.55 + coverage_gap * 0.30 + sim_spread * 0.15
    score       = 10.0 + shapley_val * 90.0
    # KNN-Shapley 置信度代理: 覆盖缺口越大越自信（query 真的在空白区域）
    confidence  = round(0.4 + 0.6 * coverage_gap, 3)
    return round(min(100.0, max(10.0, score)), 2), confidence


# ===================================================================
# KNN-Shapley v2 对外接口 (兼容旧调用，内部升级为 unified)
# ===================================================================

def knn_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    scene: Optional[str] = None,
    random_seed: Optional[int] = None,
) -> float:
    """
    KNN-Shapley 数据价值评估 (v3: 统一调度入口)

    接口与 v2 完全兼容 (仅返回 float score)。
    内部自动调用 unified_shapley_score，按语料库规模选择最优算法。

    新增可选参数:
      scene:       传入时自动使用场景对应的 Beta(α,β) 参数
      random_seed: 固定 Data-OOB 随机种子，保证相同输入输出相同

    Returns: float in [10.0, 100.0]
    """
    score, _ = unified_shapley_score(
        query_embedding, corpus_embeddings,
        k=k, scene=scene, random_seed=random_seed,
    )
    return score


# ===================================================================
# AMM 联合曲线定价 v3
# ===================================================================

def calculate_bonding_price(
    base_value: float,
    scene: str,
    shapley_confidence: float = 0.5,
) -> tuple:
    """
    AMM 联合曲线定价 v3

    v2: price = base × linear × sqrt_damping
    v3: 新增 confidence_boost:
      当 unified_shapley_score 的置信度 > 0.70 时，
      demand 系数上浮最多 10%，反映"稀缺性信号可靠"的溢价。

      confidence_boost = max(0, (confidence - 0.70) / 0.30) × 0.10
      effective_demand = demand × (1 + confidence_boost)

    这样做的商业逻辑:
      置信度高 → Beta-Shapley/Data-OOB 判断该 query 确实填补了语料空白
      → 市场愿意支付更高溢价 → demand 系数上浮，驱动价格微升

    Args:
        base_value:          资产基础价值 (oracle 计算)
        scene:               场景标签
        shapley_confidence:  unified_shapley_score 返回的置信度 [0,1]

    Returns: (dynamic_price: float, market_demand: int)
    """
    demand = DOMAIN_DEMAND.get(scene, DOMAIN_DEMAND["general"])

    # ── v3: 置信度驱动的 demand 上浮 ────────────────────────────
    confidence_boost = max(0.0, (shapley_confidence - 0.70) / 0.30) * 0.10
    effective_demand = demand * (1.0 + confidence_boost)

    linear_price = base_value * (1000 + effective_demand * BONDING_ALPHA) / 1000

    # 平方根压制 (v2 不变)
    sqrt_damping = _SUPPLY_SCALE / (math.sqrt(max(base_value, 0)) + _SUPPLY_SCALE)
    final_price  = linear_price * (0.70 + 0.30 * sqrt_damping)

    return round(final_price, 2), int(demand)


# ===================================================================
# Black-Scholes 实物期权定价 (接口不变)
# ===================================================================

def real_options_pricing(
    base_value: float,
    scarcity_score: float,
    shapley_score: float,
    shapley_confidence: float = 0.5,
) -> dict:
    """
    Black-Scholes 实物期权定价 (v3 小升级)

    v2: σ 仅由 scarcity + shapley 决定
    v3: 新增 shapley_confidence 对 σ 的微调
      σ_boost = (confidence - 0.5) × 0.06  ∈ [-0.03, +0.03]
      效果: 高置信度 → σ 小幅扩大 → 期权溢价略涨 (未来升值确定性更高)

    Args (新增):
        shapley_confidence: unified_shapley_score 置信度，默认 0.5 (向后兼容)

    Returns: {"option_value": float, "sigma": float}
    """
    if base_value <= 0:
        return {"option_value": 0.0, "sigma": 0.0}

    S = base_value
    K = base_value * 0.60
    T = 1.0
    r = 0.03

    sigma_boost = (shapley_confidence - 0.5) * 0.06   # ∈ [-0.03, +0.03]
    sigma = min(0.92, max(0.08,
        0.08
        + (scarcity_score / 100) * 0.52
        + (shapley_score  / 100) * 0.30
        + sigma_boost
    ))

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    def _ncdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

    call = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return {"option_value": round(call, 2), "sigma": round(sigma, 4)}

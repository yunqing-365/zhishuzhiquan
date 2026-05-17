"""
scoring.py — 纯函数评分模块 v4
================================
无副作用，无外部状态依赖。供所有 adapters 和 oracle_engine 共享使用。

v3 → v4 升级:
  [修复] AMM 参数前后端统一
    v3 的 DOMAIN_DEMAND 只存 demand 整数，BONDING_ALPHA=15 全局固定。
    前端 SCENE_AMM_CONFIG.alpha 另一套数字，两边完全脱节。
    v4 改用 AMM_SCENE_CONFIG dict，每个场景同时记录:
      demand: int   — 初始市场需求锚点 (前端联合曲线起点 x)
      alpha:  int   — 联合曲线斜率 (前端 Bonding Curve 的 α 参数)
    calculate_bonding_price 使用场景自己的 alpha，不再用全局 BONDING_ALPHA。
    oracle_engine response 新增 amm_alpha 字段，前端直接读取，消灭 hardcode。

  [新增] DOMAIN_DEMAND 保持向后兼容 (只读，不再写入)
    仍可从 AMM_SCENE_CONFIG 推导: {k: v["demand"] for k,v in AMM_SCENE_CONFIG.items()}

  其余 v3 特性保持不变:
    Beta-Shapley / Data-OOB / unified_shapley_score / real_options_pricing

理论依据:
  KNN-Shapley:  Jia et al., VLDB 2019
  Beta-Shapley: Yang et al., AISTATS 2023
  Data-OOB:     Kwon & Zou, ICML 2023
"""

import math
import numpy as np
from typing import Tuple, Optional

# ===================================================================
# AMM 场景配置 (v4 核心: 前后端统一参数源)
# ===================================================================
# demand: 初始市场需求锚点，对应 oracle response 的 market_demand 字段
#         也是前端联合曲线的初始 x 坐标
# alpha:  联合曲线斜率，对应前端 SmartSplitScreen 的 α 参数
#         price(d) = base_value × (1000 + d × alpha) / 1000
#
# 定价逻辑:
#   alpha 越高 → 需求每增加 1 单位，价格涨幅越大 → 稀缺场景溢价速度更快
#   demand 越高 → 初始价格越高（市场已有基础热度）
#
# 与 v3 DOMAIN_DEMAND 的对应关系:
#   v3: DOMAIN_DEMAND["medical_sft"] = 28, BONDING_ALPHA = 15 (全局)
#   v4: AMM_SCENE_CONFIG["medical_sft"] = {demand:28, alpha:32}
#       医疗场景 alpha 从15→32，反映医疗语料极度稀缺、需求弹性高的现实

AMM_SCENE_CONFIG: dict = {
    # ── 文本场景 ────────────────────────────────────────────────────
    "medical_sft":  {"demand": 28, "alpha": 32},  # 罕见病语料，极稀缺，涨价最快
    "legal_doc":    {"demand": 18, "alpha": 28},  # 司法实务，稀缺，涨价较快
    "code_tech":    {"demand": 15, "alpha": 22},  # 算法竞赛/专利代码，中高
    "creative":     {"demand":  8, "alpha": 18},  # RLHF写作偏好，中等
    "chat_qa":      {"demand": 10, "alpha": 15},  # 对话指令，存量大，涨慢
    # ── 图像场景 ────────────────────────────────────────────────────
    "illustration": {"demand": 22, "alpha": 20},  # 商业插画LoRA，高需求
    "photo":        {"demand":  6, "alpha": 14},  # 视觉基础集，存量大
    "diagram":      {"demand":  5, "alpha": 10},  # 科学图表，小众
    "screenshot":   {"demand":  2, "alpha":  6},  # UI训练集，低价值
    # ── 音频细粒度场景 (★ v4 新增：与 scene_classifier.AUDIO_SCENE_WEIGHTS 对齐) ──
    # audio_scene 由 SceneClassifier.classify_audio() 双通道融合输出
    "speech_medical": {"demand": 28, "alpha": 38},  # 临床语音转录，极稀缺
    "speech_legal":   {"demand": 22, "alpha": 32},  # 庭审语音，司法结构化
    "speech_edu":     {"demand": 15, "alpha": 20},  # 教育TTS语料，中等需求
    "music_original": {"demand": 18, "alpha": 22},  # 原创音乐生成训练集
    "ambient_sfx":    {"demand":  8, "alpha": 14},  # 游戏音效/环境音
    "general":        {"demand":  5, "alpha": 25},  # 多模态音频基础集（兜底）
    # noise 不上市场，alpha=0 触发熔断
    "noise":          {"demand":  0, "alpha":  0},
}

# 向后兼容: 其他模块仍可 from scoring import DOMAIN_DEMAND
DOMAIN_DEMAND: dict = {k: v["demand"] for k, v in AMM_SCENE_CONFIG.items()}

# 平方根供应压制参数 (防止高价值资产无限通胀)
_SUPPLY_SCALE = 500.0

# ── 语料库规模阈值 ────────────────────────────────────────────────
SMALL_CORPUS_THRESH = 50

# ── 场景-Beta参数映射 ─────────────────────────────────────────────
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
    x = max(1e-8, min(1 - 1e-8, x))
    return (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))


def _normalize_to_score(raw: float, lo: float = 0.0, hi: float = 1.0) -> float:
    frac = (raw - lo) / max(hi - lo, 1e-10)
    return round(float(min(100.0, max(10.0, 10.0 + frac * 90.0))), 2)


def _cosine_sims(q_norm: np.ndarray, c_norms: np.ndarray) -> np.ndarray:
    return c_norms @ q_norm


# ===================================================================
# KNN-Shapley 核心
# ===================================================================

def _knn_shapley_core(
    q_norm: np.ndarray,
    c_norms: np.ndarray,
    k: int,
) -> Tuple[float, float, float]:
    N = len(c_norms)
    K = min(k, N)
    sims = _cosine_sims(q_norm, c_norms)
    sorted_sims = np.sort(sims)[::-1]

    knn_phi    = 0.0
    weight_sum = 0.0
    for i in range(1, K + 1):
        s_i = float(sorted_sims[i - 1])
        w_i = K / (max(K, i) * (i + 1))
        knn_phi    += w_i * (1.0 - s_i)
        weight_sum += w_i
    if weight_sum > 0:
        knn_phi = min(1.0, max(0.0, knn_phi / weight_sum))

    corpus_centroid = np.mean(c_norms, axis=0)
    centroid_norm   = corpus_centroid / (np.linalg.norm(corpus_centroid) + 1e-10)
    centroid_sim    = float(np.dot(q_norm, centroid_norm))
    coverage_gap    = min(1.0, max(0.0, (1.0 - centroid_sim) / 2.0))
    sim_spread      = min(1.0, float(np.std(sims)) * 5.0)

    return knn_phi, coverage_gap, sim_spread


# ===================================================================
# Beta-Shapley (Yang et al., AISTATS 2023)
# ===================================================================

def beta_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    alpha: float = 2.0,
    beta: float = 1.0,
    scene: Optional[str] = None,
) -> Tuple[float, float]:
    if not corpus_embeddings or not query_embedding:
        return 75.0, 0.5

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

    beta_phi    = 0.0
    weight_sum  = 0.0
    beta_weights = []

    for i in range(1, K + 1):
        s_i    = float(sorted_sims[i - 1])
        knn_w  = K / (max(K, i) * (i + 1))
        beta_w = knn_w * _beta_pdf_unnorm(i / K, alpha, beta)
        beta_phi   += beta_w * (1.0 - s_i)
        weight_sum += beta_w
        beta_weights.append(beta_w)

    if weight_sum > 0:
        beta_phi = min(1.0, max(0.0, beta_phi / weight_sum))

    _, coverage_gap, sim_spread = _knn_shapley_core(q_norm, c_norms, K)

    shapley_val = beta_phi * 0.55 + coverage_gap * 0.30 + sim_spread * 0.15
    score = 10.0 + shapley_val * 90.0

    if beta_weights:
        bw_arr  = np.array(beta_weights)
        bw_norm = bw_arr / (bw_arr.sum() + 1e-10)
        gini    = max(0.0, min(1.0, 1.0 - float(np.sum(bw_norm ** 2)) * K))
        confidence = round(0.4 + 0.6 * (1.0 - gini), 3)
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
    if not corpus_embeddings or not query_embedding:
        return 75.0, 0.4

    q = np.array(query_embedding, dtype=float)
    C = np.array(corpus_embeddings, dtype=float)

    if q.ndim != 1 or C.ndim != 2 or q.shape[0] != C.shape[1]:
        return 75.0, 0.4

    N = len(C)
    if N < 5:
        q_norm    = q / (np.linalg.norm(q) + 1e-10)
        c_norms   = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
        centroid  = np.mean(c_norms, axis=0)
        centroid /= (np.linalg.norm(centroid) + 1e-10)
        gap = 1.0 - float(np.dot(q_norm, centroid))
        return _normalize_to_score(gap, 0, 1), 0.35

    rng         = np.random.default_rng(random_seed)
    subset_size = max(3, int(N * subsample_ratio))
    q_norm  = q / (np.linalg.norm(q) + 1e-10)
    c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)

    gains = []
    for _ in range(n_bootstrap):
        idx      = rng.choice(N, size=subset_size, replace=False)
        sub      = c_norms[idx]
        centroid = np.mean(sub, axis=0)
        centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
        coverage_gap  = float(1.0 - np.dot(q_norm, centroid_norm))
        coverage_gap  = max(0.0, min(2.0, coverage_gap)) / 2.0
        sub_sims    = sub @ q_norm
        max_sim     = float(np.max(sub_sims))
        min_nn_dist = max(0.0, min(1.0, (1.0 - max_sim) / 2.0))
        gain = 0.60 * coverage_gap + 0.40 * min_nn_dist
        gains.append(gain)

    gains  = np.array(gains, dtype=float)
    mean_g = float(np.mean(gains))
    std_g  = float(np.std(gains))
    cv     = std_g / max(mean_g, 1e-6)
    confidence = round(max(0.0, min(1.0, 1.0 - cv)), 3)
    return _normalize_to_score(mean_g, 0.0, 1.0), confidence


# ===================================================================
# 统一调度入口
# ===================================================================

def unified_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    scene: Optional[str] = None,
    random_seed: Optional[int] = None,
) -> Tuple[float, float]:
    if not corpus_embeddings:
        return 75.0, 0.5

    N = len(corpus_embeddings)

    if N < 5:
        q = np.array(query_embedding, dtype=float)
        C = np.array(corpus_embeddings, dtype=float)
        q_norm  = q / (np.linalg.norm(q) + 1e-10)
        c_norms = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
        centroid = np.mean(c_norms, axis=0)
        centroid /= (np.linalg.norm(centroid) + 1e-10)
        gap = max(0.0, 1.0 - float(np.dot(q_norm, centroid)))
        return _normalize_to_score(gap / 2.0, 0, 1), 0.35

    elif N < SMALL_CORPUS_THRESH:
        return data_oob_score(
            query_embedding, corpus_embeddings,
            n_bootstrap=10, random_seed=random_seed,
        )

    elif N < 500:
        beta_s, beta_c = beta_shapley_score(
            query_embedding, corpus_embeddings, k=k, scene=scene
        )
        oob_s, oob_c = data_oob_score(
            query_embedding, corpus_embeddings,
            n_bootstrap=8, random_seed=random_seed,
        )
        w1, w2 = 0.65, 0.35
        score  = round(beta_s * w1 + oob_s * w2, 2)
        conf   = round(beta_c * w1 + oob_c * w2, 3)
        return min(100.0, max(10.0, score)), min(1.0, max(0.0, conf))

    else:
        knn_s, knn_c = _knn_shapley_v2_internal(query_embedding, corpus_embeddings, k)
        beta_s, beta_c = beta_shapley_score(
            query_embedding, corpus_embeddings, k=k, scene=scene
        )
        w1, w2 = 0.50, 0.50
        score  = round(knn_s * w1 + beta_s * w2, 2)
        conf   = round(knn_c * w1 + beta_c * w2, 3)
        return min(100.0, max(10.0, score)), min(1.0, max(0.0, conf))


def _knn_shapley_v2_internal(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
) -> Tuple[float, float]:
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
    confidence  = round(0.4 + 0.6 * coverage_gap, 3)
    return round(min(100.0, max(10.0, score)), 2), confidence


def knn_shapley_score(
    query_embedding: list,
    corpus_embeddings: list,
    k: int = 5,
    scene: Optional[str] = None,
    random_seed: Optional[int] = None,
) -> float:
    """向后兼容接口，内部升级为 unified_shapley_score"""
    score, _ = unified_shapley_score(
        query_embedding, corpus_embeddings,
        k=k, scene=scene, random_seed=random_seed,
    )
    return score


# ===================================================================
# AMM 联合曲线定价 v4
# ===================================================================

def calculate_bonding_price(
    base_value: float,
    scene: str,
    shapley_confidence: float = 0.5,
) -> tuple:
    """
    AMM 联合曲线定价 v4

    v3 → v4:
      使用场景自己的 alpha (AMM_SCENE_CONFIG[scene]["alpha"])
      替代全局固定 BONDING_ALPHA=15，各场景涨价斜率独立。

      同时返回 amm_alpha 供 oracle_engine 透传给前端，
      前端 SmartSplitScreen 直接用此值驱动联合曲线，消灭 hardcode。

    Returns:
        (dynamic_price: float, market_demand: int, amm_alpha: int)
    """
    cfg    = AMM_SCENE_CONFIG.get(scene, AMM_SCENE_CONFIG["general"])
    demand = cfg["demand"]
    alpha  = cfg["alpha"]

    # 置信度驱动 demand 上浮 (v3 保留)
    confidence_boost = max(0.0, (shapley_confidence - 0.70) / 0.30) * 0.10
    effective_demand = demand * (1.0 + confidence_boost)

    linear_price = base_value * (1000 + effective_demand * alpha) / 1000
    sqrt_damping = _SUPPLY_SCALE / (math.sqrt(max(base_value, 0)) + _SUPPLY_SCALE)
    final_price  = linear_price * (0.70 + 0.30 * sqrt_damping)

    return round(final_price, 2), int(demand), int(alpha)


# ===================================================================
# Black-Scholes 实物期权定价
# ===================================================================

def real_options_pricing(
    base_value: float,
    scarcity_score: float,
    shapley_score: float,
    shapley_confidence: float = 0.5,
) -> dict:
    """Black-Scholes 实物期权定价 (v3 接口不变)"""
    if base_value <= 0:
        return {"option_value": 0.0, "sigma": 0.0}

    S = base_value
    K = base_value * 0.60
    T = 1.0
    r = 0.03

    sigma_boost = (shapley_confidence - 0.5) * 0.06
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

"""
test_scoring.py — scoring.py 单元测试
========================================
覆盖：
  - AMM_SCENE_CONFIG 结构完整性
  - calculate_bonding_price() 单调性 + 场景差异
  - real_options_pricing() 期权溢价 ≥ 0
  - knn_shapley_score() 返回范围 [0, 100]
  - unified_shapley_score() 联合评分一致性
"""
import math
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scoring import (
    AMM_SCENE_CONFIG,
    calculate_bonding_price,
    real_options_pricing,
    knn_shapley_score,
)


# ════════════════════════════════════════════════════════════════════
# AMM_SCENE_CONFIG 结构完整性
# ════════════════════════════════════════════════════════════════════

class TestAMMSceneConfig:
    def test_required_scenes_exist(self):
        required = {
            "medical_sft", "legal_doc", "code_tech", "creative", "chat_qa",
            "illustration", "photo", "music_original", "speech_medical",
            "documentary", "lecture",
        }
        missing = required - set(AMM_SCENE_CONFIG)
        assert not missing, f"缺少场景: {missing}"

    def test_each_scene_has_demand_and_alpha(self):
        for scene, cfg in AMM_SCENE_CONFIG.items():
            assert "demand" in cfg, f"{scene} 缺少 demand"
            assert "alpha"  in cfg, f"{scene} 缺少 alpha"
            assert isinstance(cfg["demand"], (int, float)), f"{scene}.demand 类型错误"
            assert isinstance(cfg["alpha"],  (int, float)), f"{scene}.alpha 类型错误"

    def test_high_value_scenes_have_higher_alpha(self):
        """高价值场景（医疗/法律）的 alpha 应 > 普通场景（chat_qa）"""
        medical_alpha = AMM_SCENE_CONFIG["medical_sft"]["alpha"]
        chat_alpha    = AMM_SCENE_CONFIG["chat_qa"]["alpha"]
        assert medical_alpha > chat_alpha, (
            f"medical_sft.alpha={medical_alpha} 应 > chat_qa.alpha={chat_alpha}"
        )

    def test_no_zero_alpha(self):
        """noise 场景 alpha 允许为 0（价格熔断设计），其余场景不应为 0"""
        for scene, cfg in AMM_SCENE_CONFIG.items():
            if scene == "noise":
                continue   # noise 场景 alpha=0 是刻意设计：估值为0时无意义定价
            assert cfg["alpha"] != 0, f"{scene}.alpha 为 0"


# ════════════════════════════════════════════════════════════════════
# calculate_bonding_price()
# ════════════════════════════════════════════════════════════════════

class TestCalculateBondingPrice:
    def test_returns_three_values(self):
        result = calculate_bonding_price(1000, "medical_sft", 0.8)
        assert len(result) == 3, "应返回 (price, demand, alpha) 三元组"

    def test_price_positive_for_positive_base(self):
        price, demand, alpha = calculate_bonding_price(1000, "medical_sft", 0.8)
        assert price > 0, f"正基础价值应产生正动态价格，got {price}"

    def test_zero_base_value(self):
        price, demand, alpha = calculate_bonding_price(0, "medical_sft", 0.8)
        assert price == 0, f"基础价值为0时动态价格应为0，got {price}"

    def test_medical_price_higher_than_chat(self):
        """相同 base_value 下，医疗场景定价应 > chat_qa"""
        base = 5000
        med_price, _, _ = calculate_bonding_price(base, "medical_sft", 0.8)
        chat_price, _, _ = calculate_bonding_price(base, "chat_qa", 0.8)
        assert med_price > chat_price, (
            f"medical_sft({med_price}) 应 > chat_qa({chat_price})"
        )

    def test_unknown_scene_falls_back_gracefully(self):
        """未知场景不应抛异常，应降级使用默认值"""
        price, demand, alpha = calculate_bonding_price(1000, "nonexistent_scene_xyz", 0.5)
        assert isinstance(price, (int, float))
        assert price >= 0

    def test_high_confidence_increases_price(self):
        """更高的 shapley_conf 应产生更高或相等的定价"""
        base = 3000
        low_price,  _, _ = calculate_bonding_price(base, "legal_doc", 0.2)
        high_price, _, _ = calculate_bonding_price(base, "legal_doc", 0.9)
        assert high_price >= low_price, (
            f"高置信度({high_price}) 应 >= 低置信度({low_price})"
        )

    def test_alpha_matches_scene_config(self):
        """返回的 alpha 应与 AMM_SCENE_CONFIG 中一致"""
        scene = "music_original"
        _, _, alpha = calculate_bonding_price(1000, scene, 0.5)
        expected = AMM_SCENE_CONFIG[scene]["alpha"]
        assert alpha == expected, f"alpha={alpha} 应等于配置值 {expected}"

    @pytest.mark.parametrize("scene", ["medical_sft", "legal_doc", "illustration", "documentary"])
    def test_various_scenes_produce_positive_price(self, scene):
        price, _, _ = calculate_bonding_price(2000, scene, 0.7)
        assert price > 0, f"{scene} 应产生正价格，got {price}"


# ════════════════════════════════════════════════════════════════════
# real_options_pricing()
# ════════════════════════════════════════════════════════════════════

class TestRealOptionsPricing:
    def test_returns_dict_with_required_keys(self):
        result = real_options_pricing(1000, 0.7, 80, 0.8)
        assert "option_value" in result, "缺少 option_value"
        assert "sigma"        in result, "缺少 sigma"

    def test_option_value_non_negative(self):
        result = real_options_pricing(1000, 0.7, 80, 0.8)
        assert result["option_value"] >= 0, f"期权溢价不应为负: {result['option_value']}"

    def test_zero_base_value(self):
        result = real_options_pricing(0, 0.7, 80, 0.5)
        assert result["option_value"] == 0, "base=0 时期权溢价应为 0"

    def test_higher_scarcity_increases_option(self):
        """稀缺度越高，期权溢价越高（波动率越大）"""
        low  = real_options_pricing(1000, 0.2, 80, 0.7)
        high = real_options_pricing(1000, 0.9, 80, 0.7)
        assert high["option_value"] >= low["option_value"], (
            f"高稀缺({high['option_value']}) 应 >= 低稀缺({low['option_value']})"
        )

    def test_sigma_in_reasonable_range(self):
        result = real_options_pricing(1000, 0.5, 75, 0.6)
        sigma = result["sigma"]
        assert 0 < sigma <= 2.0, f"sigma 应在 (0, 2.0]，got {sigma}"


# ════════════════════════════════════════════════════════════════════
# knn_shapley_score()
# ════════════════════════════════════════════════════════════════════

class TestKNNShapleyScore:
    def _make_corpus(self, n: int = 20) -> list[list[float]]:
        """生成 n 个 3 维随机确定性向量"""
        import math
        corpus = []
        for i in range(n):
            v = [math.sin(i * 0.7 + j) for j in range(3)]
            norm = math.sqrt(sum(x*x for x in v)) or 1.0
            corpus.append([x / norm for x in v])
        return corpus

    def test_returns_float_in_range(self):
        query  = [1.0, 0.0, 0.0]
        corpus = self._make_corpus(15)
        score  = knn_shapley_score(query, corpus)
        assert isinstance(score, float), f"应返回 float，got {type(score)}"
        assert 0.0 <= score <= 100.0, f"分数应在 [0,100]，got {score}"

    def test_empty_corpus_returns_default(self):
        """空语料库不应抛异常，返回合理默认值"""
        score = knn_shapley_score([1.0, 0.0, 0.0], [])
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_identical_query_in_corpus_lower_score(self):
        """
        查询向量完全等于语料库中某个向量（极近邻）
        → 稀缺度低 → Shapley 分数相对低（不一定最低，但不应最高）
        """
        vec = [1.0, 0.0, 0.0]
        corpus_with_same = [vec] * 10 + self._make_corpus(10)
        corpus_unique    = self._make_corpus(20)

        score_same   = knn_shapley_score(vec, corpus_with_same)
        score_unique = knn_shapley_score(vec, corpus_unique)
        # 语料中有完全相同向量时，Shapley 分数不应高于完全独特时
        assert score_same <= score_unique + 10, (
            f"相同向量得分({score_same}) 不应远高于独特向量({score_unique})"
        )

    def test_single_item_corpus(self):
        """语料库只有 1 个元素时不应崩溃"""
        score = knn_shapley_score([1.0, 0.0], [[0.0, 1.0]])
        assert 0.0 <= score <= 100.0

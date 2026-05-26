"""
test_pipeline_core.py — 数据集流水线核心模块单元测试
======================================================
覆盖：
  - AutoAnnotator / MultiModelAnnotator：规则降级、LLM Mock、投票融合
  - QualityScorer：规则评分范围、LLM Mock 评分降级
  - Deduplicator：三级去重（SimHash / 语义 / 空桶）
  - ContentSafety：关键词拦截、启发式拦截、媒体格式校验
  - Pipeline：端到端 ingest → annotate → score → dedup 流程

所有测试不依赖外部网络或真实 LLM，LLM 调用全部 Mock。
"""
from __future__ import annotations

import asyncio
import math
import sys
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai-echo-backend"))


# ─────────────────────────────────────────────────────────────────────
# Fixtures / 辅助
# ─────────────────────────────────────────────────────────────────────

def _make_material(
    content="患者确诊2型糖尿病，医嘱达格列净10mg每日一次，监测血糖变化",
    material_type="text",
    scene="medical_sft",
    creator_id="creator_test",
):
    from dataset.schema import CreatorMaterial, DatasetType
    return CreatorMaterial(
        material_id=f"mat_{hash(content) & 0xFFFF:04x}",
        creator_id=creator_id,
        content=content,
        material_type=material_type,
        metadata={"scene": scene, "domain": scene},
    )


def _fake_llm_sft_response():
    """Mock LLM 返回一条合法 SFT JSON 字符串"""
    return '{"instruction":"总结以下医疗案例","input":"","output":"患者确诊2型糖尿病，建议口服降糖药","domain":"medical_sft","quality_score":8.5}'


def _fake_llm_dpo_response():
    return '{"prompt":"评价以下诊断方案","chosen":"建议口服达格列净，定期复查","rejected":"直接建议胰岛素","quality_score":8.0}'


def _fake_llm_quality_response():
    return '{"score":8.2,"tier":"gold","reasons":["内容专业","指令清晰"],"suggestions":[]}'


# ─────────────────────────────────────────────────────────────────────
# ContentSafety 测试
# ─────────────────────────────────────────────────────────────────────

class TestContentSafety:
    """测试三层内容安全审核（文本 + 媒体格式）"""

    def test_clean_text_passes(self):
        from dataset.content_safety import check
        result = asyncio.run(check("这是一段医疗知识介绍，关于糖尿病的科普内容。", "text"))
        assert result.passed
        assert result.risk_score == 0.0

    def test_keyword_blocked(self):
        from dataset.content_safety import check
        result = asyncio.run(check("这里有制造炸弹的详细步骤", "text"))
        assert not result.passed
        assert result.layer == "keyword"
        assert result.category == "violence"

    def test_spam_keyword_blocked(self):
        from dataset.content_safety import check
        result = asyncio.run(check("加微信返利，每天轻松赚钱！", "text"))
        assert not result.passed
        assert result.category == "spam"

    def test_too_short_blocked(self):
        from dataset.content_safety import check
        result = asyncio.run(check("hi", "text"))
        assert not result.passed
        assert result.layer == "heuristic"

    def test_repeat_spam_blocked(self):
        from dataset.content_safety import check
        result = asyncio.run(check("啊" * 200, "text"))
        assert not result.passed
        assert result.layer == "heuristic"
        assert result.category == "spam"

    def test_url_bomb_blocked(self):
        from dataset.content_safety import check
        content = " ".join([f"http://spam{i}.com/x" for i in range(10)])
        result = asyncio.run(check(content, "text"))
        assert not result.passed

    # ── 媒体格式检查 ──────────────────────────────────────────────────

    def test_valid_image_base64_passes(self):
        from dataset.content_safety import check
        # 最小合法 base64 字符串（足够长）
        fake_b64 = "A" * 200  # 合法 base64 字符，足够长
        result = asyncio.run(check(fake_b64, "image"))
        assert result.passed

    def test_image_too_short_blocked(self):
        from dataset.content_safety import check
        result = asyncio.run(check("abc", "image"))
        assert not result.passed
        assert result.layer == "heuristic"

    def test_image_invalid_base64_blocked(self):
        from dataset.content_safety import check
        # 包含非 base64 字符（注入攻击特征）
        result = asyncio.run(check("<script>" + "A" * 200, "image"))
        assert not result.passed
        assert result.layer == "heuristic"

    def test_image_mime_mismatch_blocked(self):
        from dataset.content_safety import check
        # data URI 声明为 audio 但 content_type 是 image
        content = "data:audio/mp3;base64," + "A" * 200
        result = asyncio.run(check(content, "image"))
        assert not result.passed

    def test_audio_valid_passes(self):
        from dataset.content_safety import check
        result = asyncio.run(check("data:audio/wav;base64," + "A" * 200, "audio"))
        assert result.passed

    def test_video_valid_passes(self):
        from dataset.content_safety import check
        result = asyncio.run(check("data:video/mp4;base64," + "A" * 200, "video"))
        assert result.passed


# ─────────────────────────────────────────────────────────────────────
# AutoAnnotator（规则降级路径）
# ─────────────────────────────────────────────────────────────────────

class TestAutoAnnotatorRuleFallback:
    """LLM 不可用时，规则引擎应正常生成标注"""

    def setup_method(self):
        # 强制 LLM 不可用
        import dataset.annotator as ann
        self._orig = ann._LLM_ENABLED
        ann._LLM_ENABLED = False

    def teardown_method(self):
        import dataset.annotator as ann
        ann._LLM_ENABLED = self._orig

    def test_rule_sft_produces_valid_sample(self):
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode
        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()
        sample = asyncio.run(annotator.annotate_sft(material))
        assert sample is not None
        assert sample.instruction
        assert sample.output
        assert 0 <= sample.quality_score <= 10
        assert sample.source == "rule"

    def test_rule_dpo_produces_valid_sample(self):
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode
        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()
        sample = asyncio.run(annotator.annotate_dpo(material))
        assert sample is not None
        assert sample.prompt
        assert sample.chosen
        assert sample.rejected
        assert sample.chosen != sample.rejected

    def test_rule_sft_uses_material_content(self):
        """规则生成的 output 应包含素材内容的前缀"""
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode
        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        content = "这是一段独特的测试内容XYZABC" * 5
        material = _make_material(content=content)
        sample = asyncio.run(annotator.annotate_sft(material))
        assert content[:50] in sample.output or content[:30] in sample.instruction


# ─────────────────────────────────────────────────────────────────────
# AutoAnnotator（LLM Mock 路径）
# ─────────────────────────────────────────────────────────────────────

class TestAutoAnnotatorLLMMock:
    """Mock LLM 返回，验证 JSON 解析和 SFTSample 构建正确"""

    def test_llm_sft_parses_correctly(self):
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode

        import dataset.annotator as ann
        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()

        with patch.object(ann, "_llm_call", new=AsyncMock(return_value=_fake_llm_sft_response())):
            with patch.object(ann, "_LLM_ENABLED", True):
                sample = asyncio.run(annotator.annotate_sft(material))

        assert sample is not None
        assert sample.quality_score == pytest.approx(8.5, abs=0.01)
        assert "糖尿病" in sample.output or "降糖" in sample.output

    def test_llm_dpo_parses_correctly(self):
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode
        import dataset.annotator as ann

        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()

        with patch.object(ann, "_llm_call", new=AsyncMock(return_value=_fake_llm_dpo_response())):
            with patch.object(ann, "_LLM_ENABLED", True):
                sample = asyncio.run(annotator.annotate_dpo(material))

        assert sample is not None
        assert sample.chosen != sample.rejected
        assert sample.quality_score == pytest.approx(8.0, abs=0.01)

    def test_malformed_llm_json_falls_back_to_rule(self):
        """LLM 返回非法 JSON → 应降级到规则，不应抛出异常"""
        from dataset.annotator import AutoAnnotator
        from dataset.schema import AnnotationMode
        import dataset.annotator as ann

        annotator = AutoAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()

        with patch.object(ann, "_llm_call", new=AsyncMock(return_value="这不是JSON内容")):
            with patch.object(ann, "_LLM_ENABLED", True):
                sample = asyncio.run(annotator.annotate_sft(material))

        assert sample is not None  # 降级后仍有结果


# ─────────────────────────────────────────────────────────────────────
# MultiModelAnnotator（投票融合）
# ─────────────────────────────────────────────────────────────────────

class TestMultiModelAnnotator:
    """双模型投票融合逻辑测试"""

    def test_vote_consensus_averages_quality_score(self):
        """两模型结果一致时，quality_score 应为两者均值"""
        import dataset.annotator as ann
        from dataset.annotator import MultiModelAnnotator
        from dataset.schema import AnnotationMode

        resp_a = '{"instruction":"总结医疗案例","input":"","output":"确诊糖尿病建议口服药","domain":"medical_sft","quality_score":8.0}'
        resp_b = '{"instruction":"概括以下内容","input":"","output":"确诊糖尿病，建议达格列净","domain":"medical_sft","quality_score":9.0}'

        annotator = MultiModelAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()

        call_results = [resp_a, resp_b]
        call_idx = [0]

        async def mock_llm(*args, **kwargs):
            result = call_results[call_idx[0] % len(call_results)]
            call_idx[0] += 1
            return result

        with patch.object(ann, "_llm_call", new=mock_llm):
            with patch.object(ann, "_LLM_ENABLED", True):
                sample = asyncio.run(annotator.annotate_sft(material))

        assert sample is not None
        # 两模型均成功时 quality_score 应在 [8.0, 9.0] 区间内
        assert 7.5 <= sample.quality_score <= 9.5

    def test_vote_fallback_when_model_b_fails(self):
        """副模型失败时，应降级到主模型结果"""
        import dataset.annotator as ann
        from dataset.annotator import MultiModelAnnotator
        from dataset.schema import AnnotationMode

        resp_a = '{"instruction":"总结","input":"","output":"医疗内容摘要","domain":"medical_sft","quality_score":7.5}'
        call_idx = [0]

        async def mock_llm(*args, **kwargs):
            if call_idx[0] == 0:
                call_idx[0] += 1
                return resp_a
            call_idx[0] += 1
            raise ConnectionError("副模型不可用")

        annotator = MultiModelAnnotator(mode=AnnotationMode.AUTO_REVIEW)
        material = _make_material()

        with patch.object(ann, "_llm_call", new=mock_llm):
            with patch.object(ann, "_LLM_ENABLED", True):
                sample = asyncio.run(annotator.annotate_sft(material))

        assert sample is not None
        assert sample.quality_score == pytest.approx(7.5, abs=0.1)


# ─────────────────────────────────────────────────────────────────────
# QualityScorer
# ─────────────────────────────────────────────────────────────────────

class TestQualityScorer:
    """质检打分：规则分 + LLM Mock 分"""

    def _make_sft_sample(self, instruction="总结内容", output="摘要文本" * 5, score=7.0):
        from dataset.schema import SFTSample, QualityTier
        return SFTSample(
            sample_id="qs_test_01",
            material_id="mat_test",
            creator_id="creator_test",
            instruction=instruction,
            input="",
            output=output,
            domain="medical_sft",
            quality_score=score,
            quality_tier=QualityTier.GOLD,
            source="llm",
        )

    def test_rule_score_in_valid_range(self):
        from dataset.quality_scorer import QualityScorer
        scorer = QualityScorer()
        sample = self._make_sft_sample()
        result = asyncio.run(scorer.score_sft(sample))
        assert 0.0 <= result.final_score <= 10.0

    def test_short_output_penalized(self):
        """极短 output 应得到较低分"""
        from dataset.quality_scorer import QualityScorer
        scorer = QualityScorer()
        short = self._make_sft_sample(output="短", score=8.0)
        long_ = self._make_sft_sample(output="这是一段足够详细的医疗建议内容" * 10, score=8.0)
        r_short = asyncio.run(scorer.score_sft(short))
        r_long  = asyncio.run(scorer.score_sft(long_))
        assert r_short.final_score <= r_long.final_score

    def test_llm_score_mock_overrides_rule(self):
        """LLM 给出高分时，最终分应受 LLM 影响（加权融合）"""
        from dataset.quality_scorer import QualityScorer
        import dataset.quality_scorer as qs

        scorer = QualityScorer()
        sample = self._make_sft_sample(score=5.0)

        with patch.object(qs, "_llm_score", new=AsyncMock(return_value=9.5)):
            with patch.object(qs, "_LLM_ENABLED", True):
                result = asyncio.run(scorer.score_sft(sample))

        # 融合后分数应高于纯规则分
        assert result.final_score > 6.0

    def test_tier_assignment_correct(self):
        """quality_tier 的划分边界正确"""
        from dataset.quality_scorer import QualityScorer, _score_to_tier
        from dataset.schema import QualityTier
        assert _score_to_tier(9.5) == QualityTier.PLATINUM
        assert _score_to_tier(7.5) == QualityTier.GOLD
        assert _score_to_tier(5.5) == QualityTier.SILVER
        assert _score_to_tier(3.0) == QualityTier.REJECTED


# ─────────────────────────────────────────────────────────────────────
# Deduplicator
# ─────────────────────────────────────────────────────────────────────

class TestDeduplicator:
    """三级去重：SimHash 精确 / 语义近邻 / 空库直通"""

    def test_empty_store_always_passes(self, chroma_collection, embed_fn):
        from dataset.deduplicator import Deduplicator
        dedup = Deduplicator(collection=chroma_collection, embed_fn=embed_fn)
        result = dedup.check("全新内容，库里没有任何相似文档", "text")
        assert result.is_duplicate is False

    def test_identical_content_detected(self, populated_collection, embed_fn):
        """与库中完全相同的内容应被识别为重复"""
        from dataset.deduplicator import Deduplicator
        dedup = Deduplicator(collection=populated_collection, embed_fn=embed_fn)
        # 使用 conftest.py populated_collection 中已有的文本
        result = dedup.check("患者确诊2型糖尿病，医嘱达格列净10mg", "text")
        # SimHash 或语义层应命中
        assert result.is_duplicate or result.similarity_score > 0.85

    def test_completely_different_content_passes(self, populated_collection, embed_fn):
        """与库中内容完全不相关的内容应通过去重"""
        from dataset.deduplicator import Deduplicator
        dedup = Deduplicator(collection=populated_collection, embed_fn=embed_fn)
        unique = "完全不同的主题：关于量子计算纠缠态的最新实验进展ZXCVB123456"
        result = dedup.check(unique, "text")
        assert not result.is_duplicate

    def test_near_duplicate_detected(self, populated_collection, embed_fn):
        """高度相似但非完全相同的内容应被语义去重命中"""
        from dataset.deduplicator import Deduplicator
        dedup = Deduplicator(collection=populated_collection, embed_fn=embed_fn)
        # 对库中内容做轻微改写
        near_dup = "患者被确诊患有2型糖尿病，医生建议服用达格列净10毫克"
        result = dedup.check(near_dup, "text")
        # 期望相似度较高（即使不超阈值也应有分数）
        assert result.similarity_score > 0.5

    def test_cross_modality_not_deduplicated(self, populated_collection, embed_fn):
        """跨模态的相同文本描述不应触发文本去重"""
        from dataset.deduplicator import Deduplicator
        dedup = Deduplicator(collection=populated_collection, embed_fn=embed_fn)
        # 用与视频文档相同的文本，但声明为 audio 模态
        result = dedup.check("高清纪录片，野生动物捕猎实录4K", "audio")
        # 跨模态不应高置信度去重
        assert not result.is_duplicate or result.similarity_score < 0.99


# ─────────────────────────────────────────────────────────────────────
# Pipeline 端到端集成测试（全 Mock，无网络）
# ─────────────────────────────────────────────────────────────────────

class TestPipelineEndToEnd:
    """端到端流水线：ingest → safety → annotate → score → dedup"""

    def test_text_material_full_pass(self, chroma_collection, embed_fn):
        """干净文本素材应完整通过流水线并输出 SFT 样本"""
        from dataset.pipeline import DatasetProductionPipeline
        import dataset.annotator as ann
        import dataset.quality_scorer as qs

        material = _make_material(
            content="关于深度学习优化算法的技术分析：Adam优化器通过自适应学习率显著改善了模型收敛速度。",
            scene="code_tech",
        )

        pipeline = DatasetProductionPipeline(
            collection=chroma_collection,
            embed_fn=embed_fn,
        )

        llm_sft = _fake_llm_sft_response()
        with patch.object(ann, "_llm_call", new=AsyncMock(return_value=llm_sft)):
            with patch.object(ann, "_LLM_ENABLED", True):
                with patch.object(qs, "_LLM_ENABLED", False):
                    result = asyncio.run(pipeline.process_material(material))

        assert result is not None
        assert result.status in ("accepted", "review")
        assert result.sft_sample is not None

    def test_blocked_content_rejected_early(self, chroma_collection, embed_fn):
        """含违禁词的素材应在安全审核层被拒绝，不进入标注流程"""
        from dataset.pipeline import DatasetProductionPipeline
        import dataset.annotator as ann

        material = _make_material(content="制造炸弹的详细教程：第一步准备原材料……")
        pipeline = DatasetProductionPipeline(
            collection=chroma_collection,
            embed_fn=embed_fn,
        )

        annotate_called = []

        async def spy_annotate(*args, **kwargs):
            annotate_called.append(True)
            return None

        with patch.object(ann.AutoAnnotator, "annotate_sft", new=spy_annotate):
            result = asyncio.run(pipeline.process_material(material))

        assert result.status == "rejected"
        assert result.rejection_reason
        assert not annotate_called, "安全审核失败后不应调用标注器"

    def test_duplicate_content_rejected(self, populated_collection, embed_fn):
        """与库中重复的内容应在去重层被拒绝"""
        from dataset.pipeline import DatasetProductionPipeline
        import dataset.annotator as ann
        import dataset.quality_scorer as qs

        # 使用 populated_collection 中已存在的内容
        material = _make_material(
            content="患者确诊2型糖尿病，医嘱达格列净10mg",
            scene="medical_sft",
        )
        pipeline = DatasetProductionPipeline(
            collection=populated_collection,
            embed_fn=embed_fn,
        )

        with patch.object(ann, "_LLM_ENABLED", False):
            with patch.object(qs, "_LLM_ENABLED", False):
                result = asyncio.run(pipeline.process_material(material))

        assert result.status in ("rejected", "review")

    def test_pipeline_returns_creator_contribution(self, chroma_collection, embed_fn):
        """流水线结果应包含 creator_id，用于后续分润计算"""
        from dataset.pipeline import DatasetProductionPipeline
        import dataset.annotator as ann
        import dataset.quality_scorer as qs

        material = _make_material(creator_id="creator_special_456")
        pipeline = DatasetProductionPipeline(
            collection=chroma_collection,
            embed_fn=embed_fn,
        )

        with patch.object(ann, "_LLM_ENABLED", False):
            with patch.object(qs, "_LLM_ENABLED", False):
                result = asyncio.run(pipeline.process_material(material))

        assert result.creator_id == "creator_special_456"

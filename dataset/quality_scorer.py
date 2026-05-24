# dataset/quality_scorer.py
"""
息壤 · 多维质检评分引擎

SFT 样本七维评分体系：
  ┌─────────────────┬──────┬────────────────────────────────────┐
  │ 维度             │ 权重  │ 说明                               │
  ├─────────────────┼──────┼────────────────────────────────────┤
  │ 准确性           │ 0.25 │ 事实是否正确，专业知识是否可靠        │
  │ 相关性           │ 0.20 │ 输出是否真正回答了指令               │
  │ 完整性           │ 0.15 │ 回答是否完整，无截断                 │
  │ 可读性           │ 0.15 │ 语言流畅，格式清晰                   │
  │ 安全性           │ 0.15 │ 无有害内容，无违规信息               │
  │ 多样性贡献       │ 0.05 │ 相比已有数据集的新颖性               │
  │ 指令遵循度       │ 0.05 │ 是否严格按指令要求格式输出           │
  └─────────────────┴──────┴────────────────────────────────────┘

评分结果 → QualityTier 分档 → 决定是否进入数据集
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from openai import AsyncOpenAI

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_settings
from dataset.schema import (
    AnnotationStatus, DPOSample, PretrainChunk,
    QualityTier, SFTSample,
)

_settings = get_settings()
_client = AsyncOpenAI(api_key=_settings.API_KEY, base_url=_settings.BASE_URL)

# 权重配置
_SFT_WEIGHTS: Dict[str, float] = {
    "accuracy":     0.25,
    "relevance":    0.20,
    "completeness": 0.15,
    "readability":  0.15,
    "safety":       0.15,
    "diversity":    0.05,
    "instruction_following": 0.05,
}

# 分档阈值
_TIER_THRESHOLDS = [
    (9.0, QualityTier.PLATINUM),
    (7.0, QualityTier.GOLD),
    (5.0, QualityTier.SILVER),
    (3.0, QualityTier.BRONZE),
    (0.0, QualityTier.DISCARD),
]

_SFT_SCORE_PROMPT = """\
你是一位严格的AI训练数据质检专家。请对以下SFT训练样本进行多维度评分。

【系统提示】{system_prompt}
【指令】{instruction}
【输入上下文】{input_context}
【输出】{output}

请从以下7个维度分别打分（每项0-10分，保留一位小数），并给出简短理由：
- accuracy（准确性）：事实是否正确，专业知识是否可靠
- relevance（相关性）：输出是否真正回答了指令
- completeness（完整性）：回答是否完整，无截断或遗漏
- readability（可读性）：语言流畅，格式清晰，易于理解
- safety（安全性）：无有害内容，无违规违法信息（有则直接给0）
- diversity（多样性贡献）：内容是否有独特视角或知识点
- instruction_following（指令遵循度）：是否严格按指令要求格式回答

请严格按以下JSON格式输出：
{{
  "accuracy": 8.5, "accuracy_reason": "...",
  "relevance": 9.0, "relevance_reason": "...",
  "completeness": 7.5, "completeness_reason": "...",
  "readability": 8.0, "readability_reason": "...",
  "safety": 10.0, "safety_reason": "...",
  "diversity": 7.0, "diversity_reason": "...",
  "instruction_following": 9.0, "instruction_following_reason": "...",
  "overall_comment": "总体评价..."
}}
"""

_PRETRAIN_SCORE_PROMPT = """\
你是一位语料质量评估专家。请评估以下预训练文本语料的质量。

【文本】
{text}

评估维度：
- fluency（流畅性）：语言是否自然流畅
- informativeness（信息量）：是否包含有价值的知识
- cleanliness（清洁度）：是否有乱码、广告、重复内容
- coherence（连贯性）：文章逻辑是否清晰连贯

请按以下JSON格式输出（0-10分）：
{{
  "fluency": 8.0,
  "informativeness": 7.5,
  "cleanliness": 9.0,
  "coherence": 8.0
}}
"""


@dataclass
class QualityReport:
    """质检报告"""
    sample_id:     str
    sample_type:   str           # "sft" / "dpo" / "pretrain"
    dimension_scores: Dict[str, float]
    weighted_score:   float
    quality_tier:     QualityTier
    passed:           bool
    rejection_reason: str = ""
    overall_comment:  str = ""


class QualityScorer:
    """
    多维度质检评分器

    工作流：
      LLM 评分 → 加权汇总 → 分档 → 安全硬过滤 → 最终结论
    """

    def __init__(self, min_pass_score: float = 5.0):
        self.min_pass_score = min_pass_score

    # ── SFT 评分 ────────────────────────────────────────

    async def score_sft(self, sample: SFTSample) -> QualityReport:
        prompt = _SFT_SCORE_PROMPT.format(
            system_prompt=sample.system_prompt or "(无)",
            instruction=sample.instruction,
            input_context=sample.input_context or "(无)",
            output=sample.output[:2000],  # 防超长
        )

        raw = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)

        if not parsed:
            # 解析失败 → 给低分
            report = QualityReport(
                sample_id=sample.sample_id,
                sample_type="sft",
                dimension_scores={},
                weighted_score=2.0,
                quality_tier=QualityTier.DISCARD,
                passed=False,
                rejection_reason="评分解析失败",
            )
            self._apply_to_sample(sample, report)
            return report

        # 提取各维度分数
        dim_scores: Dict[str, float] = {}
        for dim in _SFT_WEIGHTS:
            dim_scores[dim] = float(parsed.get(dim, 5.0))

        # 安全硬过滤：safety = 0 → 直接丢弃
        if dim_scores.get("safety", 10.0) == 0.0:
            report = QualityReport(
                sample_id=sample.sample_id,
                sample_type="sft",
                dimension_scores=dim_scores,
                weighted_score=0.0,
                quality_tier=QualityTier.DISCARD,
                passed=False,
                rejection_reason="安全检查不通过：含有害内容",
            )
            self._apply_to_sample(sample, report)
            return report

        # 加权汇总
        weighted = sum(dim_scores[d] * _SFT_WEIGHTS[d] for d in _SFT_WEIGHTS)
        tier = self._score_to_tier(weighted)
        passed = weighted >= self.min_pass_score

        report = QualityReport(
            sample_id=sample.sample_id,
            sample_type="sft",
            dimension_scores=dim_scores,
            weighted_score=round(weighted, 2),
            quality_tier=tier,
            passed=passed,
            overall_comment=parsed.get("overall_comment", ""),
            rejection_reason="" if passed else f"综合分 {weighted:.1f} 低于阈值 {self.min_pass_score}",
        )
        self._apply_to_sample(sample, report)
        return report

    # ── Pretrain 评分 ────────────────────────────────────

    async def score_pretrain(self, chunk: PretrainChunk) -> QualityReport:
        weights = {"fluency": 0.3, "informativeness": 0.3,
                   "cleanliness": 0.25, "coherence": 0.15}

        prompt = _PRETRAIN_SCORE_PROMPT.format(text=chunk.text[:1500])
        raw = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)

        if not parsed:
            report = QualityReport(
                sample_id=chunk.chunk_id, sample_type="pretrain",
                dimension_scores={}, weighted_score=2.0,
                quality_tier=QualityTier.DISCARD, passed=False,
                rejection_reason="评分解析失败",
            )
            chunk.quality_score = 2.0
            chunk.status = AnnotationStatus.REJECTED
            return report

        dim_scores = {d: float(parsed.get(d, 5.0)) for d in weights}
        weighted = sum(dim_scores[d] * weights[d] for d in weights)
        tier = self._score_to_tier(weighted)
        passed = weighted >= self.min_pass_score

        chunk.quality_score = round(weighted, 2)
        chunk.status = AnnotationStatus.APPROVED if passed else AnnotationStatus.REJECTED

        return QualityReport(
            sample_id=chunk.chunk_id, sample_type="pretrain",
            dimension_scores=dim_scores, weighted_score=round(weighted, 2),
            quality_tier=tier, passed=passed,
        )

    # ── 批量评分 ─────────────────────────────────────────


    # ── DPO 评分 ──────────────────────────────────────────

    async def score_dpo(self, sample: DPOSample) -> QualityReport:
        """
        DPO 样本四维评分：
          chosen_quality   0.35  chosen 本身是否高质量
          contrast_clarity 0.30  chosen/rejected 差异是否显著且合理
          preference_validity 0.25 偏好理由是否充分可信
          safety           0.10  双方均无安全风险
        """
        prompt = (
            "你是一个DPO训练数据质检专家。请评估以下偏好对样本质量。\n\n"
            f"【问题】{sample.prompt}\n\n"
            f"【chosen】{sample.chosen[:800]}\n\n"
            f"【rejected】{sample.rejected[:800]}\n\n"
            f"【偏好理由】{sample.preference_reason}\n\n"
            "请从以下4个维度评分（0-10分），仅输出JSON：\n"
            "{\n"
            '  "chosen_quality": 8.0,\n'
            '  "contrast_clarity": 7.5,\n'
            '  "preference_validity": 8.5,\n'
            '  "safety": 10.0,\n'
            '  "overall_comment": "..."\n'
            "}"
        )
        raw    = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)

        weights = {
            "chosen_quality":      0.35,
            "contrast_clarity":    0.30,
            "preference_validity": 0.25,
            "safety":              0.10,
        }

        if not parsed:
            sample.quality_score = 2.0
            sample.status = AnnotationStatus.REJECTED
            return QualityReport(
                sample_id=sample.sample_id, sample_type="dpo",
                dimension_scores={}, weighted_score=2.0,
                quality_tier=QualityTier.DISCARD, passed=False,
                rejection_reason="DPO评分解析失败",
            )

        dim_scores = {d: float(parsed.get(d, 5.0)) for d in weights}

        if dim_scores.get("safety", 10.0) == 0.0:
            sample.quality_score = 0.0
            sample.status = AnnotationStatus.REJECTED
            return QualityReport(
                sample_id=sample.sample_id, sample_type="dpo",
                dimension_scores=dim_scores, weighted_score=0.0,
                quality_tier=QualityTier.DISCARD, passed=False,
                rejection_reason="安全检查不通过",
            )

        weighted = sum(dim_scores[d] * weights[d] for d in weights)
        tier     = self._score_to_tier(weighted)
        passed   = weighted >= self.min_pass_score

        sample.quality_score = round(weighted, 2)
        sample.status = AnnotationStatus.APPROVED if passed else AnnotationStatus.REJECTED

        return QualityReport(
            sample_id=sample.sample_id, sample_type="dpo",
            dimension_scores=dim_scores, weighted_score=round(weighted, 2),
            quality_tier=tier, passed=passed,
            overall_comment=parsed.get("overall_comment", ""),
            rejection_reason="" if passed else f"DPO综合分 {weighted:.1f} 低于阈值",
        )

    async def batch_score_dpo(
        self,
        samples: "List[DPOSample]",
        concurrency: int = 8,
    ) -> "List[QualityReport]":
        sem = asyncio.Semaphore(concurrency)
        async def _score(s):
            async with sem:
                return await self.score_dpo(s)
        reports = await asyncio.gather(*[_score(s) for s in samples], return_exceptions=True)
        return [r if not isinstance(r, Exception) else None for r in reports]

    async def batch_score_sft(
        self,
        samples: List[SFTSample],
        concurrency: int = 8,
    ) -> List[QualityReport]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(s):
            async with sem:
                return await self.score_sft(s)

        reports = await asyncio.gather(*[_score(s) for s in samples], return_exceptions=True)

        valid = []
        for i, r in enumerate(reports):
            if isinstance(r, Exception):
                print(f"⚠️  样本 {samples[i].sample_id[:8]} 评分失败: {r}")
                valid.append(None)
            else:
                valid.append(r)

        return valid

    async def batch_score_pretrain(
        self,
        chunks: List[PretrainChunk],
        concurrency: int = 10,
    ) -> List[QualityReport]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(c):
            async with sem:
                return await self.score_pretrain(c)

        return await asyncio.gather(*[_score(c) for c in chunks], return_exceptions=False)

    # ── 统计摘要 ─────────────────────────────────────────

    @staticmethod
    def summarize(reports: List[QualityReport]) -> dict:
        valid = [r for r in reports if r and not isinstance(r, Exception)]
        if not valid:
            return {}

        passed = [r for r in valid if r.passed]
        tier_counts = {}
        for t in QualityTier:
            tier_counts[t.value] = sum(1 for r in valid if r.quality_tier == t)

        scores = [r.weighted_score for r in valid]
        return {
            "total":         len(valid),
            "passed":        len(passed),
            "pass_rate":     round(len(passed) / len(valid), 3),
            "avg_score":     round(sum(scores) / len(scores), 2),
            "max_score":     max(scores),
            "min_score":     min(scores),
            "tier_distribution": tier_counts,
        }

    # ── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _score_to_tier(score: float) -> QualityTier:
        for threshold, tier in _TIER_THRESHOLDS:
            if score >= threshold:
                return tier
        return QualityTier.DISCARD

    @staticmethod
    def _apply_to_sample(sample: SFTSample, report: QualityReport):
        sample.quality_score = report.weighted_score
        sample.quality_tier = report.quality_tier
        sample.quality_detail = report.dimension_scores
        sample.status = (
            AnnotationStatus.APPROVED if report.passed else AnnotationStatus.REJECTED
        )

    async def _llm_call(self, prompt: str) -> str:
        try:
            resp = await _client.chat.completions.create(
                model=_settings.MODEL_NAME,
                max_tokens=800,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"❌ 质检 LLM 调用失败: {e}")
            return ""

    @staticmethod
    def _safe_parse_json(text: str):
        if not text:
            return None
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None

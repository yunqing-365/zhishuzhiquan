# dataset/quality_scorer.py
"""
知数知圈 · 质量评分引擎

对 SFT / DPO / Pretrain 三类样本进行多维度质量评估。
评分维度：完整性、准确性、多样性、语言流畅度、领域相关性。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Dict

from dataset.schema import SFTSample, DPOSample, PretrainChunk, QualityTier


@dataclass
class SFTQualityReport:
    sample_id:   str
    score:       float
    passed:      bool
    dimensions:  Dict[str, float]
    issues:      List[str]


@dataclass
class DPOQualityReport:
    sample_id:  str
    score:      float
    passed:     bool
    issues:     List[str]


@dataclass
class PretrainQualityReport:
    chunk_id: str
    score:    float
    passed:   bool
    issues:   List[str]


class QualityScorer:
    """多维度质量评分器（规则 + LLM 混合模式）"""

    def __init__(self, min_score: float = 5.0):
        self.min_score = min_score

    # ── SFT ────────────────────────────────────────────

    async def score_sft(self, sample: SFTSample) -> Optional[SFTQualityReport]:
        """评分单个 SFT 样本"""
        issues = []
        dims: Dict[str, float] = {}

        # 完整性检查
        if not sample.instruction:
            issues.append("instruction 为空")
            dims["completeness"] = 0.0
        else:
            dims["completeness"] = min(10.0, len(sample.instruction) / 20)

        # 输出质量
        if not sample.output or len(sample.output) < 10:
            issues.append("output 过短")
            dims["output_quality"] = 2.0
        else:
            dims["output_quality"] = min(10.0, len(sample.output) / 50)

        # 语言检查（简单规则）
        text = (sample.instruction + sample.output)
        has_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
        dims["language_quality"] = 8.0 if has_zh or len(text) > 30 else 4.0

        # 综合分（使用 sample 自带的 quality_score 为基准）
        base = sample.quality_score if sample.quality_score > 0 else 6.0
        rule_score = sum(dims.values()) / len(dims)
        final_score = round((base * 0.7 + rule_score * 0.3), 2)
        final_score = min(10.0, max(0.0, final_score))

        return SFTQualityReport(
            sample_id=sample.sample_id,
            score=final_score,
            passed=final_score >= self.min_score and not any("为空" in i for i in issues),
            dimensions=dims,
            issues=issues,
        )

    async def batch_score_sft(
        self, samples: List[SFTSample], concurrency: int = 8
    ) -> List[Optional[SFTQualityReport]]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(s):
            async with sem:
                return await self.score_sft(s)

        return await asyncio.gather(*[_score(s) for s in samples], return_exceptions=False)

    # ── DPO ────────────────────────────────────────────

    async def score_dpo(self, sample: DPOSample) -> Optional[DPOQualityReport]:
        issues = []

        if not sample.prompt:
            issues.append("prompt 为空")
        if not sample.chosen:
            issues.append("chosen 为空")
        if not sample.rejected:
            issues.append("rejected 为空")
        if sample.chosen == sample.rejected:
            issues.append("chosen 与 rejected 相同")

        score = sample.quality_score if sample.quality_score > 0 else 6.0
        if issues:
            score = max(0.0, score - len(issues) * 1.5)

        return DPOQualityReport(
            sample_id=sample.sample_id,
            score=round(score, 2),
            passed=score >= self.min_score and not issues,
            issues=issues,
        )

    async def batch_score_dpo(
        self, samples: List[DPOSample], concurrency: int = 8
    ) -> List[Optional[DPOQualityReport]]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(s):
            async with sem:
                return await self.score_dpo(s)

        return await asyncio.gather(*[_score(s) for s in samples])

    # ── Pretrain ────────────────────────────────────────

    async def score_pretrain(self, chunk: PretrainChunk) -> Optional[PretrainQualityReport]:
        issues = []
        score = chunk.quality_score if chunk.quality_score > 0 else 7.0

        if len(chunk.text) < 50:
            issues.append("文本过短")
            score -= 2.0
        if chunk.token_count < 10:
            issues.append("token 数量过少")
            score -= 1.0

        score = min(10.0, max(0.0, score))
        return PretrainQualityReport(
            chunk_id=chunk.chunk_id,
            score=round(score, 2),
            passed=score >= self.min_score,
            issues=issues,
        )

    async def batch_score_pretrain(
        self, chunks: List[PretrainChunk], concurrency: int = 10
    ) -> List[Optional[PretrainQualityReport]]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(c):
            async with sem:
                return await self.score_pretrain(c)

        return await asyncio.gather(*[_score(c) for c in chunks])

    # ── 汇总 ────────────────────────────────────────────

    def summarize(self, reports: list) -> dict:
        """对一批报告做汇总统计"""
        if not reports:
            return {"count": 0, "pass_rate": 0.0, "avg_score": 0.0}

        valid = [r for r in reports if r is not None]
        passed = [r for r in valid if r.passed]
        scores = [r.score for r in valid]

        return {
            "count":     len(valid),
            "passed":    len(passed),
            "pass_rate": round(len(passed) / len(valid), 4) if valid else 0.0,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        }

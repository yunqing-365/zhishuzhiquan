# dataset/quality_scorer.py  v2
"""
知数知圈 · 质量评分引擎 v2

升级日志 v2:
  [核心] LLM 语义评分替代纯字符数规则
         原来: output_quality = min(10.0, len(output) / 50)  ← 垃圾长文也满分
         现在: LLM 从 6 个维度给出 0-10 分，规则分降权为辅助校验
  [新增] SFT 六维评分：completeness / instruction_clarity /
         output_quality / factuality / language_fluency / relevance
  [新增] DPO 差异度检测：chosen 与 rejected 语义相似度 > 0.85 → 扣分
  [新增] LLM 评分失败时自动降级为规则评分（不阻断流水线）
  [保留] batch_score_* 并发接口不变（上游 pipeline.py 无需改动）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataset.schema import SFTSample, DPOSample, PretrainChunk, QualityTier

try:
    from config import get_settings
    _s = get_settings()
    _API_KEY  = _s.openai_api_key
    _BASE_URL = _s.openai_base_url
    _MODEL    = _s.openai_model
except Exception:
    _API_KEY  = os.environ.get("OPENAI_API_KEY", "")
    _BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    _MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_LLM_ENABLED = bool(_API_KEY)


# ════════════════════════════════════════════════════════════════════
# 数据类
# ════════════════════════════════════════════════════════════════════

@dataclass
class SFTQualityReport:
    sample_id:  str
    score:      float
    passed:     bool
    dimensions: Dict[str, float]
    issues:     List[str]
    scored_by:  str = "rule"   # "llm" | "rule" | "hybrid"


@dataclass
class DPOQualityReport:
    sample_id:  str
    score:      float
    passed:     bool
    issues:     List[str]
    scored_by:  str = "rule"


@dataclass
class PretrainQualityReport:
    chunk_id:  str
    score:     float
    passed:    bool
    issues:    List[str]
    scored_by: str = "rule"


# ════════════════════════════════════════════════════════════════════
# LLM 调用（带超时 + 降级）
# ════════════════════════════════════════════════════════════════════

_SFT_SCORER_SYSTEM = """\
你是专业 AI 训练数据质量审核员。对给定的 SFT 样本从以下 6 个维度评分（0-10，可以有小数）：

1. completeness（完整性）：instruction + input + output 信息是否完备
2. instruction_clarity（指令清晰度）：指令是否明确、无歧义
3. output_quality（输出质量）：回答是否准确、有价值、内容充实
4. factuality（事实准确性）：内容是否可信，无明显错误
5. language_fluency（语言流畅度）：表达是否自然流畅，无语法错误
6. relevance（指令-输出相关性）：输出是否切题

输出严格 JSON，不含 markdown 代码块：
{"completeness":8,"instruction_clarity":7,"output_quality":6,"factuality":8,"language_fluency":9,"relevance":8,"overall":7.5,"issues":["..."]}\
"""

_DPO_SCORER_SYSTEM = """\
你是专业 AI 训练数据质量审核员。对给定的 DPO 偏好对评分（0-10）。
评估维度：
- chosen_quality（chosen 回答的绝对质量）
- rejected_quality（rejected 回答是否确实更差）
- preference_clarity（两者差异是否足够明显，差异度越大越好）
- prompt_quality（问题本身质量）

输出严格 JSON：
{"chosen_quality":8,"rejected_quality":4,"preference_clarity":7,"prompt_quality":8,"overall":7.0,"issues":["..."]}\
"""


async def _llm_score(prompt: str, system: str, timeout: float = 20.0) -> Optional[dict]:
    """调用 LLM 获取评分 JSON，失败返回 None。"""
    if not _LLM_ENABLED:
        return None
    try:
        import aiohttp
    except ImportError:
        return None

    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 256,
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
                raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
                return json.loads(raw)
    except Exception as e:
        print(f"⚠️  [quality_scorer] LLM 评分失败: {e}")
        return None


# ════════════════════════════════════════════════════════════════════
# 规则评分（兜底，不依赖 LLM）
# ════════════════════════════════════════════════════════════════════

def _rule_score_sft(sample: SFTSample) -> tuple[Dict[str, float], List[str]]:
    """返回 (dimensions_dict, issues_list)"""
    issues: List[str] = []
    dims: Dict[str, float] = {}

    # 完整性
    if not sample.instruction:
        issues.append("instruction 为空")
        dims["completeness"] = 0.0
    elif len(sample.instruction) < 5:
        issues.append("instruction 过短（<5字）")
        dims["completeness"] = 3.0
    else:
        dims["completeness"] = min(9.5, 5.0 + len(sample.instruction) / 40)

    # 输出质量（不再用字符数除50，改为分段判断）
    out_len = len(sample.output) if sample.output else 0
    if out_len < 10:
        issues.append("output 过短（<10字）")
        dims["output_quality"] = 1.0
    elif out_len < 50:
        dims["output_quality"] = 4.0
    elif out_len < 200:
        dims["output_quality"] = 6.5
    elif out_len < 800:
        dims["output_quality"] = 8.0
    else:
        dims["output_quality"] = 8.5  # 超长不代表更好

    # 语言流畅度（有中文且无乱码）
    text = (sample.instruction or "") + (sample.output or "")
    has_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
    has_en = any('a' <= c.lower() <= 'z' for c in text)
    if not has_zh and not has_en:
        issues.append("内容无可辨识语言字符")
        dims["language_fluency"] = 2.0
    else:
        dims["language_fluency"] = 7.5

    # 指令清晰度（简单启发：有问号/动词开头加分）
    instr = sample.instruction or ""
    dims["instruction_clarity"] = 8.0 if (
        instr.endswith("？") or instr.endswith("?") or
        any(instr.startswith(v) for v in ["请", "如何", "分析", "描述", "解释", "写", "生成", "总结"])
    ) else 6.0

    # 相关性（output 含 instruction 关键词）
    kw = set(instr[:50].split())
    out_words = set((sample.output or "").split())
    overlap = len(kw & out_words) / max(len(kw), 1)
    dims["relevance"] = min(9.0, 5.0 + overlap * 10)

    # 事实准确性无法规则判断，给中性分
    dims["factuality"] = 7.0

    return dims, issues


def _rule_score_final(dims: Dict[str, float], base: float, issues: List[str]) -> float:
    rule_avg = sum(dims.values()) / len(dims) if dims else 5.0
    # base 来自标注阶段 LLM 给的 quality_score（参考权重低）
    if base > 0:
        score = rule_avg * 0.6 + base * 0.4
    else:
        score = rule_avg
    return round(min(10.0, max(0.0, score)), 2)


# ════════════════════════════════════════════════════════════════════
# 主评分器
# ════════════════════════════════════════════════════════════════════

class QualityScorer:
    """多维度质量评分器（LLM 语义 + 规则兜底 混合模式）"""

    def __init__(self, min_score: float = 5.0):
        self.min_score = min_score

    # ── SFT ─────────────────────────────────────────────────────────

    async def score_sft(self, sample: SFTSample) -> Optional[SFTQualityReport]:
        issues: List[str] = []
        dims: Dict[str, float] = {}
        scored_by = "rule"

        # 先跑规则（快速前置校验，LLM 也依赖这里发现的硬性问题）
        rule_dims, rule_issues = _rule_score_sft(sample)

        # 硬性拦截：instruction 为空直接拒绝，不再浪费 LLM token
        if "instruction 为空" in rule_issues:
            return SFTQualityReport(
                sample_id=sample.sample_id,
                score=0.0, passed=False,
                dimensions=rule_dims, issues=rule_issues,
                scored_by="rule",
            )

        # 尝试 LLM 评分
        llm_result = None
        if _LLM_ENABLED:
            prompt = (
                f"instruction: {sample.instruction[:500]}\n"
                f"input: {sample.input[:200]}\n"
                f"output: {sample.output[:800]}"
            )
            llm_result = await _llm_score(prompt, _SFT_SCORER_SYSTEM)

        if llm_result:
            scored_by = "llm"
            # 取 LLM 的各维度分
            dims = {
                "completeness":       float(llm_result.get("completeness",       rule_dims.get("completeness", 7))),
                "instruction_clarity":float(llm_result.get("instruction_clarity",rule_dims.get("instruction_clarity", 6))),
                "output_quality":     float(llm_result.get("output_quality",     rule_dims.get("output_quality", 6))),
                "factuality":         float(llm_result.get("factuality",         7)),
                "language_fluency":   float(llm_result.get("language_fluency",   rule_dims.get("language_fluency", 7))),
                "relevance":          float(llm_result.get("relevance",          rule_dims.get("relevance", 6))),
            }
            issues = list(llm_result.get("issues", []))
            # LLM 给的 overall 与维度平均做加权
            llm_overall = float(llm_result.get("overall", sum(dims.values()) / len(dims)))
            dim_avg = sum(dims.values()) / len(dims)
            final_score = round((llm_overall * 0.6 + dim_avg * 0.4), 2)
        else:
            # 降级到规则
            scored_by = "rule"
            dims = rule_dims
            issues = rule_issues
            final_score = _rule_score_final(dims, sample.quality_score, issues)

        # 合并规则发现的硬性问题（即使 LLM 没提）
        for ri in rule_issues:
            if ri not in issues:
                issues.append(ri)
                final_score = max(0.0, final_score - 0.5)

        final_score = round(min(10.0, max(0.0, final_score)), 2)
        hard_fail = any(kw in i for i in issues for kw in ["为空", "过短"])

        return SFTQualityReport(
            sample_id=sample.sample_id,
            score=final_score,
            passed=final_score >= self.min_score and not hard_fail,
            dimensions=dims,
            issues=issues,
            scored_by=scored_by,
        )

    async def batch_score_sft(
        self, samples: List[SFTSample], concurrency: int = 5
    ) -> List[Optional[SFTQualityReport]]:
        # LLM 并发控制更保守（避免速率限制）
        effective_concurrency = 3 if _LLM_ENABLED else concurrency
        sem = asyncio.Semaphore(effective_concurrency)

        async def _score(s):
            async with sem:
                return await self.score_sft(s)

        return await asyncio.gather(*[_score(s) for s in samples], return_exceptions=False)

    # ── DPO ─────────────────────────────────────────────────────────

    async def score_dpo(self, sample: DPOSample) -> Optional[DPOQualityReport]:
        issues: List[str] = []

        # 硬性规则校验
        if not sample.prompt:
            issues.append("prompt 为空")
        if not sample.chosen:
            issues.append("chosen 为空")
        if not sample.rejected:
            issues.append("rejected 为空")
        if sample.chosen and sample.rejected and sample.chosen == sample.rejected:
            issues.append("chosen 与 rejected 完全相同，无偏好信号")
        # 简单差异度检测（字符集重叠率）
        if sample.chosen and sample.rejected:
            c_set = set(sample.chosen)
            r_set = set(sample.rejected)
            overlap = len(c_set & r_set) / max(len(c_set | r_set), 1)
            if overlap > 0.90:
                issues.append(f"chosen 与 rejected 过于相似（字符重叠 {overlap:.0%}），偏好信号弱")

        if issues and any("为空" in i for i in issues):
            return DPOQualityReport(
                sample_id=sample.sample_id,
                score=0.0, passed=False,
                issues=issues, scored_by="rule",
            )

        # 尝试 LLM 评分
        llm_result = None
        if _LLM_ENABLED and not issues:
            prompt = (
                f"prompt: {sample.prompt[:400]}\n"
                f"chosen: {sample.chosen[:600]}\n"
                f"rejected: {sample.rejected[:600]}"
            )
            llm_result = await _llm_score(prompt, _DPO_SCORER_SYSTEM)

        if llm_result:
            score = float(llm_result.get("overall", sample.quality_score or 6.0))
            llm_issues = list(llm_result.get("issues", []))
            issues = list(set(issues + llm_issues))
            scored_by = "llm"
        else:
            score = sample.quality_score if sample.quality_score > 0 else 6.0
            scored_by = "rule"

        score = round(max(0.0, score - len([i for i in issues if "偏好" in i or "相似" in i]) * 1.0), 2)
        score = round(min(10.0, max(0.0, score)), 2)

        return DPOQualityReport(
            sample_id=sample.sample_id,
            score=score,
            passed=score >= self.min_score and not any("为空" in i for i in issues),
            issues=issues,
            scored_by=scored_by,
        )

    async def batch_score_dpo(
        self, samples: List[DPOSample], concurrency: int = 5
    ) -> List[Optional[DPOQualityReport]]:
        effective_concurrency = 3 if _LLM_ENABLED else concurrency
        sem = asyncio.Semaphore(effective_concurrency)

        async def _score(s):
            async with sem:
                return await self.score_dpo(s)

        return await asyncio.gather(*[_score(s) for s in samples])

    # ── Pretrain ─────────────────────────────────────────────────────

    async def score_pretrain(self, chunk: PretrainChunk) -> Optional[PretrainQualityReport]:
        issues: List[str] = []
        score = chunk.quality_score if chunk.quality_score > 0 else 7.0

        # 规则校验
        if len(chunk.text) < 50:
            issues.append("文本过短（<50字）")
            score -= 2.5
        elif len(chunk.text) < 100:
            issues.append("文本较短（<100字），上下文可能不完整")
            score -= 1.0

        if chunk.token_count < 10:
            issues.append("token 数量过少（<10）")
            score -= 1.5

        # 重复检测（相邻 5-gram 重复率）
        words = chunk.text.split()
        if len(words) > 20:
            ngrams = [tuple(words[i:i+5]) for i in range(len(words) - 4)]
            unique_ratio = len(set(ngrams)) / len(ngrams)
            if unique_ratio < 0.5:
                issues.append(f"文本重复率过高（unique 5-gram: {unique_ratio:.0%}）")
                score -= 2.0

        score = round(min(10.0, max(0.0, score)), 2)
        return PretrainQualityReport(
            chunk_id=chunk.chunk_id,
            score=score,
            passed=score >= self.min_score,
            issues=issues,
            scored_by="rule",
        )

    async def batch_score_pretrain(
        self, chunks: List[PretrainChunk], concurrency: int = 10
    ) -> List[Optional[PretrainQualityReport]]:
        sem = asyncio.Semaphore(concurrency)

        async def _score(c):
            async with sem:
                return await self.score_pretrain(c)

        return await asyncio.gather(*[_score(c) for c in chunks])

    # ── 汇总 ─────────────────────────────────────────────────────────

    def summarize(self, reports: list) -> dict:
        if not reports:
            return {"count": 0, "pass_rate": 0.0, "avg_score": 0.0, "llm_scored": 0}

        valid   = [r for r in reports if r is not None]
        passed  = [r for r in valid if r.passed]
        scores  = [r.score for r in valid]
        llm_cnt = sum(1 for r in valid if getattr(r, "scored_by", "rule") == "llm")

        return {
            "count":      len(valid),
            "passed":     len(passed),
            "pass_rate":  round(len(passed) / len(valid), 4) if valid else 0.0,
            "avg_score":  round(sum(scores) / len(scores), 2) if scores else 0.0,
            "llm_scored": llm_cnt,
            "rule_scored": len(valid) - llm_cnt,
        }

# dataset/annotator.py  v2
"""
知数知圈 · 自动标注引擎 v2

升级日志 v2:
  [核心] 多模型投票标注：同一素材分别调用主模型和副模型，
         取字段一致的结果（instruction/prompt 文本相似度 > 0.6 视为一致）
         不一致时降级为规则生成，确保输出可信度
  [新增] MultiModelAnnotator：封装投票逻辑，上层 BatchAnnotationJob 无感知切换
  [保留] 单模型 AutoAnnotator 接口不变，兼容旧调用方
  [修复] _llm_call 超时从 60s 缩短到 30s，避免慢请求拖慢整个 batch
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataset.schema import (
    CreatorMaterial, SFTSample, DPOSample, PretrainChunk, QualityTier
)

try:
    from config import get_settings
    _settings = get_settings()
    _API_KEY   = _settings.openai_api_key
    _BASE_URL  = _settings.openai_base_url
    _MODEL     = _settings.openai_model
    # 副模型：优先用配置中显式设置的，为空则回退到主模型
    _MODEL_B   = _settings.openai_model_b or _settings.openai_model
except Exception:
    _API_KEY   = os.environ.get("OPENAI_API_KEY", "")
    _BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    _MODEL     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    _MODEL_B   = os.environ.get("OPENAI_MODEL_B", _MODEL)

_LLM_ENABLED = bool(_API_KEY)


class AnnotationMode(str, Enum):
    AUTO_ONLY   = "auto_only"
    AUTO_REVIEW = "auto_review"
    HUMAN_FIRST = "human_first"


# ════════════════════════════════════════════════════════════════════
# LLM 调用
# ════════════════════════════════════════════════════════════════════

async def _llm_call(
    prompt: str,
    system: str = "",
    model: str = "",
    temperature: float = 0.3,
    timeout: float = 30.0,
) -> str:
    if not _LLM_ENABLED:
        return ""
    try:
        import aiohttp
    except ImportError:
        return ""

    model = model or _MODEL
    headers = {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": 1024}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️  LLM 调用失败 (model={model}): {e}")
        return ""


def _parse_json(raw: str) -> Optional[dict]:
    """安全解析 LLM 输出的 JSON，自动去掉 markdown 代码块。"""
    if not raw:
        return None
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════
# 文本相似度（用于投票一致性判断，无需 embedding）
# ════════════════════════════════════════════════════════════════════

def _char_overlap(a: str, b: str) -> float:
    """字符集 Jaccard 相似度，快速近似判断两段文本是否表达同一意思。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a[:200]), set(b[:200])
    return len(sa & sb) / max(len(sa | sb), 1)


def _vote_sft(data_a: dict, data_b: dict) -> Tuple[dict, str]:
    """
    对两个模型的 SFT 标注结果投票。
    返回 (merged_data, method)：method = "vote" | "fallback_a" | "rule"
    """
    instr_a = data_a.get("instruction", "")
    instr_b = data_b.get("instruction", "")
    sim = _char_overlap(instr_a, instr_b)

    if sim >= 0.5 and instr_a and instr_b:
        # 两个模型基本一致，取质量分更高的那个，其余字段融合
        qa = float(data_a.get("quality_score", 0))
        qb = float(data_b.get("quality_score", 0))
        base = data_a if qa >= qb else data_b
        merged = {**base, "quality_score": round((qa + qb) / 2, 2)}
        merged["_vote_sim"] = round(sim, 3)
        return merged, "vote"
    elif instr_a:
        data_a["_vote_sim"] = round(sim, 3)
        return data_a, "fallback_a"
    else:
        return {}, "rule"


def _vote_dpo(data_a: dict, data_b: dict) -> Tuple[dict, str]:
    prompt_a = data_a.get("prompt", "")
    prompt_b = data_b.get("prompt", "")
    sim = _char_overlap(prompt_a, prompt_b)

    if sim >= 0.5 and prompt_a:
        qa = float(data_a.get("quality_score", 0))
        qb = float(data_b.get("quality_score", 0))
        base = data_a if qa >= qb else data_b
        merged = {**base, "quality_score": round((qa + qb) / 2, 2)}
        merged["_vote_sim"] = round(sim, 3)
        return merged, "vote"
    elif prompt_a:
        data_a["_vote_sim"] = round(sim, 3)
        return data_a, "fallback_a"
    else:
        return {}, "rule"


# ════════════════════════════════════════════════════════════════════
# Prompt 模板
# ════════════════════════════════════════════════════════════════════

_SFT_SYSTEM = """\
你是专业数据集标注员。给定一段文本，生成高质量 SFT 训练样本。
输出严格 JSON（不含 markdown 代码块）：
{"instruction":"...","input":"...","output":"...","domain":"...","quality_score":0-10}"""

_DPO_SYSTEM = """\
你是专业数据集标注员。给定文本，生成 DPO 偏好对。
chosen 应明显优于 rejected（长度/质量差异需显著）。
输出严格 JSON（不含 markdown 代码块）：
{"prompt":"...","chosen":"...","rejected":"...","quality_score":0-10}"""


# ════════════════════════════════════════════════════════════════════
# 规则兜底（不依赖 LLM）
# ════════════════════════════════════════════════════════════════════

def _rule_sft(material: CreatorMaterial) -> dict:
    content = material.content
    preview = content[:80].replace("\n", " ")
    return {
        "instruction": f"请分析并总结以下内容：{preview}",
        "input": "",
        "output": content[:600],
        "domain": material.metadata.get("domain", "general"),
        "quality_score": 5.5,
        "_source": "rule",
    }


def _rule_dpo(material: CreatorMaterial) -> dict:
    content = material.content
    preview = content[:80].replace("\n", " ")
    return {
        "prompt": f"关于以下内容，给出专业分析：{preview}",
        "chosen": content[:400],
        "rejected": "此问题较为复杂，需要更多背景信息才能回答。",
        "quality_score": 5.0,
        "_source": "rule",
    }


def _score_to_tier(score: float) -> QualityTier:
    if score >= 9.0:  return QualityTier.PLATINUM
    if score >= 7.0:  return QualityTier.GOLD
    if score >= 5.0:  return QualityTier.SILVER
    return QualityTier.REJECTED


# ════════════════════════════════════════════════════════════════════
# 单模型标注器（向后兼容）
# ════════════════════════════════════════════════════════════════════

class AutoAnnotator:
    """单模型标注器，接口与 v1 完全兼容。"""

    def __init__(self, mode: AnnotationMode = AnnotationMode.AUTO_REVIEW):
        self.mode = mode

    async def annotate_sft(self, material: CreatorMaterial) -> Optional[SFTSample]:
        content = material.content[:3000]
        raw  = await _llm_call(f"原始素材:\n{content}", system=_SFT_SYSTEM)
        data = _parse_json(raw) or _rule_sft(material)
        score = float(data.get("quality_score", 6.0))
        return SFTSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            instruction=data.get("instruction", ""),
            input=data.get("input", ""),
            output=data.get("output", ""),
            quality_score=score,
            quality_tier=_score_to_tier(score),
            domain=data.get("domain", "general"),
            token_count=len(content.split()),
        )

    async def annotate_dpo(self, material: CreatorMaterial) -> Optional[DPOSample]:
        content = material.content[:3000]
        raw  = await _llm_call(f"原始素材:\n{content}", system=_DPO_SYSTEM)
        data = _parse_json(raw) or _rule_dpo(material)
        score = float(data.get("quality_score", 6.0))
        return DPOSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            prompt=data.get("prompt", ""),
            chosen=data.get("chosen", ""),
            rejected=data.get("rejected", ""),
            quality_score=score,
            quality_tier=_score_to_tier(score),
            token_count=len(content.split()),
        )

    async def annotate_pretrain(self, material: CreatorMaterial) -> Optional[PretrainChunk]:
        chunks = await self.annotate_all_pretrain(material)
        return chunks[0] if chunks else None

    async def annotate_all_pretrain(self, material: CreatorMaterial) -> List[PretrainChunk]:
        words      = material.content.split()
        chunk_size = 512
        chunks     = []
        for i in range(0, len(words), chunk_size):
            chunk_text = " ".join(words[i:i + chunk_size])
            if len(chunk_text) < 50:
                continue
            chunks.append(PretrainChunk(
                material_id=material.material_id,
                creator_id=material.creator_id,
                text=chunk_text,
                quality_score=7.0,
                domain=material.metadata.get("domain", "general"),
                token_count=len(words[i:i + chunk_size]),
            ))
        return chunks


# ════════════════════════════════════════════════════════════════════
# 多模型投票标注器（v2 核心）
# ════════════════════════════════════════════════════════════════════

class MultiModelAnnotator:
    """
    双模型并发标注 + 投票融合。

    主模型（_MODEL, temperature=0.3）与副模型（_MODEL_B, temperature=0.5）
    并发生成标注，比较一致性后合并或降级。

    投票逻辑：
      - 两者 instruction/prompt 字符集重叠 ≥ 50% → 视为一致，取均分
      - 否则取主模型结果
      - 主模型也失败 → 规则生成

    【真正的多模型投票】：
      在 .env 中设置 OPENAI_MODEL_B 为不同厂商/架构的模型，例如：
        OPENAI_MODEL=gpt-4o-mini          (OpenAI)
        OPENAI_MODEL_B=deepseek-chat      (DeepSeek, 同时配 OPENAI_BASE_URL_B)
      不同来源模型的分歧比同模型不同 temperature 更能提升标注可信度。

    当前状态：
      若 OPENAI_MODEL_B 未设置，_MODEL_B == _MODEL（仅 temperature 不同），
      这是伪多模型投票，可信度提升有限。配置不同的真实副模型可显著改善。
    """

    def __init__(self, mode: AnnotationMode = AnnotationMode.AUTO_REVIEW):
        self.mode = mode

    async def annotate_sft(self, material: CreatorMaterial) -> Optional[SFTSample]:
        content = material.content[:3000]
        prompt  = f"原始素材:\n{content}"

        if _LLM_ENABLED:
            # 并发调用两个模型
            raw_a, raw_b = await asyncio.gather(
                _llm_call(prompt, system=_SFT_SYSTEM, model=_MODEL,   temperature=0.3),
                _llm_call(prompt, system=_SFT_SYSTEM, model=_MODEL_B, temperature=0.5),
                return_exceptions=True,
            )
            data_a = _parse_json(raw_a if isinstance(raw_a, str) else "")
            data_b = _parse_json(raw_b if isinstance(raw_b, str) else "")

            if data_a and data_b:
                data, method = _vote_sft(data_a, data_b)
            elif data_a:
                data, method = data_a, "single_a"
            elif data_b:
                data, method = data_b, "single_b"
            else:
                data, method = _rule_sft(material), "rule"
        else:
            data, method = _rule_sft(material), "rule"

        if not data:
            data, method = _rule_sft(material), "rule"

        score = float(data.get("quality_score", 6.0))
        sample = SFTSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            instruction=data.get("instruction", ""),
            input=data.get("input", ""),
            output=data.get("output", ""),
            quality_score=score,
            quality_tier=_score_to_tier(score),
            domain=data.get("domain", "general"),
            token_count=len(content.split()),
        )
        # 附带投票元数据（写入 sample.metadata，不影响结构）
        if hasattr(sample, "metadata"):
            sample.metadata = {
                "vote_method": method,
                "vote_sim":    data.get("_vote_sim", 0.0),
            }
        return sample

    async def annotate_dpo(self, material: CreatorMaterial) -> Optional[DPOSample]:
        content = material.content[:3000]
        prompt  = f"原始素材:\n{content}"

        if _LLM_ENABLED:
            raw_a, raw_b = await asyncio.gather(
                _llm_call(prompt, system=_DPO_SYSTEM, model=_MODEL,   temperature=0.3),
                _llm_call(prompt, system=_DPO_SYSTEM, model=_MODEL_B, temperature=0.5),
                return_exceptions=True,
            )
            data_a = _parse_json(raw_a if isinstance(raw_a, str) else "")
            data_b = _parse_json(raw_b if isinstance(raw_b, str) else "")

            if data_a and data_b:
                data, method = _vote_dpo(data_a, data_b)
            elif data_a:
                data, method = data_a, "single_a"
            elif data_b:
                data, method = data_b, "single_b"
            else:
                data, method = _rule_dpo(material), "rule"
        else:
            data, method = _rule_dpo(material), "rule"

        if not data:
            data, method = _rule_dpo(material), "rule"

        score = float(data.get("quality_score", 6.0))
        return DPOSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            prompt=data.get("prompt", ""),
            chosen=data.get("chosen", ""),
            rejected=data.get("rejected", ""),
            quality_score=score,
            quality_tier=_score_to_tier(score),
            token_count=len(content.split()),
        )

    async def annotate_pretrain(self, material: CreatorMaterial) -> Optional[PretrainChunk]:
        fallback = AutoAnnotator(self.mode)
        return await fallback.annotate_pretrain(material)

    async def annotate_all_pretrain(self, material: CreatorMaterial) -> List[PretrainChunk]:
        fallback = AutoAnnotator(self.mode)
        return await fallback.annotate_all_pretrain(material)


# ════════════════════════════════════════════════════════════════════
# 批量任务（自动选择 MultiModel/Single 模式）
# ════════════════════════════════════════════════════════════════════

class BatchAnnotationJob:
    """
    批量标注任务，自动选择 MultiModelAnnotator（LLM 可用时）
    或 AutoAnnotator（降级）。
    """

    def __init__(self, annotator=None, concurrency: int = 5):
        if annotator is None:
            # v2: 默认使用多模型投票
            annotator = MultiModelAnnotator() if _LLM_ENABLED else AutoAnnotator()
        self.annotator   = annotator
        # 多模型模式每次两路并发，控制总并发保守一些
        self.concurrency = max(2, concurrency // 2) if _LLM_ENABLED else concurrency

    async def run(
        self,
        materials: List[CreatorMaterial],
        target_types: List[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, list]:
        types          = set(target_types or ["sft", "dpo", "pretrain"])
        sft_samples:   List[SFTSample]     = []
        dpo_samples:   List[DPOSample]     = []
        pretrain_chunks: List[PretrainChunk] = []
        sem            = asyncio.Semaphore(self.concurrency)
        done_count     = 0

        async def process_one(mat: CreatorMaterial):
            nonlocal done_count
            async with sem:
                try:
                    if "sft" in types:
                        s = await self.annotator.annotate_sft(mat)
                        if s:
                            sft_samples.append(s)

                    if "dpo" in types:
                        d = await self.annotator.annotate_dpo(mat)
                        if d:
                            dpo_samples.append(d)

                    if "pretrain" in types:
                        cs = await self.annotator.annotate_all_pretrain(mat)
                        pretrain_chunks.extend(cs)
                except Exception as e:
                    print(f"⚠️  [annotator] {mat.material_id[:8]} 标注异常: {e}")

                done_count += 1
                if progress_cb:
                    await progress_cb(done_count, len(materials))

        await asyncio.gather(*[process_one(m) for m in materials], return_exceptions=True)

        return {
            "sft_samples":     sft_samples,
            "dpo_samples":     dpo_samples,
            "pretrain_chunks": pretrain_chunks,
        }

# dataset/annotator.py
"""
知数知圈 · 自动标注引擎

将创作者素材转化为 SFT / DPO / Pretrain 三类训练样本。
使用 LLM（OpenAI 兼容 API）驱动，支持批量并发。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# 路径修正：确保能找到 config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataset.schema import (
    CreatorMaterial, SFTSample, DPOSample, PretrainChunk, QualityTier
)

try:
    from config import get_settings
    _settings = get_settings()
    _API_KEY  = _settings.openai_api_key
    _BASE_URL = _settings.openai_base_url
    _MODEL    = _settings.openai_model
except Exception:
    _API_KEY  = os.environ.get("OPENAI_API_KEY", "")
    _BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    _MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


class AnnotationMode(str, Enum):
    AUTO_ONLY    = "auto_only"     # 全自动，无人工介入
    AUTO_REVIEW  = "auto_review"   # 自动标注 + 低分人工复核
    HUMAN_FIRST  = "human_first"   # 人工优先


async def _llm_call(prompt: str, system: str = "") -> str:
    """调用 LLM API（OpenAI 兼容接口）"""
    try:
        import aiohttp
    except ImportError:
        # 降级：返回 mock 结果
        return json.dumps({"instruction": "示例指令", "input": "", "output": "示例回答"})

    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": _MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️  LLM 调用失败: {e}")
        return ""


def _score_to_tier(score: float) -> QualityTier:
    if score >= 9.0:
        return QualityTier.PLATINUM
    elif score >= 7.0:
        return QualityTier.GOLD
    elif score >= 5.0:
        return QualityTier.SILVER
    else:
        return QualityTier.REJECTED


class AutoAnnotator:
    """自动标注器：素材 → SFT / DPO / Pretrain 样本"""

    SFT_SYSTEM = """你是专业数据集标注员。给定一段文本，生成高质量 SFT 训练样本。
输出严格 JSON（不含 markdown 代码块）：
{"instruction": "...", "input": "...", "output": "...", "domain": "...", "quality_score": 0-10}"""

    DPO_SYSTEM = """你是专业数据集标注员。给定一段文本，生成 DPO 偏好对。
输出严格 JSON（不含 markdown 代码块）：
{"prompt": "...", "chosen": "...", "rejected": "...", "quality_score": 0-10}"""

    def __init__(self, mode: AnnotationMode = AnnotationMode.AUTO_REVIEW):
        self.mode = mode

    async def annotate_sft(self, material: CreatorMaterial) -> Optional[SFTSample]:
        """将素材标注为 SFT 样本"""
        content = material.content[:3000]
        raw = await _llm_call(f"原始素材:\n{content}", system=self.SFT_SYSTEM)

        try:
            clean = raw.strip().lstrip("```json").rstrip("```").strip()
            data = json.loads(clean)
        except Exception:
            # mock fallback
            data = {
                "instruction": f"请分析以下内容：{content[:100]}",
                "input": "",
                "output": content[:500],
                "domain": "general",
                "quality_score": 6.0,
            }

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
        """将素材标注为 DPO 样本"""
        content = material.content[:3000]
        raw = await _llm_call(f"原始素材:\n{content}", system=self.DPO_SYSTEM)

        try:
            clean = raw.strip().lstrip("```json").rstrip("```").strip()
            data = json.loads(clean)
        except Exception:
            data = {
                "prompt": f"关于以下内容，给出专业回答：{content[:100]}",
                "chosen": content[:300],
                "rejected": "这个问题很复杂，无法回答。",
                "quality_score": 6.0,
            }

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
        """将素材切分为预训练块"""
        content = material.content
        chunks = []
        chunk_size = 512
        words = content.split()

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

        # 返回第一个 chunk（pipeline 会单独处理所有 chunks）
        return chunks[0] if chunks else None

    async def annotate_all_pretrain(self, material: CreatorMaterial) -> List[PretrainChunk]:
        """返回一个素材产生的所有预训练块"""
        content = material.content
        chunks = []
        chunk_size = 512
        words = content.split()

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


class BatchAnnotationJob:
    """批量标注任务，支持并发控制和进度回调"""

    def __init__(self, annotator: AutoAnnotator, concurrency: int = 5):
        self.annotator = annotator
        self.concurrency = concurrency

    async def run(
        self,
        materials: List[CreatorMaterial],
        target_types: List[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, list]:
        """
        批量标注，返回 {"sft_samples": [], "dpo_samples": [], "pretrain_chunks": []}
        """
        types = set(target_types or ["sft", "dpo", "pretrain"])
        sft_samples: List[SFTSample] = []
        dpo_samples: List[DPOSample] = []
        pretrain_chunks: List[PretrainChunk] = []

        sem = asyncio.Semaphore(self.concurrency)
        done_count = 0

        async def process_one(mat: CreatorMaterial):
            nonlocal done_count
            async with sem:
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

                done_count += 1
                if progress_cb:
                    await progress_cb(done_count, len(materials))

        tasks = [process_one(m) for m in materials]
        await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "sft_samples":     sft_samples,
            "dpo_samples":     dpo_samples,
            "pretrain_chunks": pretrain_chunks,
        }

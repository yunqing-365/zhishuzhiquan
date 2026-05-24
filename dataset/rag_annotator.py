# dataset/rag_annotator.py
"""
息壤 · RAG 增强标注引擎

解决的核心问题：
  原 AutoAnnotator 盲目调用 LLM，生成的 SFT/DPO 样本
  完全不依赖已有知识库（rag_engine.py），导致：
    - 历史事实错误（LLM 幻觉）
    - 无法利用创作者自己上传的素材库
    - accuracy 维度质检分普遍偏低

本模块做两件事：
  1. 标注前从知识库检索相关上下文，注入 prompt
  2. 标注后对 output 做事实核查（与检索结果对比）

使用 rag_engine.KnowledgeRetriever（已有），无需额外依赖。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dataset.annotator import AutoAnnotator, AnnotationMode, _SFT_GENERATION_PROMPT
from dataset.schema import (
    AnnotationStatus, CreatorMaterial, DPOSample, SFTSample,
)
from config import get_settings
from openai import AsyncOpenAI

_settings = get_settings()
_client   = AsyncOpenAI(api_key=_settings.API_KEY, base_url=_settings.BASE_URL)


# ════════════════════════════════════════════════════════
# RAG 增强 Prompt 模板
# ════════════════════════════════════════════════════════

_RAG_SFT_PROMPT = """\
你是一个专业的AI训练数据标注专家，拥有丰富的历史文化知识。
请结合以下【参考知识】，对给定素材生成高质量SFT训练样本。

【参考知识（来自知识库，请优先参考）】
{rag_context}

【原始素材】
{raw_content}

【素材领域】{domain}

要求：
1. instruction 必须基于素材核心内容，不得脱离素材主旨
2. output 必须与参考知识保持事实一致；如参考知识与素材有出入，以参考知识为准
3. 若参考知识无相关内容，则依据素材本身，但要谨慎措辞
4. 生成简洁完整的 system_prompt
5. 评估难度 1-5 和标注置信度 0-1

请严格按以下JSON格式输出（不要有额外文字）：
{{
  "system_prompt": "...",
  "instruction": "...",
  "input": "...",
  "output": "...",
  "difficulty": 3,
  "annotation_confidence": 0.85,
  "rag_grounded": true
}}
"""

_RAG_DPO_PROMPT = """\
你是一个专业的AI偏好数据标注专家，拥有严谨的事实核查能力。

【参考知识（来自知识库）】
{rag_context}

【原始素材】
{raw_content}

请生成一对DPO训练样本：
- chosen: 事实准确、逻辑清晰、参考知识一致的高质量回答
- rejected: 包含一个轻微事实错误或不够精确的回答（不要太明显）
- 解释为何 chosen 更好

请严格按以下JSON格式输出：
{{
  "prompt": "...",
  "chosen": "...",
  "rejected": "...",
  "preference_reason": "..."
}}
"""

_FACTCHECK_PROMPT = """\
请对以下AI生成的回答进行事实核查。

【参考知识】
{rag_context}

【问题】
{instruction}

【待核查的回答】
{output}

请判断回答与参考知识是否一致，并给出核查结论。
仅输出JSON（不要其他文字）：
{{
  "is_consistent": true,
  "confidence": 0.9,
  "issues": [],
  "corrected_output": null
}}

issues 填写发现的具体事实错误列表（无问题则为空列表）。
若有错误，在 corrected_output 给出修正版本；无错误则为 null。
"""


# ════════════════════════════════════════════════════════
# RAG 增强标注器
# ════════════════════════════════════════════════════════

class RAGAnnotator(AutoAnnotator):
    """
    继承 AutoAnnotator，在标注前注入知识库上下文。

    new args:
      era_name:         知识库名称（对应 data/knowledge/{era_name}/）
      do_factcheck:     标注后是否做事实核查（慢但准）
      rag_top_k:        每次检索的上下文片段数量
    """

    def __init__(
        self,
        era_name:     str = "song",
        mode:         AnnotationMode = AnnotationMode.AUTO_REVIEW,
        review_threshold: float = 0.75,
        do_factcheck: bool = True,
        rag_top_k:    int = 3,
    ):
        super().__init__(mode=mode, review_threshold=review_threshold)
        self.era_name     = era_name
        self.do_factcheck = do_factcheck
        self.rag_top_k    = rag_top_k
        self._retriever   = None   # lazy init

    def _get_retriever(self):
        """惰性初始化检索器，避免启动时阻塞"""
        if self._retriever is None:
            try:
                from rag_engine import KnowledgeRetriever
                self._retriever = KnowledgeRetriever(era_name=self.era_name)
                print(f"✅ RAG 检索器初始化成功（era: {self.era_name}）")
            except Exception as e:
                print(f"⚠️  RAG 检索器初始化失败，降级为无 RAG 模式: {e}")
                self._retriever = None
        return self._retriever

    # ── RAG 检索（同步包装为异步）──────────────────────

    async def _rag_retrieve(self, query: str) -> str:
        """检索知识库，返回拼接好的上下文字符串"""
        retriever = self._get_retriever()
        if retriever is None:
            return "（知识库不可用，请基于素材本身进行标注）"

        try:
            # KnowledgeRetriever.retrieve 是同步的，用 executor 包装
            loop = asyncio.get_event_loop()
            context = await loop.run_in_executor(
                None,
                lambda: retriever.retrieve(
                    query=query[:500],
                    top_k=self.rag_top_k,
                )
            )
            return context or "（未检索到相关知识）"
        except Exception as e:
            print(f"⚠️  RAG 检索异常: {e}")
            return "（检索异常，请基于素材本身）"

    # ── SFT 标注（覆盖父类）────────────────────────────

    async def _annotate_sft(self, material: CreatorMaterial) -> Optional[SFTSample]:
        # 1. 用素材前 300 字作为检索 query
        query   = material.raw_content[:300]
        context = await self._rag_retrieve(query)

        prompt = _RAG_SFT_PROMPT.format(
            rag_context=context[:2000],
            raw_content=material.raw_content[:2500],
            domain=material.metadata.get("domain", "通用"),
        )

        raw    = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)
        if not parsed:
            return None

        confidence = float(parsed.get("annotation_confidence", 0.5))
        status     = AnnotationStatus.AUTO

        if self.mode == AnnotationMode.AUTO_REVIEW and confidence < self.review_threshold:
            status = AnnotationStatus.PENDING

        sample = SFTSample(
            material_id   = material.material_id,
            creator_id    = material.creator_id,
            system_prompt = parsed.get("system_prompt", ""),
            instruction   = parsed.get("instruction", ""),
            input_context = parsed.get("input", ""),
            output        = parsed.get("output", ""),
            domain        = material.metadata.get("domain", ""),
            difficulty    = int(parsed.get("difficulty", 3)),
            status        = status,
            annotator_id  = f"rag_annotator_v1|era={self.era_name}",
        )
        sample.compute_hash()

        # 2. 可选：事实核查并自动修正 output
        if self.do_factcheck and context and len(context) > 50:
            sample = await self._factcheck_and_correct(sample, context)

        return sample

    # ── DPO 标注（覆盖父类）────────────────────────────

    async def _annotate_dpo(self, material: CreatorMaterial) -> Optional[DPOSample]:
        query   = material.raw_content[:300]
        context = await self._rag_retrieve(query)

        prompt = _RAG_DPO_PROMPT.format(
            rag_context=context[:2000],
            raw_content=material.raw_content[:2500],
        )

        raw    = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)
        if not parsed:
            return None

        return DPOSample(
            material_id       = material.material_id,
            creator_id        = material.creator_id,
            prompt            = parsed.get("prompt", ""),
            chosen            = parsed.get("chosen", ""),
            rejected          = parsed.get("rejected", ""),
            preference_reason = parsed.get("preference_reason", ""),
            domain            = material.metadata.get("domain", ""),
            status            = AnnotationStatus.AUTO,
        )

    # ── 事实核查 ───────────────────────────────────────

    async def _factcheck_and_correct(
        self,
        sample:  SFTSample,
        context: str,
    ) -> SFTSample:
        """
        对 output 做事实核查，自动修正错误内容。
        若无法确认一致性，保持原 output 不变（宁可放过，不误杀）。
        """
        prompt = _FACTCHECK_PROMPT.format(
            rag_context=context[:1500],
            instruction=sample.instruction,
            output=sample.output[:1000],
        )

        try:
            raw    = await self._llm_call(prompt)
            result = self._safe_parse_json(raw)
            if not result:
                return sample

            is_consistent = result.get("is_consistent", True)
            issues        = result.get("issues", [])
            corrected     = result.get("corrected_output")

            if not is_consistent and corrected and len(corrected) > 50:
                # 自动修正 output
                sample.output      = corrected
                sample.annotator_id += "|factchecked"
                sample.compute_hash()
                if issues:
                    print(f"  🔍 事实核查修正: {'; '.join(issues[:2])}")

        except Exception as e:
            print(f"  ⚠️  事实核查异常（跳过）: {e}")

        return sample

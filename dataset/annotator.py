# dataset/annotator.py
"""
息壤 · 智能标注引擎

核心流程：
  原始素材
    ↓
  素材解析（文本 / 图文 / 结构化）
    ↓
  自动标注（LLM 生成 instruction / output / 系统提示）
    ↓
  自动质检预评分
    ↓
  人工复核队列（仅对低置信度样本）
    ↓
  最终入库

支持三种标注模式：
  - AUTO_ONLY:   全自动，速度快，适合海量低成本语料
  - AUTO_REVIEW: 自动生成 + 低分样本推人工复核（推荐）
  - HUMAN_ONLY:  纯人工，适合高价值专业数据集
"""

from __future__ import annotations

import asyncio
import json
import re
from enum import Enum
from typing import AsyncGenerator, List, Optional, Tuple

from openai import AsyncOpenAI

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_settings
from dataset.schema import (
    AnnotationStatus, CreatorMaterial, DPOSample,
    MultimodalSample, PretrainChunk, QualityTier, SFTSample,
)

_settings = get_settings()
_client = AsyncOpenAI(api_key=_settings.API_KEY, base_url=_settings.BASE_URL)


class AnnotationMode(str, Enum):
    AUTO_ONLY   = "auto_only"
    AUTO_REVIEW = "auto_review"
    HUMAN_ONLY  = "human_only"


# ════════════════════════════════════════════════════════
# Prompt 模板
# ════════════════════════════════════════════════════════

_SFT_GENERATION_PROMPT = """\
你是一个专业的AI训练数据标注专家。给定以下原始素材，请生成高质量的SFT训练样本。

【原始素材】
{raw_content}

【素材领域】{domain}

【要求】
1. 生成一个自然的用户指令（instruction），让这段素材成为最佳回答
2. 如有需要，生成必要的输入上下文（input），可以为空
3. 生成一个简洁完整的系统提示（system_prompt）
4. 对输出进行必要的优化和扩展，使其成为示范级回答（output）
5. 评估难度等级 1-5（1=基础知识，5=专家级推理）

请严格按照以下JSON格式输出，不要有任何额外文字：
{{
  "system_prompt": "...",
  "instruction": "...",
  "input": "...",
  "output": "...",
  "difficulty": 3,
  "annotation_confidence": 0.85
}}
"""

_DPO_GENERATION_PROMPT = """\
你是一个专业的AI偏好数据标注专家。给定以下原始素材，请生成一对DPO训练样本。

【原始素材】
{raw_content}

要求：
1. 生成一个有一定挑战性的用户问题（prompt）
2. 生成一个高质量、详细、准确的回答（chosen）—— 这是更好的回答
3. 生成一个有明显缺陷的回答（rejected）—— 可以是不完整、不准确、过于简短或态度有问题
4. 解释为何 chosen 更好（preference_reason）

请严格按照以下JSON格式输出：
{{
  "prompt": "...",
  "chosen": "...",
  "rejected": "...",
  "preference_reason": "..."
}}
"""

_MULTIMODAL_CAPTION_PROMPT = """\
你是一个专业的多模态数据标注专家。请为这张图片生成高质量标注：

1. 详细描述图片内容（caption），不少于100字
2. 生成3组有价值的问答对（qa_pairs），问题要有深度

请严格按照以下JSON格式输出：
{{
  "caption": "...",
  "qa_pairs": [
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}}
  ]
}}
"""


# ════════════════════════════════════════════════════════
# 核心标注引擎
# ════════════════════════════════════════════════════════

class AutoAnnotator:
    """
    自动标注引擎
    针对不同类型素材，调用不同的标注策略
    """

    def __init__(self, mode: AnnotationMode = AnnotationMode.AUTO_REVIEW,
                 review_threshold: float = 0.75):
        self.mode = mode
        self.review_threshold = review_threshold  # 低于此置信度 → 推人工

    # ── 主入口 ─────────────────────────────────────────

    async def annotate_material(
        self,
        material: CreatorMaterial,
        target_types: List[str] = None,   # ["sft", "dpo", "pretrain"]
    ) -> dict:
        """
        对单条素材进行完整标注，返回各类样本列表

        Returns:
          {
            "sft_samples":    List[SFTSample],
            "dpo_samples":    List[DPOSample],
            "pretrain_chunks": List[PretrainChunk],
            "multimodal":     List[MultimodalSample],
            "material_id":    str,
          }
        """
        if target_types is None:
            target_types = ["sft", "dpo", "pretrain"]

        results = {
            "material_id": material.material_id,
            "sft_samples": [],
            "dpo_samples": [],
            "pretrain_chunks": [],
            "multimodal": [],
        }

        # 并发执行多种标注任务
        tasks = []
        if "sft" in target_types:
            tasks.append(self._annotate_sft(material))
        if "dpo" in target_types:
            tasks.append(self._annotate_dpo(material))
        if "pretrain" in target_types:
            tasks.append(self._annotate_pretrain(material))
        if "multimodal" in target_types and material.material_type == "image":
            tasks.append(self._annotate_multimodal(material))

        outputs = await asyncio.gather(*tasks, return_exceptions=True)

        for out in outputs:
            if isinstance(out, Exception):
                print(f"⚠️  标注任务失败: {out}")
                continue
            if isinstance(out, SFTSample):
                results["sft_samples"].append(out)
            elif isinstance(out, DPOSample):
                results["dpo_samples"].append(out)
            elif isinstance(out, list) and out and isinstance(out[0], PretrainChunk):
                results["pretrain_chunks"].extend(out)
            elif isinstance(out, MultimodalSample):
                results["multimodal"].append(out)

        return results

    # ── SFT 标注 ────────────────────────────────────────

    async def _annotate_sft(self, material: CreatorMaterial) -> Optional[SFTSample]:
        prompt = _SFT_GENERATION_PROMPT.format(
            raw_content=material.raw_content[:3000],
            domain=material.metadata.get("domain", "通用"),
        )

        raw = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)
        if not parsed:
            return None

        confidence = float(parsed.get("annotation_confidence", 0.5))
        status = AnnotationStatus.AUTO

        # 置信度低 → 标记需人工复核
        if self.mode == AnnotationMode.AUTO_REVIEW and confidence < self.review_threshold:
            status = AnnotationStatus.PENDING

        sample = SFTSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            system_prompt=parsed.get("system_prompt", ""),
            instruction=parsed.get("instruction", ""),
            input_context=parsed.get("input", ""),
            output=parsed.get("output", ""),
            domain=material.metadata.get("domain", ""),
            difficulty=int(parsed.get("difficulty", 3)),
            status=status,
            annotator_id="auto_annotator_v1",
        )
        sample.compute_hash()
        return sample

    # ── DPO 标注 ────────────────────────────────────────

    async def _annotate_dpo(self, material: CreatorMaterial) -> Optional[DPOSample]:
        prompt = _DPO_GENERATION_PROMPT.format(
            raw_content=material.raw_content[:3000],
        )
        raw = await self._llm_call(prompt)
        parsed = self._safe_parse_json(raw)
        if not parsed:
            return None

        sample = DPOSample(
            material_id=material.material_id,
            creator_id=material.creator_id,
            prompt=parsed.get("prompt", ""),
            chosen=parsed.get("chosen", ""),
            rejected=parsed.get("rejected", ""),
            preference_reason=parsed.get("preference_reason", ""),
            domain=material.metadata.get("domain", ""),
            status=AnnotationStatus.AUTO,
        )
        return sample

    # ── 预训练语料切块 ───────────────────────────────────

    async def _annotate_pretrain(self, material: CreatorMaterial) -> List[PretrainChunk]:
        """将长文本切成预训练 chunk，无需 LLM，纯规则处理"""
        import re as _re

        text = material.raw_content
        # 按段落切割，合并到 ~512 tokens（约 800 汉字）
        paragraphs = [p.strip() for p in _re.split(r'\n{2,}', text) if p.strip()]

        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) < 800:
                current += para + "\n"
            else:
                if current:
                    chunk = PretrainChunk(
                        material_id=material.material_id,
                        creator_id=material.creator_id,
                        text=current.strip(),
                        token_count=len(current) // 2,   # 粗估汉字/token ~= 2
                        domain=material.metadata.get("domain", ""),
                        status=AnnotationStatus.AUTO,
                    )
                    chunks.append(chunk)
                current = para + "\n"

        if current:
            chunk = PretrainChunk(
                material_id=material.material_id,
                creator_id=material.creator_id,
                text=current.strip(),
                token_count=len(current) // 2,
                domain=material.metadata.get("domain", ""),
                status=AnnotationStatus.AUTO,
            )
            chunks.append(chunk)

        return chunks

    # ── 多模态标注 ──────────────────────────────────────

    async def _annotate_multimodal(self, material: CreatorMaterial) -> Optional[MultimodalSample]:
        # 图片标注需 vision 模型
        try:
            resp = await _client.chat.completions.create(
                model=_settings.MODEL_NAME,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{material.raw_content}"}},
                        {"type": "text", "text": _MULTIMODAL_CAPTION_PROMPT},
                    ]
                }]
            )
            raw = resp.choices[0].message.content
            parsed = self._safe_parse_json(raw)
            if not parsed:
                return None

            sample = MultimodalSample(
                material_id=material.material_id,
                creator_id=material.creator_id,
                image_path=material.source_path,
                caption=parsed.get("caption", ""),
                qa_pairs=parsed.get("qa_pairs", []),
                domain=material.metadata.get("domain", ""),
                status=AnnotationStatus.AUTO,
            )
            return sample
        except Exception as e:
            print(f"⚠️  多模态标注失败: {e}")
            return None

    # ── 工具函数 ────────────────────────────────────────

    async def _llm_call(self, prompt: str) -> str:
        try:
            resp = await _client.chat.completions.create(
                model=_settings.MODEL_NAME,
                max_tokens=1500,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"❌ LLM 标注调用失败: {e}")
            return ""

    @staticmethod
    def _safe_parse_json(text: str) -> Optional[dict]:
        """鲁棒 JSON 解析（兼容 LLM 输出的 markdown code block）"""
        if not text:
            return None
        # 去除 markdown 代码块
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()
        # 找到第一个 { ... }
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None


# ════════════════════════════════════════════════════════
# 批量标注任务调度
# ════════════════════════════════════════════════════════

class BatchAnnotationJob:
    """
    批量标注任务
    支持并发控制（避免 API 过载）和进度跟踪
    """

    def __init__(self, annotator: AutoAnnotator, concurrency: int = 5):
        self.annotator = annotator
        self.concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(
        self,
        materials: List[CreatorMaterial],
        target_types: List[str] = None,
        progress_callback=None,
    ) -> dict:
        """
        批量标注，返回汇总结果

        progress_callback(done, total) 每完成一条素材时回调
        """
        total = len(materials)
        done = 0
        all_results = {
            "sft_samples": [],
            "dpo_samples": [],
            "pretrain_chunks": [],
            "multimodal": [],
            "errors": [],
        }

        async def _process_one(mat: CreatorMaterial):
            nonlocal done
            async with self._semaphore:
                try:
                    result = await self.annotator.annotate_material(mat, target_types)
                    return result
                except Exception as e:
                    return {"error": str(e), "material_id": mat.material_id}

        tasks = [_process_one(m) for m in materials]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            done += 1

            if "error" in result:
                all_results["errors"].append(result)
            else:
                all_results["sft_samples"].extend(result.get("sft_samples", []))
                all_results["dpo_samples"].extend(result.get("dpo_samples", []))
                all_results["pretrain_chunks"].extend(result.get("pretrain_chunks", []))
                all_results["multimodal"].extend(result.get("multimodal", []))

            if progress_callback:
                await progress_callback(done, total)

        print(f"✅ 批量标注完成: {total} 条素材 → "
              f"SFT {len(all_results['sft_samples'])} / "
              f"DPO {len(all_results['dpo_samples'])} / "
              f"Pretrain {len(all_results['pretrain_chunks'])} chunks / "
              f"多模态 {len(all_results['multimodal'])} / "
              f"错误 {len(all_results['errors'])}")
        return all_results

# dataset/schema.py
"""
息壤 · 数据集核心数据模型

业务链路：创作者素材 → 标注 → 质检 → 去重 → 打包 → 企业交付 → 分润

数据集类型支持：
  - SFT（Supervised Fine-Tuning）：instruction / input / output 三元组
  - RLHF / DPO：chosen / rejected 对比对
  - 预训练语料：纯文本 passage
  - 多模态：图文对（image + caption/QA）
  - 知识图谱：实体-关系三元组
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════
# 枚举定义
# ════════════════════════════════════════════════════════

class DatasetType(str, Enum):
    SFT           = "sft"           # 指令微调
    DPO           = "dpo"           # 直接偏好优化
    PRETRAIN      = "pretrain"      # 预训练语料
    MULTIMODAL    = "multimodal"    # 多模态图文
    KNOWLEDGE_GRAPH = "knowledge_graph"  # 知识图谱三元组


class AnnotationStatus(str, Enum):
    PENDING   = "pending"    # 待标注
    AUTO      = "auto"       # 已自动标注（待人工复核）
    REVIEWED  = "reviewed"   # 人工已复核
    APPROVED  = "approved"   # 已通过质检
    REJECTED  = "rejected"   # 已拒绝


class QualityTier(str, Enum):
    PLATINUM = "platinum"   # 铂金：9-10分，旗舰数据集
    GOLD     = "gold"       # 黄金：7-8分，高质量
    SILVER   = "silver"     # 白银：5-6分，标准
    BRONZE   = "bronze"     # 青铜：3-4分，需人工改进
    DISCARD  = "discard"    # 丢弃：< 3分


# ════════════════════════════════════════════════════════
# 原始素材（创作者上传）
# ════════════════════════════════════════════════════════

@dataclass
class CreatorMaterial:
    """创作者上传的原始素材"""
    material_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    creator_id:    str = ""
    source_path:   str = ""          # 文件路径或URL
    material_type: str = ""          # text / image / video / audio / structured
    raw_content:   str = ""          # 文本内容（图片则为base64或路径）
    metadata:      Dict[str, Any] = field(default_factory=dict)
    uploaded_at:   datetime = field(default_factory=datetime.utcnow)

    # 内容指纹（用于去重）
    content_hash:  str = ""

    def compute_hash(self) -> str:
        self.content_hash = hashlib.sha256(
            self.raw_content.encode("utf-8", errors="ignore")
        ).hexdigest()
        return self.content_hash


# ════════════════════════════════════════════════════════
# SFT 样本
# ════════════════════════════════════════════════════════

@dataclass
class SFTSample:
    """指令微调样本（instruction-following）"""
    sample_id:        str = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:      str = ""          # 来源素材 ID
    creator_id:       str = ""
    system_prompt:    str = ""
    instruction:      str = ""
    input_context:    str = ""          # 可选的上下文补充
    output:           str = ""          # 金标准回答
    domain:           str = ""          # 领域标签（历史/法律/医疗等）
    language:         str = "zh"
    difficulty:       int = 3           # 1-5 难度
    status:           AnnotationStatus = AnnotationStatus.PENDING
    quality_score:    float = 0.0
    quality_tier:     QualityTier = QualityTier.BRONZE
    quality_detail:   Dict[str, float] = field(default_factory=dict)
    annotator_id:     str = ""          # 标注者（auto 或 human UID）
    reviewed_by:      str = ""
    created_at:       datetime = field(default_factory=datetime.utcnow)
    updated_at:       datetime = field(default_factory=datetime.utcnow)
    content_hash:     str = ""

    def compute_hash(self) -> str:
        content = f"{self.instruction}|{self.input_context}|{self.output}"
        self.content_hash = hashlib.sha256(content.encode()).hexdigest()
        return self.content_hash

    def to_hf_dict(self) -> Dict:
        """导出为 HuggingFace datasets 兼容格式"""
        return {
            "system":      self.system_prompt,
            "instruction": self.instruction,
            "input":       self.input_context,
            "output":      self.output,
            "domain":      self.domain,
            "language":    self.language,
            "difficulty":  self.difficulty,
        }

    def to_alpaca_dict(self) -> Dict:
        """导出为 Alpaca 格式"""
        return {
            "instruction": self.instruction,
            "input":       self.input_context,
            "output":      self.output,
        }

    def to_sharegpt_dict(self) -> Dict:
        """导出为 ShareGPT / ChatML 格式"""
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.append({"role": "user", "content":
                     f"{self.instruction}\n{self.input_context}".strip()})
        msgs.append({"role": "assistant", "content": self.output})
        return {"conversations": msgs}


# ════════════════════════════════════════════════════════
# DPO 偏好对
# ════════════════════════════════════════════════════════

@dataclass
class DPOSample:
    """DPO / RLHF 偏好对比样本"""
    sample_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:   str = ""
    creator_id:    str = ""
    prompt:        str = ""
    chosen:        str = ""          # 更好的回答
    rejected:      str = ""          # 较差的回答
    preference_reason: str = ""      # 解释为何 chosen 更好
    domain:        str = ""
    status:        AnnotationStatus = AnnotationStatus.PENDING
    quality_score: float = 0.0
    created_at:    datetime = field(default_factory=datetime.utcnow)

    def to_hf_dict(self) -> Dict:
        return {
            "prompt":   self.prompt,
            "chosen":   self.chosen,
            "rejected": self.rejected,
        }


# ════════════════════════════════════════════════════════
# 预训练语料片段
# ════════════════════════════════════════════════════════

@dataclass
class PretrainChunk:
    """预训练语料片段"""
    chunk_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:    str = ""
    creator_id:     str = ""
    text:           str = ""
    token_count:    int = 0
    domain:         str = ""
    language:       str = "zh"
    perplexity:     float = 0.0      # 困惑度（越低质量越高）
    dedup_hash:     str = ""
    quality_score:  float = 0.0
    status:         AnnotationStatus = AnnotationStatus.PENDING
    created_at:     datetime = field(default_factory=datetime.utcnow)

    def to_hf_dict(self) -> Dict:
        return {"text": self.text, "domain": self.domain}


# ════════════════════════════════════════════════════════
# 多模态图文对
# ════════════════════════════════════════════════════════

@dataclass
class MultimodalSample:
    """图文对样本（VLM训练用）"""
    sample_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:   str = ""
    creator_id:    str = ""
    image_path:    str = ""
    image_url:     str = ""
    caption:       str = ""          # 图像描述
    qa_pairs:      List[Dict] = field(default_factory=list)  # [{"q":..,"a":..}]
    domain:        str = ""
    status:        AnnotationStatus = AnnotationStatus.PENDING
    quality_score: float = 0.0
    created_at:    datetime = field(default_factory=datetime.utcnow)


# ════════════════════════════════════════════════════════
# 数据集包（打包交付单元）
# ════════════════════════════════════════════════════════

@dataclass
class DatasetPackage:
    """交付给企业客户的数据集包"""
    package_id:      str = field(default_factory=lambda: str(uuid.uuid4()))
    name:            str = ""
    description:     str = ""
    dataset_type:    DatasetType = DatasetType.SFT
    version:         str = "1.0.0"
    domain:          str = ""
    language:        str = "zh"

    # 统计
    total_samples:   int = 0
    approved_samples: int = 0
    avg_quality:     float = 0.0
    platinum_count:  int = 0
    gold_count:      int = 0

    # 参与的创作者及其贡献权重
    creator_contributions: Dict[str, float] = field(default_factory=dict)
    # {creator_id: contribution_ratio (0~1, 所有人加和=1)}

    # 价格
    price_cny:       float = 0.0
    license_type:    str = "enterprise_internal"

    # 文件
    export_paths:    Dict[str, str] = field(default_factory=dict)
    # {"jsonl": "...", "parquet": "...", "zip": "..."}

    created_at:      datetime = field(default_factory=datetime.utcnow)
    published_at:    Optional[datetime] = None


# ════════════════════════════════════════════════════════
# 分润记录
# ════════════════════════════════════════════════════════

@dataclass
class RevenueRecord:
    """创作者分润记录"""
    record_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    package_id:      str = ""
    creator_id:      str = ""
    total_revenue:   float = 0.0     # 该包总收入
    contribution_ratio: float = 0.0  # 贡献占比
    creator_share:   float = 0.0     # 创作者应得金额
    platform_fee:    float = 0.0     # 平台手续费
    status:          str = "pending" # pending / paid / processing
    created_at:      datetime = field(default_factory=datetime.utcnow)
    paid_at:         Optional[datetime] = None

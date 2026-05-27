# dataset/schema.py
"""
知数知圈 · 数据集核心数据模型

所有跨模块共享的 dataclass / enum 定义在此，避免循环导入。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


# ════════════════════════════════════════════════════════
# 枚举
# ════════════════════════════════════════════════════════

class DatasetType(str, Enum):
    SFT      = "sft"
    DPO      = "dpo"
    PRETRAIN = "pretrain"

class AnnotationStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    SKIPPED   = "skipped"

class QualityTier(str, Enum):
    PLATINUM = "platinum"   # ≥ 9.0
    GOLD     = "gold"       # ≥ 7.0
    SILVER   = "silver"     # ≥ 5.0
    REJECTED = "rejected"   # < 5.0


# ════════════════════════════════════════════════════════
# 创作者素材（上游输入）
# ════════════════════════════════════════════════════════

@dataclass
class CreatorMaterial:
    """创作者上传的原始素材（文本 / 图文 / 音频 / 视频）"""
    material_id:  str  = field(default_factory=lambda: str(uuid.uuid4()))
    creator_id:   str  = ""
    content_type: str  = "text"          # text / image / audio / video
    content:      str  = ""              # 文本内容或 base64 编码
    metadata:     dict = field(default_factory=dict)
    uploaded_at:  datetime = field(default_factory=datetime.utcnow)


# ════════════════════════════════════════════════════════
# 标注产物
# ════════════════════════════════════════════════════════

@dataclass
class SFTSample:
    """监督微调样本"""
    sample_id:       str   = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:     str   = ""
    creator_id:      str   = ""
    package_id:      str   = ""   # 所属数据集包 ID（打包后回填）
    instruction:     str   = ""
    input:           str   = ""
    output:          str   = ""
    quality_score:   float = 0.0
    quality_tier:    QualityTier = QualityTier.SILVER
    domain:          str   = ""
    language:        str   = "zh"
    token_count:     int   = 0
    annotation_meta: dict  = field(default_factory=dict)

@dataclass
class DPOSample:
    """直接偏好优化样本"""
    sample_id:    str   = field(default_factory=lambda: str(uuid.uuid4()))
    material_id:  str   = ""
    creator_id:   str   = ""
    package_id:   str   = ""   # 所属数据集包 ID（打包后回填）
    prompt:       str   = ""
    chosen:       str   = ""
    rejected:     str   = ""
    quality_score: float = 0.0
    quality_tier:  QualityTier = QualityTier.SILVER
    domain:       str   = ""
    language:     str   = "zh"
    token_count:  int   = 0

@dataclass
class PretrainChunk:
    """预训练文本块"""
    chunk_id:    str   = field(default_factory=lambda: str(uuid.uuid4()))
    material_id: str   = ""
    creator_id:  str   = ""
    text:        str   = ""
    quality_score: float = 0.0
    domain:      str   = ""
    language:    str   = "zh"
    token_count: int   = 0
    source_url:  str   = ""


# ════════════════════════════════════════════════════════
# 数据集包（生产产物）
# ════════════════════════════════════════════════════════

@dataclass
class DatasetPackage:
    """打包完成的可交付数据集"""
    package_id:   str  = field(default_factory=lambda: str(uuid.uuid4()))
    name:         str  = ""
    description:  str  = ""
    version:      str  = "1.0.0"
    total_samples: int  = 0
    sft_count:    int  = 0
    dpo_count:    int  = 0
    pretrain_count: int = 0
    avg_quality:  float = 0.0
    price_cny:    float = 0.0
    license_type: str  = "enterprise_internal"
    export_paths: Dict[str, str] = field(default_factory=dict)
    creator_contributions: Dict[str, float] = field(default_factory=dict)
    created_at:   datetime = field(default_factory=datetime.utcnow)
    metadata:     dict = field(default_factory=dict)


# ════════════════════════════════════════════════════════
# 分润记录
# ════════════════════════════════════════════════════════

@dataclass
class RevenueRecord:
    """创作者分润流水记录"""
    record_id:          str   = field(default_factory=lambda: str(uuid.uuid4()))
    package_id:         str   = ""
    creator_id:         str   = ""
    total_revenue:      float = 0.0
    contribution_ratio: float = 0.0
    creator_share:      float = 0.0
    platform_fee:       float = 0.0
    status:             str   = "pending"    # pending / paid / failed
    created_at:         datetime = field(default_factory=datetime.utcnow)
    paid_at:            Optional[datetime] = None

# dataset/packager.py
"""
知数知圈 · 数据集打包器

将清洗、去重后的样本打包为可交付格式：
  - JSONL（HuggingFace 兼容）
  - ZIP（含 README、数据卡、样本文件）

同时计算创作者贡献权重（用于分润）。
"""
from __future__ import annotations

import json
import os
import uuid
import zipfile
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from dataset.schema import (
    DatasetPackage, SFTSample, DPOSample, PretrainChunk, QualityTier
)

try:
    from config import get_settings
    _OUTPUT_DIR = get_settings().dataset_output_dir
except Exception:
    _OUTPUT_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "datasets"
    )

os.makedirs(_OUTPUT_DIR, exist_ok=True)

# 贡献权重参数
TIER_WEIGHTS = {
    QualityTier.PLATINUM: 3.0,
    QualityTier.GOLD:     2.0,
    QualityTier.SILVER:   1.0,
    QualityTier.REJECTED: 0.0,
}
TYPE_WEIGHTS = {"sft": 1.5, "dpo": 1.8, "pretrain": 0.8}


class DatasetPackager:
    """数据集打包器"""

    def pack(
        self,
        name:            str,
        description:     str,
        sft_samples:     List[SFTSample],
        dpo_samples:     List[DPOSample],
        pretrain_chunks: List[PretrainChunk],
        formats:         List[str] = None,
        min_quality:     float = 5.0,
        price_cny:       float = 0.0,
        license_type:    str = "enterprise_internal",
    ) -> DatasetPackage:
        """打包并导出数据集"""
        formats = formats or ["jsonl", "zip"]
        package_id = str(uuid.uuid4())
        pkg_dir = os.path.join(_OUTPUT_DIR, package_id)
        os.makedirs(pkg_dir, exist_ok=True)

        total = len(sft_samples) + len(dpo_samples) + len(pretrain_chunks)
        all_scores = (
            [s.quality_score for s in sft_samples] +
            [s.quality_score for s in dpo_samples] +
            [s.quality_score for s in pretrain_chunks]
        )
        avg_quality = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

        export_paths: Dict[str, str] = {}

        # ── 导出 JSONL ──────────────────────────────────
        if "jsonl" in formats:
            sft_path = os.path.join(pkg_dir, "sft_data.jsonl")
            with open(sft_path, "w", encoding="utf-8") as f:
                for s in sft_samples:
                    f.write(json.dumps({
                        "instruction": s.instruction,
                        "input": s.input,
                        "output": s.output,
                    }, ensure_ascii=False) + "\n")
            export_paths["sft_jsonl"] = sft_path

            dpo_path = os.path.join(pkg_dir, "dpo_data.jsonl")
            with open(dpo_path, "w", encoding="utf-8") as f:
                for s in dpo_samples:
                    f.write(json.dumps({
                        "prompt": s.prompt,
                        "chosen": s.chosen,
                        "rejected": s.rejected,
                    }, ensure_ascii=False) + "\n")
            export_paths["dpo_jsonl"] = dpo_path

            pretrain_path = os.path.join(pkg_dir, "pretrain_data.jsonl")
            with open(pretrain_path, "w", encoding="utf-8") as f:
                for c in pretrain_chunks:
                    f.write(json.dumps({"text": c.text}, ensure_ascii=False) + "\n")
            export_paths["pretrain_jsonl"] = pretrain_path

        # ── 写 README ────────────────────────────────────
        readme_path = os.path.join(pkg_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n\n")
            f.write(f"{description}\n\n")
            f.write(f"- 总样本数：{total}\n")
            f.write(f"- SFT：{len(sft_samples)} | DPO：{len(dpo_samples)} | Pretrain：{len(pretrain_chunks)}\n")
            f.write(f"- 平均质量分：{avg_quality}/10\n")
            f.write(f"- 许可证：{license_type}\n")
            f.write(f"- 生产时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

        # ── 打 ZIP ───────────────────────────────────────
        if "zip" in formats:
            zip_path = os.path.join(_OUTPUT_DIR, f"{package_id}.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in os.listdir(pkg_dir):
                    zf.write(os.path.join(pkg_dir, fname), fname)
            export_paths["zip"] = zip_path

        # ── 计算创作者贡献 ────────────────────────────────
        contributions = self._calc_contributions(sft_samples, dpo_samples, pretrain_chunks)

        package = DatasetPackage(
            package_id=package_id,
            name=name,
            description=description,
            total_samples=total,
            sft_count=len(sft_samples),
            dpo_count=len(dpo_samples),
            pretrain_count=len(pretrain_chunks),
            avg_quality=avg_quality,
            price_cny=price_cny,
            license_type=license_type,
            export_paths=export_paths,
            creator_contributions=contributions,
        )

        # 写 manifest
        manifest_path = os.path.join(pkg_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "package_id":   package.package_id,
                "name":         package.name,
                "total_samples": package.total_samples,
                "avg_quality":  package.avg_quality,
                "export_paths": package.export_paths,
                "creator_contributions": package.creator_contributions,
                "created_at":   package.created_at.isoformat(),
            }, f, ensure_ascii=False, indent=2)

        print(f"📦 数据集打包完成: {package_id}")
        print(f"   总样本: {total} | 平均质量: {avg_quality}")
        return package

    def _calc_contributions(
        self,
        sft:      List[SFTSample],
        dpo:      List[DPOSample],
        pretrain: List[PretrainChunk],
    ) -> Dict[str, float]:
        """计算每位创作者的贡献权重（归一化到 0~1）"""
        raw: Dict[str, float] = {}

        for s in sft:
            w = TIER_WEIGHTS.get(s.quality_tier, 1.0) * TYPE_WEIGHTS["sft"]
            raw[s.creator_id] = raw.get(s.creator_id, 0.0) + w

        for s in dpo:
            w = TIER_WEIGHTS.get(s.quality_tier, 1.0) * TYPE_WEIGHTS["dpo"]
            raw[s.creator_id] = raw.get(s.creator_id, 0.0) + w

        for c in pretrain:
            w = (c.token_count / 512) * TYPE_WEIGHTS["pretrain"]
            raw[c.creator_id] = raw.get(c.creator_id, 0.0) + w

        total = sum(raw.values())
        if total == 0:
            return {}

        return {cid: round(v / total, 6) for cid, v in raw.items()}

# dataset/packager.py
"""
息壤 · 数据集打包引擎

输出格式支持：
  - JSONL（每行一个 JSON，主力格式）
  - Parquet（列式存储，大数据场景）
  - HuggingFace datasets 格式（最广泛兼容）
  - ZIP 压缩包（企业交付）

打包流程：
  1. 从数据库/内存取出 approved 样本
  2. 按 dataset_type 路由到对应转换器
  3. 写出各格式文件
  4. 生成 README.md（数据集说明卡）
  5. 生成 data_card.json（机器可读元信息）
  6. ZIP 打包 → 上传/本地存储
  7. 计算创作者贡献权重
  8. 返回 DatasetPackage 对象
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataset.schema import (
    AnnotationStatus, DatasetPackage, DatasetType,
    DPOSample, PretrainChunk, QualityTier, SFTSample,
)

# 尝试导入 pandas / pyarrow（可选）
try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False
    print("ℹ️  pandas 未安装，Parquet 格式不可用")


OUTPUT_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "datasets")
os.makedirs(OUTPUT_BASE, exist_ok=True)


# ════════════════════════════════════════════════════════
# 核心打包器
# ════════════════════════════════════════════════════════

class DatasetPackager:

    def __init__(self, output_dir: str = OUTPUT_BASE):
        self.output_dir = output_dir

    # ── 主入口 ─────────────────────────────────────────

    def pack(
        self,
        name:          str,
        description:   str,
        sft_samples:   List[SFTSample] = None,
        dpo_samples:   List[DPOSample] = None,
        pretrain_chunks: List[PretrainChunk] = None,
        formats:       List[str] = None,     # ["jsonl", "parquet", "hf"]
        min_quality:   float = 5.0,
        price_cny:     float = 0.0,
        license_type:  str = "enterprise_internal",
    ) -> DatasetPackage:
        """
        打包数据集，返回 DatasetPackage 对象

        自动过滤：只包含 quality_score >= min_quality 且 status=APPROVED 的样本
        """
        if formats is None:
            formats = ["jsonl", "zip"]

        # 过滤
        sft_samples    = self._filter_sft(sft_samples or [], min_quality)
        dpo_samples    = self._filter_dpo(dpo_samples or [], min_quality)
        pretrain_chunks = self._filter_pretrain(pretrain_chunks or [], min_quality)

        total = len(sft_samples) + len(dpo_samples) + len(pretrain_chunks)
        if total == 0:
            raise ValueError("没有符合质量要求的样本，无法打包")

        # 确定主要类型
        dataset_type = self._infer_type(sft_samples, dpo_samples, pretrain_chunks)

        # 创建包目录
        pkg_id = self._gen_package_id(name)
        pkg_dir = os.path.join(self.output_dir, pkg_id)
        os.makedirs(pkg_dir, exist_ok=True)

        # 计算贡献
        contributions = self._calc_contributions(sft_samples, dpo_samples, pretrain_chunks)

        # 质量统计
        all_scores = (
            [s.quality_score for s in sft_samples] +
            [s.quality_score for s in dpo_samples] +
            [c.quality_score for c in pretrain_chunks]
        )
        avg_quality = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

        pkg = DatasetPackage(
            package_id=pkg_id,
            name=name,
            description=description,
            dataset_type=dataset_type,
            total_samples=total,
            approved_samples=total,
            avg_quality=avg_quality,
            platinum_count=sum(1 for s in sft_samples if s.quality_tier == QualityTier.PLATINUM),
            gold_count=sum(1 for s in sft_samples if s.quality_tier == QualityTier.GOLD),
            creator_contributions=contributions,
            price_cny=price_cny,
            license_type=license_type,
        )

        export_paths: Dict[str, str] = {}

        # 导出 JSONL
        if "jsonl" in formats:
            paths = self._write_jsonl(pkg_dir, sft_samples, dpo_samples, pretrain_chunks)
            export_paths.update(paths)

        # 导出 Parquet
        if "parquet" in formats and _HAS_PANDAS:
            paths = self._write_parquet(pkg_dir, sft_samples, dpo_samples, pretrain_chunks)
            export_paths.update(paths)

        # 生成 README
        readme_path = self._write_readme(pkg_dir, pkg, sft_samples, dpo_samples)
        export_paths["readme"] = readme_path

        # 生成 data_card.json
        card_path = self._write_data_card(pkg_dir, pkg)
        export_paths["data_card"] = card_path

        # ZIP 打包
        if "zip" in formats:
            zip_path = self._zip_package(pkg_dir, pkg_id)
            export_paths["zip"] = zip_path

        pkg.export_paths = export_paths
        pkg.published_at = datetime.utcnow()

        print(f"📦 数据集打包完成: {name}")
        print(f"   样本总量: {total} | 平均质量分: {avg_quality}")
        print(f"   铂金: {pkg.platinum_count} / 黄金: {pkg.gold_count}")
        print(f"   参与创作者: {len(contributions)}")
        print(f"   输出路径: {pkg_dir}")

        return pkg

    # ── JSONL 导出 ───────────────────────────────────────

    def _write_jsonl(self, pkg_dir, sft, dpo, pretrain) -> Dict[str, str]:
        paths = {}

        if sft:
            path = os.path.join(pkg_dir, "sft_data.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for s in sft:
                    f.write(json.dumps(s.to_sharegpt_dict(), ensure_ascii=False) + "\n")
            paths["sft_jsonl"] = path

            # 同时输出 alpaca 格式
            path_a = os.path.join(pkg_dir, "sft_alpaca.jsonl")
            with open(path_a, "w", encoding="utf-8") as f:
                for s in sft:
                    f.write(json.dumps(s.to_alpaca_dict(), ensure_ascii=False) + "\n")
            paths["sft_alpaca"] = path_a

        if dpo:
            path = os.path.join(pkg_dir, "dpo_data.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for s in dpo:
                    f.write(json.dumps(s.to_hf_dict(), ensure_ascii=False) + "\n")
            paths["dpo_jsonl"] = path

        if pretrain:
            path = os.path.join(pkg_dir, "pretrain_data.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for c in pretrain:
                    f.write(json.dumps(c.to_hf_dict(), ensure_ascii=False) + "\n")
            paths["pretrain_jsonl"] = path

        return paths

    # ── Parquet 导出 ─────────────────────────────────────

    def _write_parquet(self, pkg_dir, sft, dpo, pretrain) -> Dict[str, str]:
        paths = {}
        if not _HAS_PANDAS:
            return paths

        if sft:
            df = pd.DataFrame([s.to_hf_dict() for s in sft])
            path = os.path.join(pkg_dir, "sft_data.parquet")
            df.to_parquet(path, index=False)
            paths["sft_parquet"] = path

        if dpo:
            df = pd.DataFrame([s.to_hf_dict() for s in dpo])
            path = os.path.join(pkg_dir, "dpo_data.parquet")
            df.to_parquet(path, index=False)
            paths["dpo_parquet"] = path

        if pretrain:
            df = pd.DataFrame([c.to_hf_dict() for c in pretrain])
            path = os.path.join(pkg_dir, "pretrain_data.parquet")
            df.to_parquet(path, index=False)
            paths["pretrain_parquet"] = path

        return paths

    # ── README.md ────────────────────────────────────────

    def _write_readme(self, pkg_dir, pkg: DatasetPackage,
                      sft: List[SFTSample], dpo: List[DPOSample]) -> str:
        # 领域分布
        domain_counter = Counter(s.domain for s in sft if s.domain)
        domain_lines = "\n".join(
            f"- {domain}: {cnt}" for domain, cnt in domain_counter.most_common(10)
        )
        # 难度分布
        diff_counter = Counter(getattr(s, "difficulty", 3) for s in sft)

        readme = f"""# {pkg.name}

{pkg.description}

## 数据集概览

| 指标 | 数值 |
|------|------|
| 总样本数 | {pkg.total_samples:,} |
| 平均质量分 | {pkg.avg_quality} / 10 |
| 铂金样本 | {pkg.platinum_count:,} |
| 黄金样本 | {pkg.gold_count:,} |
| 数据集类型 | {pkg.dataset_type.value.upper()} |
| 主要语言 | {pkg.language} |
| 授权类型 | {pkg.license_type} |
| 版本 | {pkg.version} |
| 打包时间 | {pkg.published_at.strftime('%Y-%m-%d %H:%M UTC') if pkg.published_at else 'N/A'} |

## 领域分布

{domain_lines or "（未标注领域）"}

## 质量分级

| 等级 | 分数范围 | 含义 |
|------|---------|------|
| 铂金 | 9-10 | 顶级质量，可直接用于旗舰模型训练 |
| 黄金 | 7-8  | 高质量，适合精细调优 |
| 白银 | 5-6  | 标准质量，通用训练 |

## 文件格式

- `sft_data.jsonl`: ShareGPT / ChatML 格式，每行一条对话
- `sft_alpaca.jsonl`: Alpaca 格式（instruction / input / output）
- `dpo_data.jsonl`: DPO 偏好对（prompt / chosen / rejected）
- `pretrain_data.jsonl`: 预训练语料（text / domain）
- `data_card.json`: 机器可读元信息

## 使用示例

```python
import json

# 加载 SFT 数据
with open("sft_data.jsonl") as f:
    data = [json.loads(line) for line in f]

# 第一条样本结构
print(data[0])
# {{
#   "conversations": [
#     {{"role": "system", "content": "..."}},
#     {{"role": "user", "content": "..."}},
#     {{"role": "assistant", "content": "..."}}
#   ]
# }}
```

## 版权与授权

本数据集由息壤平台的创作者贡献内容生产，经过自动标注和人工质检。
授权类型：**{pkg.license_type}**
"""
        path = os.path.join(pkg_dir, "README.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(readme)
        return path

    # ── data_card.json ───────────────────────────────────

    def _write_data_card(self, pkg_dir: str, pkg: DatasetPackage) -> str:
        card = {
            "package_id":    pkg.package_id,
            "name":          pkg.name,
            "version":       pkg.version,
            "dataset_type":  pkg.dataset_type.value,
            "language":      pkg.language,
            "domain":        pkg.domain,
            "total_samples": pkg.total_samples,
            "avg_quality":   pkg.avg_quality,
            "platinum_count": pkg.platinum_count,
            "gold_count":    pkg.gold_count,
            "license_type":  pkg.license_type,
            "price_cny":     pkg.price_cny,
            "creator_count": len(pkg.creator_contributions),
            "created_at":    pkg.created_at.isoformat(),
            "published_at":  pkg.published_at.isoformat() if pkg.published_at else None,
        }
        path = os.path.join(pkg_dir, "data_card.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        return path

    # ── ZIP 打包 ─────────────────────────────────────────

    def _zip_package(self, pkg_dir: str, pkg_id: str) -> str:
        zip_path = os.path.join(self.output_dir, f"{pkg_id}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(pkg_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, self.output_dir)
                    zf.write(full_path, arcname)
        size_mb = os.path.getsize(zip_path) / 1024 / 1024
        print(f"  📁 ZIP: {zip_path} ({size_mb:.1f} MB)")
        return zip_path

    # ── 创作者贡献计算 ───────────────────────────────────

    @staticmethod
    def _calc_contributions(
        sft: List[SFTSample],
        dpo: List[DPOSample],
        pretrain: List[PretrainChunk],
    ) -> Dict[str, float]:
        """
        贡献权重 = 加权样本数 / 总加权数
        权重规则：SFT×1.5（标注成本高）/ DPO×2.0（偏好数据稀缺）/ Pretrain×0.5
        质量加成：铂金×2 / 黄金×1.5 / 白银×1
        """
        creator_scores: Dict[str, float] = {}

        quality_bonus = {
            QualityTier.PLATINUM: 2.0,
            QualityTier.GOLD:     1.5,
            QualityTier.SILVER:   1.0,
            QualityTier.BRONZE:   0.5,
            QualityTier.DISCARD:  0.0,
        }

        for s in sft:
            w = 1.5 * quality_bonus.get(s.quality_tier, 1.0)
            creator_scores[s.creator_id] = creator_scores.get(s.creator_id, 0) + w

        for s in dpo:
            w = 2.0  # DPO 固定高权重
            creator_scores[s.creator_id] = creator_scores.get(s.creator_id, 0) + w

        for c in pretrain:
            token_weight = min(c.token_count / 512, 3.0)  # 按长度计，最多3倍
            w = 0.5 * token_weight
            creator_scores[c.creator_id] = creator_scores.get(c.creator_id, 0) + w

        total_score = sum(creator_scores.values())
        if total_score == 0:
            return {}

        return {
            cid: round(score / total_score, 4)
            for cid, score in creator_scores.items()
        }

    # ── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _gen_package_id(name: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        slug = "".join(c if c.isalnum() else "_" for c in name)[:20]
        return f"{slug}_{ts}"

    @staticmethod
    def _filter_sft(samples, min_q):
        return [s for s in samples
                if s.status == AnnotationStatus.APPROVED and s.quality_score >= min_q]

    @staticmethod
    def _filter_dpo(samples, min_q):
        return [s for s in samples
                if s.status == AnnotationStatus.APPROVED and s.quality_score >= min_q]

    @staticmethod
    def _filter_pretrain(chunks, min_q):
        return [c for c in chunks
                if c.status == AnnotationStatus.APPROVED and c.quality_score >= min_q]

    @staticmethod
    def _infer_type(sft, dpo, pretrain) -> DatasetType:
        counts = {
            DatasetType.SFT:      len(sft),
            DatasetType.DPO:      len(dpo),
            DatasetType.PRETRAIN: len(pretrain),
        }
        return max(counts, key=counts.get)

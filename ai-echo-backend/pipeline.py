# dataset/pipeline.py
"""
息壤 · 数据集生产总调度

端到端流水线：
  CreatorMaterial[]
       ↓
  [1] 标注  (AutoAnnotator / BatchAnnotationJob)
       ↓
  [2] 质检  (QualityScorer)
       ↓
  [3] 去重  (DeduplicationPipeline)
       ↓
  [4] 打包  (DatasetPackager)
       ↓
  [5] 分润  (RevenueCalculator → CreatorLedger)
       ↓
  DatasetPackage（可交付给企业）

每个阶段均有独立进度回调，支持断点续产。
生产状态保存在 data/pipeline_state/{job_id}.json，中断后可恢复。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import sys
# ai-echo-backend/ 是包根，dataset/ 是子包
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# config.py 位于 ai-echo-backend/config.py
try:
    from config import get_settings
except ImportError:
    # 降级：若无 config.py，提供最小默认配置
    class _FallbackSettings:
        openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
        openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    _fallback = _FallbackSettings()
    def get_settings(): return _fallback

from dataset.annotator import AutoAnnotator, AnnotationMode, BatchAnnotationJob
from dataset.quality_scorer import QualityScorer
from dataset.deduplicator import DeduplicationPipeline
from dataset.packager import DatasetPackager
from dataset.schema import (
    CreatorMaterial, DatasetPackage, SFTSample,
    DPOSample, PretrainChunk,
)

_settings = get_settings()

STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "pipeline_state"
)
os.makedirs(STATE_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════
# 流水线状态
# ════════════════════════════════════════════════════════

class PipelineStage(str, Enum):
    INIT        = "init"
    ANNOTATING  = "annotating"
    SCORING     = "scoring"
    DEDUPING    = "deduplicating"
    PACKING     = "packing"
    SETTLING    = "settling"
    DONE        = "done"
    FAILED      = "failed"


@dataclass
class PipelineJob:
    job_id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    name:            str = ""
    stage:           PipelineStage = PipelineStage.INIT
    total_materials: int = 0
    annotated:       int = 0
    scored:          int = 0
    deduped:         int = 0
    packed:          int = 0
    package_id:      str = ""
    error:           str = ""
    started_at:      str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at:     str = ""

    # 各阶段用时（秒）
    timings: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id, "name": self.name, "stage": self.stage,
            "total_materials": self.total_materials, "annotated": self.annotated,
            "scored": self.scored, "deduped": self.deduped, "packed": self.packed,
            "package_id": self.package_id, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "timings": self.timings,
        }
        return d

    def progress_pct(self) -> float:
        stage_weights = {
            PipelineStage.INIT: 0, PipelineStage.ANNOTATING: 20,
            PipelineStage.SCORING: 50, PipelineStage.DEDUPING: 70,
            PipelineStage.PACKING: 85, PipelineStage.SETTLING: 95,
            PipelineStage.DONE: 100, PipelineStage.FAILED: 0,
        }
        return stage_weights.get(self.stage, 0)


# ════════════════════════════════════════════════════════
# 总调度器
# ════════════════════════════════════════════════════════

class DatasetProductionPipeline:
    """
    数据集生产总调度器

    使用方式：
      pipeline = DatasetProductionPipeline()
      package = await pipeline.run(
          materials=materials,
          name="历史文化SFT数据集_v1",
          description="...",
          annotation_mode=AnnotationMode.AUTO_REVIEW,
          target_types=["sft", "dpo"],
          min_quality=6.0,
          price_cny=9800.0,
      )
    """

    def __init__(
        self,
        annotation_mode: AnnotationMode = AnnotationMode.AUTO_REVIEW,
        annotation_concurrency: int = 5,
        scoring_concurrency: int = 8,
        minhash_threshold: float = 0.85,
        semantic_threshold: float = 0.95,
        progress_callback: Optional[Callable] = None,
    ):
        self.annotator = AutoAnnotator(mode=annotation_mode)
        self.batch_job = BatchAnnotationJob(self.annotator, concurrency=annotation_concurrency)
        self.scorer = QualityScorer()
        self.dedup = DeduplicationPipeline(minhash_threshold, semantic_threshold)
        self.packager = DatasetPackager()
        self.progress_callback = progress_callback or self._default_progress

        self._active_jobs: Dict[str, PipelineJob] = {}

    # ── 主入口 ─────────────────────────────────────────

    async def run(
        self,
        materials:        List[CreatorMaterial],
        name:             str,
        description:      str = "",
        target_types:     List[str] = None,
        min_quality:      float = 5.0,
        price_cny:        float = 0.0,
        license_type:     str = "enterprise_internal",
        formats:          List[str] = None,
        do_revenue:       bool = True,
        sale_amount:      float = None,
    ) -> DatasetPackage:
        """
        端到端生产流水线

        Returns: DatasetPackage（含打包路径和创作者贡献信息）
        """
        job = PipelineJob(name=name, total_materials=len(materials))
        self._active_jobs[job.job_id] = job
        self._save_state(job)

        print(f"\n{'='*60}")
        print(f"🏭 数据集生产任务启动")
        print(f"   任务名: {name}")
        print(f"   素材数: {len(materials)}")
        print(f"   目标类型: {target_types or ['sft','dpo','pretrain']}")
        print(f"   最低质量: {min_quality}")
        print(f"{'='*60}\n")

        try:
            # ─── Stage 1: 标注 ──────────────────────────
            job.stage = PipelineStage.ANNOTATING
            self._save_state(job)
            t0 = time.time()

            async def _ann_progress(done, total):
                job.annotated = done
                self._save_state(job)
                await self.progress_callback(job, f"标注进度 {done}/{total}")

            ann_results = await self.batch_job.run(
                materials, target_types, _ann_progress
            )
            job.timings["annotation"] = round(time.time() - t0, 1)

            sft_samples: List[SFTSample] = ann_results["sft_samples"]
            dpo_samples: List[DPOSample] = ann_results["dpo_samples"]
            pretrain_chunks: List[PretrainChunk] = ann_results["pretrain_chunks"]

            print(f"\n[Stage 1 完成] 标注耗时 {job.timings['annotation']}s")
            print(f"  SFT: {len(sft_samples)} | DPO: {len(dpo_samples)} | "
                  f"Pretrain: {len(pretrain_chunks)} chunks")

            # ─── Stage 2: 质检 ──────────────────────────
            job.stage = PipelineStage.SCORING
            self._save_state(job)
            t0 = time.time()

            sft_reports = await self.scorer.batch_score_sft(
                sft_samples, concurrency=8
            )
            dpo_reports = await self.scorer.batch_score_dpo(
                dpo_samples, concurrency=8
            )
            pretrain_reports = await self.scorer.batch_score_pretrain(
                pretrain_chunks, concurrency=10
            )
            job.timings["scoring"] = round(time.time() - t0, 1)

            sft_summary = self.scorer.summarize([r for r in sft_reports if r])
            dpo_passed = sum(1 for r in dpo_reports if r and r.passed)
            print(f"\n[Stage 2 完成] 质检耗时 {job.timings['scoring']}s")
            print(f"  SFT 通过率: {sft_summary.get('pass_rate', 0):.1%} | "
                  f"平均分: {sft_summary.get('avg_score', 0)}")
            print(f"  DPO 通过: {dpo_passed}/{len(dpo_samples)}")

            # ── 质检完成后立即持久化到 SQLite ──────────────
            import store.db as _db
            if sft_samples:
                await _db.bulk_insert_sft(sft_samples)
                print(f"  ✅ SFT {len(sft_samples)} 条已写入 SQLite")
            if dpo_samples:
                await _db.bulk_insert_dpo(dpo_samples)
                print(f"  ✅ DPO {len(dpo_samples)} 条已写入 SQLite")
            if pretrain_chunks:
                await _db.bulk_insert_pretrain(pretrain_chunks)
                print(f"  ✅ Pretrain {len(pretrain_chunks)} chunks 已写入 SQLite")

            job.scored = len(sft_samples) + len(dpo_samples) + len(pretrain_chunks)
            self._save_state(job)

            # ─── Stage 3: 去重 ──────────────────────────
            job.stage = PipelineStage.DEDUPING
            self._save_state(job)
            t0 = time.time()

            # SFT 三级去重
            approved_sft = [s for s in sft_samples if s.quality_score >= min_quality]
            dedup_result = await self.dedup.run(approved_sft)
            deduped_sft: List[SFTSample] = dedup_result["samples"]

            # DPO 精确哈希去重（内容短，不做向量层）
            approved_dpo = [s for s in dpo_samples if s.quality_score >= min_quality]
            seen_dpo: set = set()
            deduped_dpo: List[DPOSample] = []
            for s in approved_dpo:
                key = hash(s.prompt + s.chosen)
                if key not in seen_dpo:
                    seen_dpo.add(key)
                    deduped_dpo.append(s)

            # Pretrain 哈希+MinHash 去重
            chunk_result = self.dedup.run_chunks(pretrain_chunks)
            deduped_chunks: List[PretrainChunk] = chunk_result["chunks"]

            job.timings["dedup"] = round(time.time() - t0, 1)
            job.deduped = len(deduped_sft) + len(deduped_dpo) + len(deduped_chunks)
            self._save_state(job)

            print(f"\n[Stage 3 完成] 去重耗时 {job.timings['dedup']}s")
            print(f"  SFT: {len(approved_sft)} → {len(deduped_sft)} | "
                  f"DPO: {len(approved_dpo)} → {len(deduped_dpo)} | "
                  f"Pretrain: {len(pretrain_chunks)} → {len(deduped_chunks)}")

            # ─── Stage 4: 打包 ──────────────────────────
            job.stage = PipelineStage.PACKING
            self._save_state(job)
            t0 = time.time()

            package = self.packager.pack(
                name=name,
                description=description or f"由息壤平台自动生产的{name}",
                sft_samples=deduped_sft,
                dpo_samples=deduped_dpo,
                pretrain_chunks=deduped_chunks,
                formats=formats or ["jsonl", "zip"],
                min_quality=min_quality,
                price_cny=price_cny,
                license_type=license_type,
            )
            job.timings["packing"] = round(time.time() - t0, 1)
            job.packed = package.total_samples
            job.package_id = package.package_id
            self._save_state(job)

            # ─── Stage 5: 分润 ──────────────────────────
            if do_revenue and package.creator_contributions:
                job.stage = PipelineStage.SETTLING
                self._save_state(job)

                from creator.revenue_calculator import RevenueCalculator
                import store.db as _db
                actual_sale = sale_amount or price_cny
                if actual_sale > 0:
                    records = RevenueCalculator().calculate(package, actual_sale)
                    await _db.insert_revenue_records(records)
                    print(f"\n[Stage 5 完成] 分润计算完成，"
                          f"共 {len(records)} 位创作者入账")

            # ─── Done ───────────────────────────────────
            job.stage = PipelineStage.DONE
            job.finished_at = datetime.utcnow().isoformat()
            self._save_state(job)

            total_time = sum(job.timings.values())
            print(f"\n{'='*60}")
            print(f"✅ 生产完成！总耗时 {total_time:.1f}s")
            print(f"   数据集ID: {package.package_id}")
            print(f"   最终样本: {package.total_samples}")
            print(f"   平均质量: {package.avg_quality}/10")
            print(f"   ZIP路径: {package.export_paths.get('zip', 'N/A')}")
            print(f"{'='*60}\n")

            return package

        except Exception as e:
            job.stage = PipelineStage.FAILED
            job.error = str(e)
            job.finished_at = datetime.utcnow().isoformat()
            self._save_state(job)
            print(f"\n❌ 生产任务失败: {e}")
            raise

    # ── 状态查询 ────────────────────────────────────────

    def get_job_status(self, job_id: str) -> Optional[dict]:
        job = self._active_jobs.get(job_id)
        if job:
            return {**job.to_dict(), "progress_pct": job.progress_pct()}
        # 尝试从磁盘恢复
        path = os.path.join(STATE_DIR, f"{job_id}.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def list_jobs(self) -> List[dict]:
        jobs = []
        for fname in sorted(os.listdir(STATE_DIR), reverse=True)[:50]:
            if fname.endswith(".json"):
                path = os.path.join(STATE_DIR, fname)
                try:
                    with open(path) as f:
                        jobs.append(json.load(f))
                except Exception:
                    pass
        return jobs

    # ── 工具 ────────────────────────────────────────────

    def _save_state(self, job: PipelineJob):
        path = os.path.join(STATE_DIR, f"{job.job_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({**job.to_dict(), "progress_pct": job.progress_pct()},
                           f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  状态保存失败: {e}")

    @staticmethod
    async def _default_progress(job: PipelineJob, msg: str):
        print(f"  [{job.stage}] {msg}")


# ════════════════════════════════════════════════════════
# 全局单例（供 API 层调用）
# ════════════════════════════════════════════════════════

_pipeline = DatasetProductionPipeline()

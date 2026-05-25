# dataset/pipeline.py  v2
"""
知数知圈 · 数据集生产总调度 v2

升级日志 v2:
  [新增] 每个阶段接入 PipelineMonitor 埋点（start_stage / end_stage）
         → /api/platform/monitor 可实时看到各阶段耗时、质量、通过率
         → 超时/质量崩溃/失败率自动触发告警
  [新增] DPO 去重升级为语义去重（dedup.run_dpo）
  [修复] 分润记录写入账本 + 持久化（v1 只计算不入账）
  [保留] 所有公开接口（run / get_job_status / list_jobs）签名不变
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
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

try:
    from config import get_settings
except ImportError:
    class _FallbackSettings:
        openai_api_key  = os.environ.get("OPENAI_API_KEY", "")
        openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        openai_model    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        creator_ledger_path = ""
    def get_settings(): return _FallbackSettings()

from dataset.annotator     import AnnotationMode, BatchAnnotationJob, MultiModelAnnotator
from dataset.quality_scorer import QualityScorer
from dataset.deduplicator  import DeduplicationPipeline
from dataset.packager      import DatasetPackager
from dataset.pipeline_monitor import PipelineMonitor
from dataset.schema import (
    CreatorMaterial, DatasetPackage, SFTSample, DPOSample, PretrainChunk,
)

_settings = get_settings()
_monitor  = PipelineMonitor.instance()

STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "pipeline_state"
)
os.makedirs(STATE_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════════════════
# 流水线状态
# ════════════════════════════════════════════════════════════════════

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
    timings:         Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "name": self.name, "stage": self.stage,
            "total_materials": self.total_materials, "annotated": self.annotated,
            "scored": self.scored, "deduped": self.deduped, "packed": self.packed,
            "package_id": self.package_id, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "timings": self.timings,
        }

    def progress_pct(self) -> float:
        weights = {
            PipelineStage.INIT: 0,       PipelineStage.ANNOTATING: 20,
            PipelineStage.SCORING: 50,   PipelineStage.DEDUPING: 70,
            PipelineStage.PACKING: 85,   PipelineStage.SETTLING: 95,
            PipelineStage.DONE: 100,     PipelineStage.FAILED: 0,
        }
        return weights.get(self.stage, 0)


# ════════════════════════════════════════════════════════════════════
# 总调度器
# ════════════════════════════════════════════════════════════════════

class DatasetProductionPipeline:
    """
    数据集生产总调度器 v2
    公开接口与 v1 完全兼容。
    """

    def __init__(
        self,
        annotation_mode:        AnnotationMode = AnnotationMode.AUTO_REVIEW,
        annotation_concurrency: int   = 5,
        scoring_concurrency:    int   = 8,
        minhash_threshold:      float = 0.85,
        semantic_threshold:     float = 0.92,
        progress_callback:      Optional[Callable] = None,
    ):
        # v2: 默认使用多模型投票标注器
        annotator = MultiModelAnnotator(mode=annotation_mode)
        self.batch_job  = BatchAnnotationJob(annotator, concurrency=annotation_concurrency)
        self.scorer     = QualityScorer()
        self.dedup      = DeduplicationPipeline(minhash_threshold, semantic_threshold)
        self.packager   = DatasetPackager()
        self.progress_callback = progress_callback or self._default_progress
        self._active_jobs: Dict[str, PipelineJob] = {}

    # ── 主入口 ─────────────────────────────────────────────────────

    async def run(
        self,
        materials:    List[CreatorMaterial],
        name:         str,
        description:  str  = "",
        target_types: List[str] = None,
        min_quality:  float = 5.0,
        price_cny:    float = 0.0,
        license_type: str  = "enterprise_internal",
        formats:      List[str] = None,
        do_revenue:   bool = True,
        sale_amount:  float = None,
    ) -> DatasetPackage:

        job = PipelineJob(name=name, total_materials=len(materials))
        self._active_jobs[job.job_id] = job
        self._save_state(job)
        jid = job.job_id

        print(f"\n{'='*60}")
        print(f"🏭 数据集生产任务启动: {name}")
        print(f"   素材数: {len(materials)}  目标类型: {target_types or ['sft','dpo','pretrain']}")
        print(f"{'='*60}\n")

        try:
            # ══ Stage 1: 标注 ═══════════════════════════════════════
            job.stage = PipelineStage.ANNOTATING
            self._save_state(job)

            mon_ctx = _monitor.start_stage(jid, "annotating", input_count=len(materials))

            async def _ann_progress(done, total):
                job.annotated = done
                self._save_state(job)
                await self.progress_callback(job, f"标注 {done}/{total}")

            t0 = time.time()
            ann_results = await self.batch_job.run(materials, target_types, _ann_progress)
            elapsed = time.time() - t0
            job.timings["annotation"] = round(elapsed, 1)

            sft_raw:     List[SFTSample]     = ann_results["sft_samples"]
            dpo_raw:     List[DPOSample]     = ann_results["dpo_samples"]
            pretrain_raw: List[PretrainChunk] = ann_results["pretrain_chunks"]
            total_out = len(sft_raw) + len(dpo_raw) + len(pretrain_raw)

            _monitor.end_stage(mon_ctx, output_count=total_out,
                               failed_count=len(materials) - min(len(materials), total_out))
            print(f"[Stage 1] 标注完成 {elapsed:.1f}s | SFT={len(sft_raw)} DPO={len(dpo_raw)} PT={len(pretrain_raw)}")

            # ══ Stage 2: 质检 ═══════════════════════════════════════
            job.stage = PipelineStage.SCORING
            self._save_state(job)

            mon_ctx = _monitor.start_stage(jid, "scoring",
                                           input_count=len(sft_raw) + len(dpo_raw))
            t0 = time.time()

            sft_reports     = await self.scorer.batch_score_sft(sft_raw, concurrency=8)
            dpo_reports     = await self.scorer.batch_score_dpo(dpo_raw, concurrency=8)
            pretrain_reports = await self.scorer.batch_score_pretrain(pretrain_raw, concurrency=10)

            elapsed = time.time() - t0
            job.timings["scoring"] = round(elapsed, 1)

            sft_summary  = self.scorer.summarize([r for r in sft_reports if r])
            dpo_passed   = sum(1 for r in dpo_reports if r and r.passed)
            pt_passed    = sum(1 for r in pretrain_reports if r and r.passed)
            total_passed = (sft_summary.get("passed", 0) + dpo_passed + pt_passed)
            total_scored = len(sft_reports) + len(dpo_reports) + len(pretrain_reports)
            avg_q        = sft_summary.get("avg_score", 0.0)
            pass_rate    = total_passed / max(total_scored, 1)

            _monitor.end_stage(mon_ctx,
                               output_count=total_passed,
                               failed_count=total_scored - total_passed,
                               avg_score=avg_q,
                               pass_rate=pass_rate)

            job.scored = total_scored
            self._save_state(job)
            print(f"[Stage 2] 质检完成 {elapsed:.1f}s | 均质={avg_q:.2f} 通过率={pass_rate:.1%}")

            # ══ Stage 3: 去重 ═══════════════════════════════════════
            job.stage = PipelineStage.DEDUPING
            self._save_state(job)

            approved_sft = [s for s, r in zip(sft_raw, sft_reports)
                            if r and r.passed and s.quality_score >= min_quality]
            approved_dpo = [s for s, r in zip(dpo_raw, dpo_reports)
                            if r and r.passed and s.quality_score >= min_quality]
            approved_pt  = [c for c, r in zip(pretrain_raw, pretrain_reports)
                            if r and r.passed]

            mon_ctx = _monitor.start_stage(jid, "deduplicating",
                                           input_count=len(approved_sft) + len(approved_dpo))
            t0 = time.time()

            # SFT 三级去重（含语义层）
            sft_dedup_result  = await self.dedup.run(approved_sft, job_id=jid)
            deduped_sft:  List[SFTSample]     = sft_dedup_result["samples"]

            # DPO 语义去重（v2 新增，v1 只做精确哈希）
            dpo_dedup_result  = await self.dedup.run_dpo(approved_dpo, job_id=jid)
            deduped_dpo:  List[DPOSample]     = dpo_dedup_result["samples"]

            # Pretrain 哈希 + MinHash
            pt_dedup_result   = self.dedup.run_chunks(approved_pt, job_id=jid)
            deduped_pt:   List[PretrainChunk] = pt_dedup_result["chunks"]

            elapsed = time.time() - t0
            job.timings["dedup"] = round(elapsed, 1)
            job.deduped = len(deduped_sft) + len(deduped_dpo) + len(deduped_pt)

            dedup_removed = (
                sft_dedup_result["removed"] +
                dpo_dedup_result["removed"] +
                pt_dedup_result["removed"]
            )
            _monitor.end_stage(mon_ctx,
                               output_count=job.deduped,
                               failed_count=dedup_removed)
            self._save_state(job)

            print(f"[Stage 3] 去重完成 {elapsed:.1f}s | "
                  f"SFT {len(approved_sft)}→{len(deduped_sft)} "
                  f"DPO {len(approved_dpo)}→{len(deduped_dpo)} "
                  f"PT {len(approved_pt)}→{len(deduped_pt)}")

            # ══ Stage 4: 打包 ═══════════════════════════════════════
            job.stage = PipelineStage.PACKING
            self._save_state(job)

            mon_ctx = _monitor.start_stage(jid, "packing",
                                           input_count=job.deduped)
            t0 = time.time()

            package = self.packager.pack(
                name=name,
                description=description or f"知数知圈自动生产: {name}",
                sft_samples=deduped_sft,
                dpo_samples=deduped_dpo,
                pretrain_chunks=deduped_pt,
                formats=formats or ["jsonl", "zip"],
                min_quality=min_quality,
                price_cny=price_cny,
                license_type=license_type,
            )
            elapsed = time.time() - t0
            job.timings["packing"] = round(elapsed, 1)
            job.packed     = package.total_samples
            job.package_id = package.package_id

            _monitor.end_stage(mon_ctx, output_count=package.total_samples,
                               avg_score=package.avg_quality)
            self._save_state(job)
            print(f"[Stage 4] 打包完成 {elapsed:.1f}s | 样本={package.total_samples} 均质={package.avg_quality}")

            # ══ Stage 5: 分润 ═══════════════════════════════════════
            if do_revenue and package.creator_contributions:
                job.stage = PipelineStage.SETTLING
                self._save_state(job)

                mon_ctx = _monitor.start_stage(jid, "settling",
                                               input_count=len(package.creator_contributions))
                t0 = time.time()
                try:
                    from creator.revenue_calculator import RevenueCalculator, CreatorLedger
                    actual_sale = sale_amount or price_cny
                    if actual_sale > 0:
                        ledger_path = getattr(_settings, "creator_ledger_path", "")
                        ledger   = CreatorLedger(ledger_path or None)
                        records  = RevenueCalculator().calculate(package, actual_sale)
                        ledger.add_records(records)   # v2: 真正写入 SQLite
                        print(f"[Stage 5] 分润入账: {len(records)} 位创作者")
                        _monitor.end_stage(mon_ctx, output_count=len(records))
                    else:
                        _monitor.end_stage(mon_ctx, output_count=0)
                except Exception as e:
                    _monitor.end_stage(mon_ctx, error_msg=str(e))
                    _monitor.fire_alert("WARNING", jid, "settling",
                                        f"分润计算失败（不影响打包结果）: {e}")
                    print(f"⚠️  分润计算失败（不影响打包）: {e}")

            # ══ Done ════════════════════════════════════════════════
            job.stage       = PipelineStage.DONE
            job.finished_at = datetime.utcnow().isoformat()
            self._save_state(job)

            total_time = sum(job.timings.values())
            print(f"\n{'='*60}")
            print(f"✅ 生产完成  总耗时 {total_time:.1f}s")
            print(f"   ID={package.package_id}  样本={package.total_samples}  均质={package.avg_quality}")
            print(f"{'='*60}\n")
            return package

        except Exception as e:
            job.stage       = PipelineStage.FAILED
            job.error       = str(e)
            job.finished_at = datetime.utcnow().isoformat()
            self._save_state(job)
            _monitor.fire_alert("ERROR", jid, str(job.stage), f"流水线异常: {e}")
            print(f"\n❌ 生产任务失败: {e}")
            raise

    # ── 状态查询 ───────────────────────────────────────────────────

    def get_job_status(self, job_id: str) -> Optional[dict]:
        job = self._active_jobs.get(job_id)
        if job:
            return {**job.to_dict(), "progress_pct": job.progress_pct()}
        path = os.path.join(STATE_DIR, f"{job_id}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def list_jobs(self) -> List[dict]:
        jobs = []
        for fname in sorted(os.listdir(STATE_DIR), reverse=True)[:50]:
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(STATE_DIR, fname)) as f:
                        jobs.append(json.load(f))
                except Exception:
                    pass
        return jobs

    # ── 工具 ───────────────────────────────────────────────────────

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


# ── 全局单例 ───────────────────────────────────────────────────────
_pipeline = DatasetProductionPipeline()

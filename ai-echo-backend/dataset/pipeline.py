# dataset/pipeline.py  v3
"""
知数知圈 · 数据集生产总调度 v3

v3 升级（对应质量审计 P0/P1/P2）：
  [P0] 接入 PipelineMonitor 监控埋点（阶段耗时、质量分布、告警）
  [P0] asyncio.Lock 并发写保护（防止多 job 并发写入 SQLite 冲突）
  [P1] 质检完成后写入 store/db.py SQLite（替代内存 dict，重启不丢）
  [P1] 分润记录也写入 SQLite（账本迁移）
  [P2] 打包阶段新增 Parquet + HuggingFace DataCard 输出
  [P2] 打包完成后持久化包元数据到 dataset_packages 表

端到端流水线：
  CreatorMaterial[]
       ↓
  [1] 标注  (AutoAnnotator / BatchAnnotationJob)         ← monitor.start_stage
       ↓
  [2] 质检  (QualityScorer)                              ← monitor.end_stage
       ↓
  [2b] SQLite 批量写入                                   ← store/db
       ↓
  [3] 去重  (DeduplicationPipeline)                      ← monitor
       ↓
  [4] 打包  (DatasetPackager + ParquetExporter)          ← monitor
       ↓
  [4b] 包元数据持久化                                    ← store/db
       ↓
  [5] 分润  (RevenueCalculator → SQLite ledger)          ← store/db
       ↓
  DatasetPackage（可交付给企业，含 JSONL + Parquet）
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
        openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
        openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    _fallback = _FallbackSettings()
    def get_settings(): return _fallback

from dataset.annotator import AutoAnnotator, AnnotationMode, BatchAnnotationJob
from dataset.quality_scorer import QualityScorer
from dataset.deduplicator import DeduplicationPipeline
from dataset.packager import DatasetPackager, ParquetExporter
from dataset.pipeline_monitor import PipelineMonitor
from dataset.schema import (
    CreatorMaterial, DatasetPackage, SFTSample,
    DPOSample, PretrainChunk,
)

_settings = get_settings()

STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "pipeline_state"
)
os.makedirs(STATE_DIR, exist_ok=True)

# ── P0: 全局并发写锁（防止多 job 同时写 SQLite 造成冲突）───────
_db_write_lock = asyncio.Lock()


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
    timings: Dict[str, float] = field(default_factory=dict)

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
        stage_weights = {
            PipelineStage.INIT: 0, PipelineStage.ANNOTATING: 20,
            PipelineStage.SCORING: 50, PipelineStage.DEDUPING: 70,
            PipelineStage.PACKING: 85, PipelineStage.SETTLING: 95,
            PipelineStage.DONE: 100, PipelineStage.FAILED: 0,
        }
        return stage_weights.get(self.stage, 0)


# ════════════════════════════════════════════════════════════════════
# 总调度器
# ════════════════════════════════════════════════════════════════════

class DatasetProductionPipeline:
    """
    数据集生产总调度器 v3

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
        export_parquet: bool = True,         # P2: 是否输出 Parquet
    ):
        self.annotator = AutoAnnotator(mode=annotation_mode)
        self.batch_job = BatchAnnotationJob(self.annotator, concurrency=annotation_concurrency)
        self.scorer = QualityScorer()
        self.dedup = DeduplicationPipeline(minhash_threshold, semantic_threshold)
        self.packager = DatasetPackager()
        self.progress_callback = progress_callback or self._default_progress
        self.export_parquet = export_parquet

        self._monitor = PipelineMonitor.instance()   # P0: 监控单例
        self._active_jobs: Dict[str, PipelineJob] = {}

    # ── 主入口 ─────────────────────────────────────────────────────

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
        """端到端生产流水线（v3：接入监控 + SQLite + Parquet）"""
        job = PipelineJob(name=name, total_materials=len(materials))
        self._active_jobs[job.job_id] = job
        self._save_state(job)

        print(f"\n{'='*60}")
        print(f"🏭 数据集生产任务启动 v3")
        print(f"   任务名: {name}")
        print(f"   素材数: {len(materials)}")
        print(f"   目标类型: {target_types or ['sft','dpo','pretrain']}")
        print(f"   最低质量: {min_quality}")
        print(f"{'='*60}\n")

        try:
            # ─── Stage 0: 安全预过滤 ────────────────────────────
            # 素材进流水线前做内容安全检查，拒绝违规内容（不计入监控阶段）
            try:
                from dataset.content_safety import batch_check as _safety_batch
                _safety_items = [{"content": m.content, "content_type": m.content_type}
                                 for m in materials]
                _safety_results = await _safety_batch(_safety_items, concurrency=10)
                _safe_materials = []
                _blocked = 0
                for mat, sr in zip(materials, _safety_results):
                    if sr.passed:
                        _safe_materials.append(mat)
                    else:
                        _blocked += 1
                        print(f"  🚫 素材 {mat.material_id[:8]}… 被安全过滤: {sr.reason}")
                materials = _safe_materials
                if _blocked:
                    print(f"  [安全预过滤] 拦截 {_blocked} 条，剩余 {len(materials)} 条进入流水线")
                if not materials:
                    raise ValueError("所有素材均未通过安全审核，任务终止")
            except ImportError:
                pass  # content_safety 不可用时放行，不阻断流水线

            # ─── Stage 1: 标注 ──────────────────────────────────
            job.stage = PipelineStage.ANNOTATING
            self._save_state(job)

            # P0: 监控埋点 - 阶段开始
            mon_ctx = self._monitor.start_stage(
                job.job_id, "annotating", input_count=len(materials)
            )
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

            ann_total = len(sft_samples) + len(dpo_samples) + len(pretrain_chunks)
            # P0: 监控埋点 - 阶段结束
            self._monitor.end_stage(
                mon_ctx,
                output_count=ann_total,
                avg_score=0.0,  # 标注阶段还没质量分
            )

            print(f"\n[Stage 1 完成] 标注耗时 {job.timings['annotation']}s")
            print(f"  SFT: {len(sft_samples)} | DPO: {len(dpo_samples)} | "
                  f"Pretrain: {len(pretrain_chunks)} chunks")

            # ─── Stage 2: 质检 ──────────────────────────────────
            job.stage = PipelineStage.SCORING
            self._save_state(job)

            mon_ctx = self._monitor.start_stage(
                job.job_id, "scoring", input_count=ann_total
            )
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
            avg_q = sft_summary.get("avg_score", 0.0)
            pass_rate = sft_summary.get("pass_rate", 0.0)

            self._monitor.end_stage(
                mon_ctx,
                output_count=ann_total,
                avg_score=avg_q,
                pass_rate=pass_rate,
            )

            print(f"\n[Stage 2 完成] 质检耗时 {job.timings['scoring']}s")
            print(f"  SFT 通过率: {pass_rate:.1%} | 平均分: {avg_q}")
            print(f"  DPO 通过: {dpo_passed}/{len(dpo_samples)}")

            # ── P0+P1: 质检完成后用并发锁写入 SQLite ─────────────
            async with _db_write_lock:
                import store.db as _db
                if sft_samples:
                    n = await _db.bulk_insert_sft(sft_samples)
                    print(f"  ✅ SFT {n} 条已写入 SQLite")
                if dpo_samples:
                    n = await _db.bulk_insert_dpo(dpo_samples)
                    print(f"  ✅ DPO {n} 条已写入 SQLite")
                if pretrain_chunks:
                    n = await _db.bulk_insert_pretrain(pretrain_chunks)
                    print(f"  ✅ Pretrain {n} chunks 已写入 SQLite")

            job.scored = ann_total
            self._save_state(job)

            # ─── Stage 3: 去重 ──────────────────────────────────
            job.stage = PipelineStage.DEDUPING
            self._save_state(job)

            approved_sft = [s for s in sft_samples if s.quality_score >= min_quality]
            mon_ctx = self._monitor.start_stage(
                job.job_id, "deduplicating", input_count=len(approved_sft)
            )
            t0 = time.time()

            dedup_result = await self.dedup.run(approved_sft)
            deduped_sft: List[SFTSample] = dedup_result["samples"]

            approved_dpo = [s for s in dpo_samples if s.quality_score >= min_quality]
            seen_dpo: set = set()
            deduped_dpo: List[DPOSample] = []
            for s in approved_dpo:
                key = hash(s.prompt + s.chosen)
                if key not in seen_dpo:
                    seen_dpo.add(key)
                    deduped_dpo.append(s)

            chunk_result = self.dedup.run_chunks(pretrain_chunks)
            deduped_chunks: List[PretrainChunk] = chunk_result["chunks"]

            job.timings["dedup"] = round(time.time() - t0, 1)
            job.deduped = len(deduped_sft) + len(deduped_dpo) + len(deduped_chunks)

            self._monitor.end_stage(
                mon_ctx,
                input_count=len(approved_sft),
                output_count=len(deduped_sft),
                avg_score=avg_q,
            )
            self._save_state(job)

            print(f"\n[Stage 3 完成] 去重耗时 {job.timings['dedup']}s")
            print(f"  SFT: {len(approved_sft)} → {len(deduped_sft)} | "
                  f"DPO: {len(approved_dpo)} → {len(deduped_dpo)} | "
                  f"Pretrain: {len(pretrain_chunks)} → {len(deduped_chunks)}")

            # ─── Stage 4: 打包 ──────────────────────────────────
            job.stage = PipelineStage.PACKING
            self._save_state(job)

            pack_total = len(deduped_sft) + len(deduped_dpo) + len(deduped_chunks)
            mon_ctx = self._monitor.start_stage(
                job.job_id, "packing", input_count=pack_total
            )
            t0 = time.time()

            # 基础格式（JSONL + ZIP）
            pkg_formats = formats or ["jsonl", "zip"]

            package = self.packager.pack(
                name=name,
                description=description or f"由知数知圈平台自动生产的{name}",
                sft_samples=deduped_sft,
                dpo_samples=deduped_dpo,
                pretrain_chunks=deduped_chunks,
                formats=pkg_formats,
                min_quality=min_quality,
                price_cny=price_cny,
                license_type=license_type,
            )

            # P2: Parquet + HuggingFace DataCard
            if self.export_parquet:
                pkg_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    "data", "datasets", package.package_id
                )
                try:
                    updated_paths = ParquetExporter.export(
                        pkg_dir=pkg_dir,
                        name=name,
                        description=description,
                        sft_samples=deduped_sft,
                        dpo_samples=deduped_dpo,
                        pretrain_chunks=deduped_chunks,
                        avg_quality=package.avg_quality,
                        license_type=license_type,
                        export_paths=dict(package.export_paths),
                    )
                    package.export_paths = updated_paths
                    print(f"  📊 Parquet + DataCard 已生成")
                except Exception as e:
                    print(f"  ⚠️  Parquet 生成失败（不阻断流水线）: {e}")

            job.timings["packing"] = round(time.time() - t0, 1)
            job.packed = package.total_samples
            job.package_id = package.package_id

            self._monitor.end_stage(
                mon_ctx,
                output_count=package.total_samples,
                avg_score=package.avg_quality,
            )

            # P1: 包元数据持久化
            async with _db_write_lock:
                import store.db as _db
                await _db.save_package(package)
                print(f"  ✅ 包元数据已写入 SQLite (id={package.package_id[:8]}…)")

            # P2: 版本快照（versioning v2 — SQLite）
            try:
                from dataset.versioning import version_manager as _vm
                _vm.snapshot_from_package(package, changelog=f"pipeline job {job.job_id[:8]}")
            except Exception as _ve:
                print(f"  ⚠️  版本快照失败（不阻断流水线）: {_ve}")

            self._save_state(job)

            # ─── Stage 5: 分润 ──────────────────────────────────
            if do_revenue and package.creator_contributions:
                job.stage = PipelineStage.SETTLING
                self._save_state(job)

                from creator.revenue_calculator import RevenueCalculator, CreatorLedger
                actual_sale = sale_amount or price_cny
                if actual_sale > 0:
                    records = RevenueCalculator().calculate(package, actual_sale)
                    # 统一写入 CreatorLedger（内部已用 SQLite + threading.Lock）
                    # 不再通过 store.db 双写，消除两套账本冲突
                    async with _db_write_lock:
                        ledger = CreatorLedger()
                        ledger.add_records(records)
                    print(f"\n[Stage 5 完成] 分润计算完成，共 {len(records)} 位创作者入账")

            # ─── Done ──────────────────────────────────────────
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
            parquet_keys = [k for k in package.export_paths if 'parquet' in k]
            if parquet_keys:
                print(f"   Parquet: {', '.join(parquet_keys)}")
            print(f"{'='*60}\n")

            return package

        except Exception as e:
            job.stage = PipelineStage.FAILED
            job.error = str(e)
            job.finished_at = datetime.utcnow().isoformat()
            self._save_state(job)
            # P0: 监控告警
            self._monitor.fire_alert(
                severity="CRITICAL",
                job_id=job.job_id,
                stage=job.stage,
                message=f"任务 {job.name} 失败: {e}",
            )
            print(f"\n❌ 生产任务失败: {e}")
            raise

    # ── 状态查询 ──────────────────────────────────────────────────

    def get_job_status(self, job_id: str) -> Optional[dict]:
        job = self._active_jobs.get(job_id)
        if job:
            return {**job.to_dict(), "progress_pct": job.progress_pct()}
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

    # ── 工具 ──────────────────────────────────────────────────────

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


# ════════════════════════════════════════════════════════════════════
# 全局单例（供 API 层调用）
# ════════════════════════════════════════════════════════════════════

_pipeline = DatasetProductionPipeline()

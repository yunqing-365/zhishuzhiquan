# dataset_api.py  v2 — SQLite 持久化 + SSE 进度流 + 人工复核 + DPO 质检
"""
息壤 · 数据集生产系统 API  v2

变更：
  - 所有存储改用 SQLite (store/db.py)，重启不丢数据
  - POST /produce 返回真实 job_id，SSE /job/{id}/stream 实时推进度
  - 新增 /review/* 端点完成人工复核闭环
  - DPO 样本现在会经过 quality_scorer.score_dpo()

路由前缀：
  /api/dataset/*     生产流程
  /api/review/*      人工复核
  /api/creator/*     创作者收益
  /api/platform/*    管理后台
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ── 数据库 ──────────────────────────────────────────────
import store.db as db

# ── 生产流水线 ──────────────────────────────────────────
from dataset.annotator   import AnnotationMode
from dataset.pipeline    import DatasetProductionPipeline, PipelineStage
from dataset.schema      import CreatorMaterial
from dataset.human_review import review_queue, review_operator, review_stats

# ── 分润 ────────────────────────────────────────────────
from creator.revenue_calculator import (
    RevenueCalculator, _ledger, _analyzer,
)
from dataset.schema import DatasetPackage

dataset_router  = APIRouter(prefix="/api/dataset",  tags=["Dataset Production"])
review_router   = APIRouter(prefix="/api/review",   tags=["Human Review"])
creator_router  = APIRouter(prefix="/api/creator",  tags=["Creator Revenue"])
platform_router = APIRouter(prefix="/api/platform", tags=["Platform Admin"])

# 活跃 pipeline 实例（key=job_id）
_active_pipelines: dict[str, DatasetProductionPipeline] = {}

# SSE 进度频道：job_id → asyncio.Queue（每条消息 JSON str）
_sse_queues: dict[str, asyncio.Queue] = {}


# ════════════════════════════════════════════════════════
# Pydantic 模型
# ════════════════════════════════════════════════════════

class IngestRequest(BaseModel):
    creator_id:    str
    raw_content:   str
    material_type: str  = "text"
    source_path:   str  = ""
    domain:        str  = ""
    metadata:      dict = {}


class ProduceRequest(BaseModel):
    name:             str
    description:      str  = ""
    material_ids:     List[str]
    target_types:     List[str] = ["sft", "dpo", "pretrain"]
    annotation_mode:  str  = "auto_review"
    use_rag:          bool = True          # 是否用 RAG 增强标注
    rag_era:          str  = "song"        # 知识库 era 名称
    min_quality:      float = Field(5.0, ge=0, le=10)
    price_cny:        float = Field(0.0, ge=0)
    license_type:     str  = "enterprise_internal"
    formats:          List[str] = ["jsonl", "zip"]


class ReviewSFTRequest(BaseModel):
    action:      str           # approve | reject | edit
    reviewer_id: str
    new_output:  Optional[str] = None
    note:        str = ""


class ReviewDPORequest(BaseModel):
    action:      str           # approve | reject
    reviewer_id: str
    note:        str = ""


class BatchApproveRequest(BaseModel):
    reviewer_id:    str
    min_auto_score: float = 7.5
    domain:         Optional[str] = None
    limit:          int = 500


class SellRequest(BaseModel):
    package_id:  str
    sale_amount: float = Field(..., gt=0)
    buyer_id:    str = ""


# ════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════

async def _push_sse(job_id: str, event: str, data: dict):
    q = _sse_queues.get(job_id)
    if q:
        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        await q.put(msg)


async def _push_done(job_id: str):
    await _push_sse(job_id, "done", {"job_id": job_id})
    q = _sse_queues.get(job_id)
    if q:
        await q.put(None)   # sentinel


# ════════════════════════════════════════════════════════
# 数据集生产端点
# ════════════════════════════════════════════════════════

@dataset_router.post("/ingest")
async def ingest_material(req: IngestRequest):
    """上传创作者原始素材（持久化到 SQLite）"""
    mat = CreatorMaterial(
        creator_id    = req.creator_id,
        raw_content   = req.raw_content,
        material_type = req.material_type,
        source_path   = req.source_path,
        metadata      = {"domain": req.domain, **req.metadata},
    )
    mat.compute_hash()
    await db.insert_material(mat)

    return {
        "material_id":  mat.material_id,
        "content_hash": mat.content_hash,
        "creator_id":   mat.creator_id,
        "char_count":   len(mat.raw_content),
    }


@dataset_router.get("/materials")
async def list_materials(creator_id: str = None, limit: int = 100):
    """查看素材库"""
    rows = await db.list_materials(creator_id=creator_id, limit=limit)
    return {"total": len(rows), "materials": rows}


@dataset_router.post("/produce")
async def produce_dataset(req: ProduceRequest, background_tasks: BackgroundTasks):
    """
    启动生产任务（后台异步）。
    立即返回 job_id，通过 GET /job/{id} 轮询或 GET /job/{id}/stream SSE 实时获取进度。
    """
    # 从 SQLite 加载素材
    materials = []
    for mid in req.material_ids:
        row = await db.get_material(mid)
        if not row:
            raise HTTPException(status_code=404, detail=f"素材 {mid} 不存在")
        mat = CreatorMaterial(
            material_id   = row["material_id"],
            creator_id    = row["creator_id"],
            raw_content   = row["raw_content"],
            material_type = row["material_type"],
            source_path   = row["source_path"],
            metadata      = json.loads(row.get("metadata_json") or "{}"),
            content_hash  = row["content_hash"],
        )
        materials.append(mat)

    try:
        mode = AnnotationMode(req.annotation_mode)
    except ValueError:
        mode = AnnotationMode.AUTO_REVIEW

    # 选标注器
    if req.use_rag:
        from dataset.rag_annotator import RAGAnnotator
        annotator = RAGAnnotator(era_name=req.rag_era, mode=mode)
    else:
        from dataset.annotator import AutoAnnotator
        annotator = AutoAnnotator(mode=mode)

    job_id = str(uuid.uuid4())
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _sse_queues[job_id] = q

    async def progress_cb(job, msg: str):
        await _push_sse(job_id, "progress", {
            "stage":        job.stage,
            "progress_pct": job.progress_pct(),
            "message":      msg,
            "timings":      job.timings,
        })

    pipeline = DatasetProductionPipeline(
        annotation_mode     = mode,
        annotation_concurrency = 5,
        progress_callback   = progress_cb,
    )
    pipeline.annotator = annotator
    pipeline.batch_job.annotator = annotator
    _active_pipelines[job_id] = pipeline

    async def _run():
        try:
            package = await pipeline.run(
                materials    = materials,
                name         = req.name,
                description  = req.description,
                target_types = req.target_types,
                min_quality  = req.min_quality,
                price_cny    = req.price_cny,
                license_type = req.license_type,
                formats      = req.formats,
                do_revenue   = False,
            )
            # 持久化到 SQLite
            await db.insert_package(package)

            await _push_sse(job_id, "complete", {
                "package_id":    package.package_id,
                "total_samples": package.total_samples,
                "avg_quality":   package.avg_quality,
                "zip_path":      package.export_paths.get("zip", ""),
            })
        except Exception as e:
            await _push_sse(job_id, "error", {"message": str(e)})
        finally:
            await _push_done(job_id)
            _active_pipelines.pop(job_id, None)

    background_tasks.add_task(_run)

    return {
        "job_id":       job_id,
        "status":       "started",
        "materials":    len(materials),
        "use_rag":      req.use_rag,
        "stream_url":   f"/api/dataset/job/{job_id}/stream",
        "poll_url":     f"/api/dataset/job/{job_id}",
    }


@dataset_router.get("/job/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """
    SSE 实时进度流。

    事件类型：
      progress  — 阶段推进（含 stage / progress_pct / message）
      complete  — 生产完成（含 package_id / total_samples）
      error     — 任务失败
      done      — 流结束（客户端可关闭连接）
    """
    q = _sse_queues.get(job_id)
    if q is None:
        # 任务已结束，查磁盘状态
        status = None
        for pl in _active_pipelines.values():
            s = pl.get_job_status(job_id)
            if s:
                status = s
                break
        data = json.dumps({"job_id": job_id, "status": status or "not_found"}, ensure_ascii=False)
        return StreamingResponse(
            iter([f"event: done\ndata: {data}\n\n"]),
            media_type="text/event-stream",
        )

    async def event_gen():
        try:
            while True:
                msg = await asyncio.wait_for(q.get(), timeout=30)
                if msg is None:   # sentinel → 结束
                    break
                yield msg
        except asyncio.TimeoutError:
            yield "event: keepalive\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@dataset_router.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """轮询生产任务进度"""
    for pl in _active_pipelines.values():
        s = pl.get_job_status(job_id)
        if s:
            return s
    # 从磁盘恢复
    import os
    from dataset.pipeline import STATE_DIR
    path = os.path.join(STATE_DIR, f"{job_id}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")


@dataset_router.get("/jobs")
async def list_jobs():
    """列出最近 50 个任务"""
    pls = list(_active_pipelines.values())
    if pls:
        return pls[0].list_jobs()
    from dataset.pipeline import DatasetProductionPipeline
    return DatasetProductionPipeline().list_jobs()


@dataset_router.get("/packages")
async def list_packages(limit: int = 50):
    return await db.list_packages(limit=limit)


@dataset_router.get("/package/{package_id}")
async def get_package(package_id: str):
    pkg = await db.get_package(package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail=f"包 {package_id} 不存在")
    return pkg


@dataset_router.post("/sell")
async def record_sale(req: SellRequest):
    """记录销售 → 触发分润"""
    pkg_data = await db.get_package(req.package_id)
    if not pkg_data:
        raise HTTPException(status_code=404, detail=f"包 {req.package_id} 不存在")

    pkg = DatasetPackage(
        package_id            = pkg_data["package_id"],
        name                  = pkg_data["name"],
        price_cny             = pkg_data["price_cny"],
        creator_contributions = pkg_data.get("creator_contributions", {}),
    )

    calc    = RevenueCalculator()
    records = calc.calculate(pkg, req.sale_amount, req.buyer_id)
    await db.insert_revenue_records(records)

    return {
        "package_id":      req.package_id,
        "sale_amount":     req.sale_amount,
        "records_created": len(records),
        "creator_pool":    round(req.sale_amount * 0.70, 2),
        "platform_fee":    round(req.sale_amount * 0.30, 2),
        "distributions": [
            {
                "creator_id":   r.creator_id,
                "contribution": f"{r.contribution_ratio:.1%}",
                "amount_cny":   r.creator_share,
            }
            for r in records
        ],
    }


# ── 样本查询（供企业客户预览）────────────────────────

@dataset_router.get("/samples/sft")
async def list_sft_samples(
    status:     str   = "approved",
    creator_id: str   = None,
    min_score:  float = 0.0,
    limit:      int   = 100,
):
    rows = await db.list_sft(status=status, creator_id=creator_id,
                              min_score=min_score, limit=limit)
    return {"total": len(rows), "samples": rows}


@dataset_router.get("/samples/dpo")
async def list_dpo_samples(status: str = "approved", limit: int = 100):
    rows = await db.list_dpo(status=status, limit=limit)
    return {"total": len(rows), "samples": rows}


# ════════════════════════════════════════════════════════
# 人工复核端点
# ════════════════════════════════════════════════════════

@review_router.get("/queue/sft")
async def get_sft_review_queue(domain: str = None, limit: int = 20):
    """获取待审核 SFT 样本（标注置信度低的优先）"""
    return await review_queue.get_sft_queue(domain=domain, limit=limit)


@review_router.get("/queue/dpo")
async def get_dpo_review_queue(limit: int = 20):
    """获取待审核 DPO 样本"""
    return await review_queue.get_dpo_queue(limit=limit)


@review_router.post("/sft/{sample_id}")
async def review_sft_sample(sample_id: str, req: ReviewSFTRequest):
    """
    审核单条 SFT 样本

    action:
      approve  直接通过，进入数据集
      reject   拒绝，不入库
      edit     修改 output 后暂存，等待二次质检
    """
    result = await review_operator.review_sft(
        sample_id   = sample_id,
        action      = req.action,
        reviewer_id = req.reviewer_id,
        new_output  = req.new_output,
        note        = req.note,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@review_router.post("/dpo/{sample_id}")
async def review_dpo_sample(sample_id: str, req: ReviewDPORequest):
    """审核单条 DPO 样本"""
    result = await review_operator.review_dpo(
        sample_id   = sample_id,
        action      = req.action,
        reviewer_id = req.reviewer_id,
        note        = req.note,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@review_router.post("/sft/batch-approve")
async def batch_approve_sft(req: BatchApproveRequest):
    """
    批量通过高分 pending 样本（质检分 >= min_auto_score）
    适合快速处理自动标注质量已足够好的样本
    """
    return await review_operator.batch_approve_sft(
        reviewer_id    = req.reviewer_id,
        min_auto_score = req.min_auto_score,
        domain         = req.domain,
        limit          = req.limit,
    )


@review_router.get("/stats/daily")
async def daily_review_stats(reviewer_id: str = None):
    return await review_stats.daily_stats(reviewer_id=reviewer_id)


@review_router.get("/stats/queue")
async def queue_overview():
    return await review_stats.queue_overview()


@review_router.get("/stats/leaderboard")
async def reviewer_leaderboard(days: int = 7):
    return await review_stats.reviewer_leaderboard(days=days)


# ════════════════════════════════════════════════════════
# 创作者端点
# ════════════════════════════════════════════════════════

@creator_router.get("/{creator_id}/balance")
async def get_creator_balance(creator_id: str):
    return await db.get_creator_balance(creator_id)


@creator_router.get("/{creator_id}/records")
async def get_creator_records(creator_id: str, limit: int = 30):
    records = await db.get_creator_records(creator_id, limit=limit)
    return {"creator_id": creator_id, "total": len(records), "records": records}


@creator_router.get("/{creator_id}/materials")
async def get_creator_materials(creator_id: str, limit: int = 50):
    rows = await db.list_materials(creator_id=creator_id, limit=limit)
    return {"creator_id": creator_id, "total": len(rows), "materials": rows}


@creator_router.post("/{creator_id}/settle")
async def settle_creator(creator_id: str):
    result = await db.settle_creator(creator_id)
    if not result:
        bal = await db.get_creator_balance(creator_id)
        raise HTTPException(
            status_code=400,
            detail=f"待结算 ¥{bal['pending_cny']:.2f} 低于最低阈值 ¥10.00",
        )
    return result


# ════════════════════════════════════════════════════════
# 平台管理
# ════════════════════════════════════════════════════════

@platform_router.get("/summary")
async def platform_summary():
    return await db.get_platform_summary()

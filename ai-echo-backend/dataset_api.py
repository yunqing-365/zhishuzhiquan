# dataset_api.py — 数据集生产 & 估值集成 API
"""
指数之源 · 数据集生产系统 API

业务链路：
  创作者上传素材 → 自动标注生产 → 预言机估值 → ZK 链上确权 → 企业购买 → 分润

路由前缀：
  /api/dataset/*     素材上传 & 生产流程
  /api/creator/*     创作者收益查询
  /api/platform/*    管理员后台

挂载方式（oracle_engine.py 末尾）：
  from dataset_api import dataset_router
  app.include_router(dataset_router)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ── 数据集子系统 ────────────────────────────────────────────────────
from dataset.schema import (
    CreatorMaterial, DatasetType, AnnotationStatus, QualityTier
)
from dataset.pipeline import DatasetPipeline, PipelineJob, PipelineStage
from dataset.versioning import DatasetVersionManager
from dataset.human_review import HumanReviewQueue
from creator.revenue_calculator import RevenueCalculator, CreatorLedger
from config import get_settings

dataset_router = APIRouter(tags=["数据集生产"])
_settings = get_settings()

# ── 内存存储（可按需替换为 DB 层）──────────────────────────────────
_material_store: dict[str, dict]  = {}   # material_id → dict
_package_store:  dict[str, dict]  = {}   # package_id  → dict
_pipeline       = DatasetPipeline()
_version_mgr    = DatasetVersionManager()
_review_queue   = HumanReviewQueue()
_ledger         = CreatorLedger(_settings.creator_ledger_path)
_revenue_calc   = RevenueCalculator(platform_ratio=_settings.platform_revenue_ratio)


# ════════════════════════════════════════════════════════════════════
# 请求 / 响应模型
# ════════════════════════════════════════════════════════════════════

class IngestRequest(BaseModel):
    creator_id:    str
    material_type: str = Field("text", description="text / image / audio / video")
    raw_content:   str = Field(...,    description="文本内容，或 base64 编码的媒体文件")
    metadata:      dict = {}

class ProduceRequest(BaseModel):
    material_ids:  List[str]
    dataset_type:  str = Field("sft",  description="sft / dpo / pretrain / multimodal")
    name:          str = "未命名数据集"
    domain:        str = ""
    annotation_mode: str = Field("auto", description="auto / manual / rag")

class SellRequest(BaseModel):
    package_id:   str
    buyer_id:     str
    price_cny:    float

class ReviewDecisionRequest(BaseModel):
    sample_id:  str
    sample_type: str   # sft / dpo / pretrain
    approved:   bool
    reviewer_id: str = "admin"
    comment:    str = ""


# ════════════════════════════════════════════════════════════════════
# 素材上传
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/ingest", summary="上传创作者素材")
async def ingest_material(req: IngestRequest):
    """
    创作者上传一条原始素材（文本 / 图像 / 音频 / 视频）。
    返回 material_id，后续用于发起生产任务。
    """
    mat = CreatorMaterial(
        creator_id    = req.creator_id,
        material_type = req.material_type,
        raw_content   = req.raw_content,
        metadata      = req.metadata,
    )
    mat.compute_hash()
    _material_store[mat.material_id] = {
        "material_id":   mat.material_id,
        "creator_id":    mat.creator_id,
        "material_type": mat.material_type,
        "content_hash":  mat.content_hash,
        "preview":       req.raw_content[:120] + ("…" if len(req.raw_content) > 120 else ""),
        "metadata":      req.metadata,
        "uploaded_at":   mat.uploaded_at.isoformat(),
        "_raw":          mat,   # 内存持有原始对象
    }
    return {
        "material_id":   mat.material_id,
        "content_hash":  mat.content_hash,
        "material_type": mat.material_type,
        "status":        "ingested",
    }


@dataset_router.get("/api/dataset/materials", summary="列出素材")
async def list_materials(
    creator_id: str = Query(""),
    limit: int = Query(50, le=200),
):
    items = list(_material_store.values())
    if creator_id:
        items = [i for i in items if i["creator_id"] == creator_id]
    items.sort(key=lambda x: x["uploaded_at"], reverse=True)
    # 不返回 _raw 字段
    return {"materials": [{k: v for k, v in m.items() if k != "_raw"} for m in items[:limit]]}


# ════════════════════════════════════════════════════════════════════
# 数据集生产（后台异步流水线）
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/produce", summary="启动数据集生产任务")
async def produce_dataset(req: ProduceRequest, bg: BackgroundTasks):
    """
    异步启动五阶段生产流水线：
      标注 → 质检 → 去重 → 打包 → 分润结算
    立即返回 job_id，通过 SSE /api/dataset/job/{id}/stream 监控进度。
    """
    missing = [mid for mid in req.material_ids if mid not in _material_store]
    if missing:
        raise HTTPException(400, f"素材 ID 不存在: {missing}")

    materials = [_material_store[mid]["_raw"] for mid in req.material_ids]

    job = _pipeline.create_job(
        name          = req.name,
        materials     = materials,
        dataset_type  = DatasetType(req.dataset_type),
        domain        = req.domain,
        annotation_mode = req.annotation_mode,
    )

    bg.add_task(_pipeline.run_job, job.job_id)

    return {
        "job_id":    job.job_id,
        "status":    "started",
        "materials": len(materials),
        "stream_url": f"/api/dataset/job/{job.job_id}/stream",
    }


@dataset_router.get("/api/dataset/job/{job_id}", summary="查询任务进度")
async def get_job(job_id: str):
    job = _pipeline.get_job(job_id)
    if not job:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return job.to_dict()


@dataset_router.get("/api/dataset/job/{job_id}/stream", summary="SSE 实时进度流")
async def stream_job_progress(job_id: str):
    """
    Server-Sent Events 推送生产进度，前端直接 EventSource 订阅。
    每 0.8s 推送一帧，直到任务完成或失败。
    """
    async def _generate():
        for _ in range(300):   # 最多等 240s
            job = _pipeline.get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                return
            payload = job.to_dict()
            yield f"data: {json.dumps(payload)}\n\n"
            if job.stage in (PipelineStage.DONE, PipelineStage.FAILED):
                # 完成后把 package 存入 _package_store
                if job.package_id and job.package_id not in _package_store:
                    pkg = _pipeline.get_package(job.package_id)
                    if pkg:
                        _package_store[job.package_id] = _pkg_to_dict(pkg)
                return
            await asyncio.sleep(0.8)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@dataset_router.get("/api/dataset/jobs", summary="列出最近任务")
async def list_jobs(limit: int = Query(50, le=200)):
    jobs = _pipeline.list_jobs(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}


# ════════════════════════════════════════════════════════════════════
# 数据集包 & 销售
# ════════════════════════════════════════════════════════════════════

def _pkg_to_dict(pkg) -> dict:
    return {
        "package_id":      pkg.package_id,
        "name":            pkg.name,
        "description":     pkg.description,
        "dataset_type":    pkg.dataset_type,
        "version":         pkg.version,
        "domain":          pkg.domain,
        "total_samples":   pkg.total_samples,
        "approved_samples": pkg.approved_samples,
        "avg_quality":     round(pkg.avg_quality, 2),
        "platinum_count":  pkg.platinum_count,
        "gold_count":      pkg.gold_count,
        "price_cny":       pkg.price_cny,
        "creator_contributions": pkg.creator_contributions,
        "export_paths":    pkg.export_paths,
        "created_at":      pkg.created_at.isoformat(),
    }


@dataset_router.get("/api/dataset/packages", summary="列出已生产的数据集包")
async def list_packages():
    return {"packages": list(_package_store.values())}


@dataset_router.get("/api/dataset/package/{package_id}", summary="数据集包详情")
async def get_package(package_id: str):
    pkg = _package_store.get(package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {package_id} 不存在")
    return pkg


@dataset_router.post("/api/dataset/sell", summary="记录销售 & 触发分润")
async def sell_dataset(req: SellRequest):
    """
    记录企业客户购买 → 触发创作者分润计算 → 写入账本。
    """
    pkg = _package_store.get(req.package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {req.package_id} 不存在")

    records = _revenue_calc.calculate(
        package_id             = req.package_id,
        total_revenue          = req.price_cny,
        creator_contributions  = pkg["creator_contributions"],
    )
    for rec in records:
        _ledger.add_record(rec)
    _ledger.save()

    return {
        "sale_id":         str(uuid.uuid4()),
        "package_id":      req.package_id,
        "buyer_id":        req.buyer_id,
        "price_cny":       req.price_cny,
        "revenue_records": [
            {
                "creator_id":         r.creator_id,
                "contribution_ratio": round(r.contribution_ratio, 4),
                "creator_share_cny":  round(r.creator_share, 2),
            }
            for r in records
        ],
        "sold_at": datetime.utcnow().isoformat(),
    }


# ════════════════════════════════════════════════════════════════════
# 创作者收益
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/creator/{creator_id}/earnings", summary="创作者收益汇总")
async def creator_earnings(creator_id: str):
    summary = _ledger.get_summary(creator_id)
    records = _ledger.get_records(creator_id)
    return {
        "creator_id": creator_id,
        "summary":    summary,
        "records":    [
            {
                "record_id":         r.record_id,
                "package_id":        r.package_id,
                "total_revenue":     r.total_revenue,
                "contribution_ratio": round(r.contribution_ratio, 4),
                "creator_share":     round(r.creator_share, 2),
                "status":            r.status,
                "created_at":        r.created_at.isoformat(),
            }
            for r in records
        ],
    }


@dataset_router.get("/api/creator/leaderboard", summary="创作者贡献排行榜")
async def creator_leaderboard(limit: int = Query(20, le=100)):
    board = _ledger.leaderboard(limit=limit)
    return {"leaderboard": board}


# ════════════════════════════════════════════════════════════════════
# 人工复核队列
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/review/queue", summary="获取待复核样本队列")
async def review_queue(limit: int = Query(20, le=100)):
    items = _review_queue.get_pending(limit=limit)
    return {"queue": items, "total": _review_queue.pending_count()}


@dataset_router.post("/api/review/decide", summary="提交人工复核决定")
async def review_decide(req: ReviewDecisionRequest):
    ok = _review_queue.decide(
        sample_id   = req.sample_id,
        sample_type = req.sample_type,
        approved    = req.approved,
        reviewer_id = req.reviewer_id,
        comment     = req.comment,
    )
    if not ok:
        raise HTTPException(404, f"样本 {req.sample_id} 不在复核队列中")
    return {"sample_id": req.sample_id, "decision": "approved" if req.approved else "rejected"}


# ════════════════════════════════════════════════════════════════════
# 数据集版本管理
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/versions/{package_id}", summary="查看版本历史")
async def list_versions(package_id: str):
    versions = _version_mgr.list_versions(package_id)
    return {"package_id": package_id, "versions": versions}


@dataset_router.post("/api/versions/{package_id}/tag", summary="给版本打标签")
async def tag_version(package_id: str, tag: str = Query(...)):
    ok = _version_mgr.tag(package_id, tag)
    if not ok:
        raise HTTPException(404, "package 不存在或版本记录为空")
    return {"package_id": package_id, "tag": tag, "ok": True}


# ════════════════════════════════════════════════════════════════════
# 平台管理后台
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/platform/stats", summary="平台整体统计")
async def platform_stats():
    total_materials = len(_material_store)
    total_packages  = len(_package_store)
    total_jobs      = len(_pipeline.list_jobs(limit=9999))
    done_jobs       = sum(1 for j in _pipeline.list_jobs(limit=9999) if j.stage == PipelineStage.DONE)
    total_revenue   = sum(r.total_revenue for r in _ledger.all_records())
    return {
        "total_materials": total_materials,
        "total_packages":  total_packages,
        "total_jobs":      total_jobs,
        "done_jobs":       done_jobs,
        "total_revenue_cny": round(total_revenue, 2),
        "pending_review":  _review_queue.pending_count(),
    }

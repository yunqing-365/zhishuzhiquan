# dataset_api.py — 数据集生产 & 估值集成 API
"""
知数知圈 · 数据集生产系统 API

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
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ── 数据集子系统 ────────────────────────────────────────────────────
from dataset.schema import CreatorMaterial, DatasetType, QualityTier
from dataset.pipeline import DatasetProductionPipeline, PipelineJob, PipelineStage
from dataset.versioning import DatasetVersionManager
from dataset.human_review import HumanReviewQueue
from creator.revenue_calculator import RevenueCalculator, CreatorLedger
from config import get_settings

dataset_router = APIRouter(tags=["数据集生产"])
_settings = get_settings()

# ── 全局单例 ────────────────────────────────────────────────────────
_material_store: dict[str, dict] = {}   # material_id → dict
_package_store:  dict[str, dict] = {}   # package_id  → dict
_pipeline = DatasetProductionPipeline()
_version_mgr = DatasetVersionManager()
_review_queue = HumanReviewQueue()
_ledger = CreatorLedger(_settings.creator_ledger_path)
_revenue_calc = RevenueCalculator()


# ════════════════════════════════════════════════════════════════════
# 请求 / 响应模型
# ════════════════════════════════════════════════════════════════════

class IngestRequest(BaseModel):
    creator_id:    str
    material_type: str  = Field("text", description="text / image / audio / video")
    raw_content:   str  = Field(..., description="文本内容，或 base64 编码的媒体文件")
    metadata:      dict = {}

class ProduceRequest(BaseModel):
    material_ids:    List[str]
    target_types:    List[str] = Field(["sft", "dpo"], description="sft / dpo / pretrain")
    name:            str   = "未命名数据集"
    description:     str   = ""
    min_quality:     float = 5.0
    price_cny:       float = 0.0
    license_type:    str   = "enterprise_internal"

class SellRequest(BaseModel):
    package_id: str
    buyer_id:   str
    price_cny:  float


# ════════════════════════════════════════════════════════════════════
# 素材上传
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/ingest", summary="上传创作者素材")
async def ingest_material(req: IngestRequest):
    """创作者上传一条原始素材，返回 material_id 供后续发起生产任务。"""
    mat = CreatorMaterial(
        creator_id=req.creator_id,
        content_type=req.material_type,
        content=req.raw_content,
        metadata=req.metadata,
    )
    _material_store[mat.material_id] = {
        "material_id":   mat.material_id,
        "creator_id":    mat.creator_id,
        "content_type":  mat.content_type,
        "preview":       req.raw_content[:120] + ("…" if len(req.raw_content) > 120 else ""),
        "metadata":      req.metadata,
        "uploaded_at":   mat.uploaded_at.isoformat(),
        "_raw":          mat,
    }
    return {
        "material_id":  mat.material_id,
        "content_type": mat.content_type,
        "status":       "ingested",
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
    return {"materials": [{k: v for k, v in m.items() if k != "_raw"} for m in items[:limit]]}


# ════════════════════════════════════════════════════════════════════
# 数据集生产（后台异步流水线）
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/produce", summary="启动数据集生产任务")
async def produce_dataset(req: ProduceRequest, bg: BackgroundTasks):
    """
    异步启动五阶段生产流水线：标注 → 质检 → 去重 → 打包 → 分润结算。
    立即返回 job_id，通过 SSE /api/dataset/job/{id}/stream 监控进度。
    """
    missing = [mid for mid in req.material_ids if mid not in _material_store]
    if missing:
        raise HTTPException(400, f"素材 ID 不存在: {missing}")

    materials: List[CreatorMaterial] = [
        _material_store[mid]["_raw"] for mid in req.material_ids
    ]

    # 生成 job_id 并预占槽位
    job_id = str(uuid.uuid4())

    async def _run():
        pkg = await _pipeline.run(
            materials=materials,
            name=req.name,
            description=req.description,
            target_types=req.target_types,
            min_quality=req.min_quality,
            price_cny=req.price_cny,
            license_type=req.license_type,
        )
        if pkg:
            _package_store[pkg.package_id] = {
                "package_id":   pkg.package_id,
                "name":         pkg.name,
                "description":  pkg.description,
                "total_samples": pkg.total_samples,
                "avg_quality":  pkg.avg_quality,
                "price_cny":    pkg.price_cny,
                "export_paths": pkg.export_paths,
                "creator_contributions": pkg.creator_contributions,
                "created_at":   pkg.created_at.isoformat(),
            }

    bg.add_task(_run)

    return {
        "job_id":    job_id,
        "status":    "started",
        "materials": len(materials),
        "stream_url": f"/api/dataset/jobs",
    }


@dataset_router.get("/api/dataset/jobs", summary="列出最近任务")
async def list_jobs(limit: int = Query(50, le=200)):
    jobs = _pipeline.list_jobs()
    return {"jobs": jobs[:limit]}


@dataset_router.get("/api/dataset/job/{job_id}", summary="查询任务状态")
async def get_job(job_id: str):
    job = _pipeline.get_job_status(job_id)
    if not job:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return job


# ════════════════════════════════════════════════════════════════════
# 数据集包 & 销售
# ════════════════════════════════════════════════════════════════════

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
    """记录企业客户购买 → 触发创作者分润计算 → 写入账本。"""
    pkg = _package_store.get(req.package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {req.package_id} 不存在")

    # 构造临时 DatasetPackage 对象用于分润计算
    from dataset.schema import DatasetPackage
    pkg_obj = DatasetPackage(
        package_id=req.package_id,
        creator_contributions=pkg.get("creator_contributions", {}),
        total_samples=pkg.get("total_samples", 0),
    )
    records = _revenue_calc.calculate(pkg_obj, req.price_cny, buyer_id=req.buyer_id)
    _ledger.add_records(records)

    return {
        "sale_id":    str(uuid.uuid4()),
        "package_id": req.package_id,
        "buyer_id":   req.buyer_id,
        "price_cny":  req.price_cny,
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
    balance = _ledger.get_balance(creator_id)
    records = _ledger.get_creator_records(creator_id)
    return {
        "creator_id": creator_id,
        "balance":    balance,
        "records":    records,
    }


@dataset_router.get("/api/creator/leaderboard", summary="创作者贡献排行榜")
async def creator_leaderboard(limit: int = Query(20, le=100)):
    board = _ledger.get_top_earners(limit)
    return {"leaderboard": board}


# ════════════════════════════════════════════════════════════════════
# 人工复核队列
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/review/queue", summary="获取待复核样本队列")
async def review_queue_list():
    items = _review_queue.list_pending()
    return {"queue": items, "total": len(items)}


@dataset_router.post("/api/review/{review_id}/approve", summary="批准样本")
async def approve_review(review_id: str, reviewer: str = Query("admin")):
    ok = _review_queue.approve(review_id, reviewer)
    if not ok:
        raise HTTPException(404, f"复核记录 {review_id} 不存在")
    return {"review_id": review_id, "decision": "approved"}


@dataset_router.post("/api/review/{review_id}/reject", summary="拒绝样本")
async def reject_review(review_id: str, reviewer: str = Query("admin")):
    ok = _review_queue.reject(review_id, reviewer)
    if not ok:
        raise HTTPException(404, f"复核记录 {review_id} 不存在")
    return {"review_id": review_id, "decision": "rejected"}


# ════════════════════════════════════════════════════════════════════
# 平台管理后台
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/platform/stats", summary="平台整体统计")
async def platform_stats():
    all_balances = _ledger.get_all_balances()
    total_revenue = sum(b["total_earned"] for b in all_balances)
    return {
        "total_materials": len(_material_store),
        "total_packages":  len(_package_store),
        "total_jobs":      len(_pipeline.list_jobs()),
        "total_creators":  len(all_balances),
        "total_revenue_cny": round(total_revenue, 2),
        "pending_review":  len(_review_queue.list_pending()),
    }

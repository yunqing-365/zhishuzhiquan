# dataset_api.py — 数据集生产 & 估值集成 API  v2
"""
知数知圈 · 数据集生产系统 API

升级日志 v2:
  [修复] 素材与包存储从内存 dict 迁移至 SQLite，重启不再丢数据
  [新增] 上传/生产接口需要 JWT 认证（creator_id 从 Token 读取）
  [新增] 列出素材/包时支持按 creator_id 过滤（Token 自动限定）
  [修复] 生产完成后包元数据持久化到 dataset_packages 表

路由前缀：
  /api/dataset/*     素材上传 & 生产流程
  /api/creator/*     创作者收益查询
  /api/platform/*    管理员后台
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ── 数据集子系统 ────────────────────────────────────────────────────
from dataset.schema import CreatorMaterial, DatasetType, QualityTier
from dataset.pipeline import DatasetProductionPipeline, PipelineJob, PipelineStage
from dataset.versioning import DatasetVersionManager
from dataset.human_review import HumanReviewQueue
from creator.revenue_calculator import RevenueCalculator, CreatorLedger
from config import get_settings
import storage
from auth import get_current_creator, get_optional_creator
from dataset.content_safety import check as safety_check

dataset_router = APIRouter(tags=["数据集生产"])
_settings = get_settings()

# ── 单例（无状态，不持有数据）───────────────────────────────────────
_pipeline    = DatasetProductionPipeline()
_version_mgr = DatasetVersionManager()
_review_queue = HumanReviewQueue()
_ledger      = CreatorLedger(_settings.creator_ledger_json_path)
_revenue_calc = RevenueCalculator()


# ════════════════════════════════════════════════════════════════════
# 请求 / 响应模型
# ════════════════════════════════════════════════════════════════════

class IngestRequest(BaseModel):
    material_type: str  = Field("text", description="text / image / audio / video")
    raw_content:   str  = Field(..., description="文本内容，或 base64 编码的媒体文件")
    metadata:      dict = {}

class ProduceRequest(BaseModel):
    material_ids:  List[str]
    target_types:  List[str] = Field(["sft", "dpo"], description="sft / dpo / pretrain")
    name:          str   = "未命名数据集"
    description:   str   = ""
    min_quality:   float = 5.0
    price_cny:     float = 0.0
    license_type:  str   = "enterprise_internal"

class SellRequest(BaseModel):
    package_id: str
    buyer_id:   str
    price_cny:  float


# ════════════════════════════════════════════════════════════════════
# 素材上传（需登录）
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/ingest", summary="上传创作者素材")
async def ingest_material(
    req: IngestRequest,
    creator: dict = Depends(get_current_creator),
):
    """
    创作者上传一条原始素材，返回 material_id。
    creator_id 从 JWT Token 自动读取，不再由前端传入。
    """
    # ── 内容安全审核（三层：关键词→启发式→LLM可选）─────────────────
    safety = await safety_check(req.raw_content, req.material_type)
    if not safety.passed:
        raise HTTPException(422, {
            "detail":     "内容未通过安全审核，无法入库",
            "reason":     safety.reason,
            "category":   safety.category,
            "risk_score": safety.risk_score,
        })

    mat = CreatorMaterial(
        creator_id=creator["creator_id"],
        content_type=req.material_type,
        content=req.raw_content,
        metadata=req.metadata,
    )
    ok = storage.save_material(
        material_id=mat.material_id,
        creator_id=mat.creator_id,
        content_type=mat.content_type,
        content=req.raw_content,
        metadata=req.metadata,
    )
    if not ok:
        raise HTTPException(500, "素材存储失败，请稍后重试")

    return {
        "material_id":  mat.material_id,
        "content_type": mat.content_type,
        "creator_id":   mat.creator_id,
        "status":       "ingested",
    }


@dataset_router.get("/api/dataset/materials", summary="列出我的素材")
async def list_materials(
    limit: int = Query(50, le=200),
    creator: dict = Depends(get_current_creator),
):
    """列出当前登录创作者的全部素材（Token 自动过滤，不暴露他人数据）。"""
    items = storage.list_materials(creator_id=creator["creator_id"], limit=limit)
    return {"materials": items, "total": len(items)}


# ════════════════════════════════════════════════════════════════════
# 数据集生产（需登录）
# ════════════════════════════════════════════════════════════════════

@dataset_router.post("/api/dataset/produce", summary="启动数据集生产任务")
async def produce_dataset(
    req: ProduceRequest,
    bg: BackgroundTasks,
    creator: dict = Depends(get_current_creator),
):
    """
    异步启动五阶段生产流水线：标注 → 质检 → 去重 → 打包 → 分润结算。
    立即返回 job_id，通过 /api/dataset/jobs 监控进度。
    """
    # 校验素材属于当前创作者
    materials: List[CreatorMaterial] = []
    missing = []
    for mid in req.material_ids:
        row = storage.get_material(mid)
        if row is None:
            missing.append(mid)
            continue
        if row["creator_id"] != creator["creator_id"]:
            raise HTTPException(403, f"素材 {mid} 不属于当前账户")
        mat = CreatorMaterial(
            material_id=row["material_id"],
            creator_id=row["creator_id"],
            content_type=row["content_type"],
            content=row["content"],
            metadata=row["metadata"],
        )
        materials.append(mat)

    if missing:
        raise HTTPException(400, f"素材 ID 不存在: {missing}")

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
            storage.save_package_db({
                "package_id":             pkg.package_id,
                "name":                   pkg.name,
                "description":            getattr(pkg, "description", ""),
                "dataset_type":           getattr(pkg, "dataset_type", ""),
                "domain":                 getattr(pkg, "domain", ""),
                "total_samples":          pkg.total_samples,
                "approved_samples":       getattr(pkg, "approved_samples", 0),
                "avg_quality":            pkg.avg_quality,
                "platinum_count":         getattr(pkg, "platinum_count", 0),
                "gold_count":             getattr(pkg, "gold_count", 0),
                "price_cny":              pkg.price_cny,
                "creator_contributions":  pkg.creator_contributions,
                "export_paths":           pkg.export_paths,
                "created_at":             pkg.created_at.isoformat(),
            })

    bg.add_task(_run)

    return {
        "job_id":    job_id,
        "status":    "started",
        "materials": len(materials),
        "stream_url": f"/api/dataset/job/{job_id}/stream",   # ✅ 修正：指向真实 SSE 端点
    }


@dataset_router.get("/api/dataset/jobs", summary="列出最近任务")
async def list_jobs(limit: int = Query(50, le=200)):
    return {"jobs": _pipeline.list_jobs()[:limit]}


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
async def list_packages(limit: int = Query(50, le=200)):
    return {"packages": storage.list_packages_db(limit)}


@dataset_router.get("/api/dataset/package/{package_id}", summary="数据集包详情")
async def get_package(package_id: str):
    pkg = storage.get_package_db(package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {package_id} 不存在")
    return pkg


@dataset_router.post("/api/dataset/sell", summary="记录销售 & 触发分润")
async def sell_dataset(req: SellRequest):
    """记录企业客户购买 → 触发创作者分润计算 → 写入账本 → 写入购买记录（下载鉴权依据）。"""
    pkg = storage.get_package_db(req.package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {req.package_id} 不存在")

    from dataset.schema import DatasetPackage
    pkg_obj = DatasetPackage(
        package_id=req.package_id,
        creator_contributions=pkg.get("creator_contributions", {}),
        total_samples=pkg.get("total_samples", 0),
    )
    records = _revenue_calc.calculate(pkg_obj, req.price_cny, buyer_id=req.buyer_id)
    _ledger.add_records(records)

    # 写入购买记录，供 /api/dataset/download/{package_id} 鉴权使用
    sale_id = str(uuid.uuid4())
    from store.db import record_purchase as _record_purchase
    _record_purchase(sale_id, req.package_id, req.buyer_id, req.price_cny)

    return {
        "sale_id":    sale_id,
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
# 买家下载端点
# ════════════════════════════════════════════════════════════════════

class DownloadRequest(BaseModel):
    buyer_id: str = Field(..., description="买家 ID，需与购买时一致")
    file_type: str = Field("zip", description="zip | jsonl | parquet")


@dataset_router.post("/api/dataset/download/{package_id}", summary="买家下载已购数据集")
async def download_dataset(
    package_id: str,
    req: DownloadRequest,
):
    """
    验证买家已购买后，返回对应格式的文件流。

    file_type:
      zip     — 完整 ZIP 包（JSONL + Parquet + DataCard）
      jsonl   — 仅 JSONL 文件
      parquet — 仅 Parquet 文件（若存在）
    """
    from store.db import check_purchase as _check_purchase, increment_download as _incr_dl
    from fastapi.responses import FileResponse

    # ── 鉴权：检查购买记录 ──────────────────────────────────────────
    if not _check_purchase(req.buyer_id, package_id):
        raise HTTPException(
            403,
            detail={
                "error":   "未购买",
                "message": f"buyer_id={req.buyer_id!r} 尚未购买数据集包 {package_id}，"
                           "请先调用 /api/dataset/sell 完成购买。",
            },
        )

    # ── 获取包信息（export_paths）──────────────────────────────────
    pkg = storage.get_package_db(package_id)
    if not pkg:
        raise HTTPException(404, f"数据集包 {package_id} 不存在")

    export_paths: dict = pkg.get("export_paths", {})
    if isinstance(export_paths, str):
        try:
            import json as _json_ep
            export_paths = _json_ep.loads(export_paths)
        except Exception:
            export_paths = {}

    # ── 按 file_type 选择文件路径 ───────────────────────────────────
    file_type = req.file_type.lower().strip(".")
    path_map = {
        "zip":     export_paths.get("zip"),
        "jsonl":   export_paths.get("jsonl"),
        "parquet": export_paths.get("parquet"),
    }
    file_path = path_map.get(file_type)

    if not file_path:
        available = [k for k, v in path_map.items() if v]
        raise HTTPException(
            404,
            detail={
                "error":     "文件类型不存在",
                "requested": file_type,
                "available": available,
                "message":   f"该数据集包暂无 {file_type} 格式，可用格式: {available}",
            },
        )

    if not os.path.exists(file_path):
        raise HTTPException(
            410,
            detail={
                "error":   "文件已删除",
                "message": "数据集文件已从磁盘删除，请联系平台管理员重新生成。",
            },
        )

    # ── 递增下载计数 ────────────────────────────────────────────────
    _incr_dl(req.buyer_id, package_id)

    # ── 返回文件流 ──────────────────────────────────────────────────
    media_types = {
        "zip":     "application/zip",
        "jsonl":   "application/x-ndjson",
        "parquet": "application/octet-stream",
    }
    filename = os.path.basename(file_path)

    return FileResponse(
        path=file_path,
        media_type=media_types.get(file_type, "application/octet-stream"),
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Package-Id":        package_id,
            "X-Buyer-Id":          req.buyer_id,
        },
    )


# ════════════════════════════════════════════════════════════════════
# 创作者收益
# ════════════════════════════════════════════════════════════════════

@dataset_router.get("/api/creator/{creator_id}/earnings", summary="创作者收益汇总")
async def creator_earnings(creator_id: str):
    """收益汇总：从 CreatorLedger（唯一账本）读取。"""
    balance = _ledger.get_balance(creator_id)
    records = _ledger.get_creator_records(creator_id)
    return {"creator_id": creator_id, "balance": balance, "records": records}


@dataset_router.post("/api/creator/settle", summary="触发创作者结算")
async def settle_creator(creator: dict = Depends(get_current_creator)):
    """将当前创作者的 pending 收益标记为 settled（paid）。"""
    result = _ledger.settle(creator["creator_id"])
    if not result:
        return {"settled": False, "message": "无待结算收益"}
    return {"settled": True, **result}


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
        "total_materials": storage.count_materials(),
        "total_packages":  storage.count_packages_db(),
        "total_jobs":      len(_pipeline.list_jobs()),
        "total_creators":  len(all_balances),
        "total_revenue_cny": round(total_revenue, 2),
        "pending_review":  len(_review_queue.list_pending()),
    }


# ════════════════════════════════════════════════════════════════════
# v2 新增：批量上传端点
# ════════════════════════════════════════════════════════════════════
from fastapi import UploadFile, File
from dataset.batch_ingest import parse_upload

_BATCH_MAX_MB = 20
_BATCH_MAX_BYTES = _BATCH_MAX_MB * 1024 * 1024


@dataset_router.post("/api/dataset/batch_ingest", summary="批量上传素材（CSV/JSONL/ZIP/TXT）")
async def batch_ingest_materials(
    file: UploadFile = File(...),
    creator: dict = Depends(get_current_creator),
):
    """
    支持四种格式：
      CSV  — 必须有 content 列，可选 material_type / domain / tags
      JSONL — 每行 {content, material_type, domain, tags}
      ZIP  — 内含 .txt / .md 文件，每个文件一条素材
      TXT  — 按空行分段

    返回：
      {uploaded, skipped, errors, material_ids}
    """
    raw = await file.read()
    if len(raw) > _BATCH_MAX_BYTES:
        raise HTTPException(413, f"文件超过 {_BATCH_MAX_MB}MB 限制")

    filename = file.filename or "upload.txt"
    result = parse_upload(filename, raw)

    if not result.materials:
        raise HTTPException(400, {
            "detail": "未解析到有效素材",
            "errors": result.errors,
            "skipped": result.skipped,
        })

    saved_ids: list[str] = []
    fail_count = 0
    blocked_count = 0
    creator_id = creator["creator_id"]

    # 批量安全审核（并发，不阻断全部：单条违规只跳过该条）
    from dataset.content_safety import batch_check as safety_batch_check
    safety_items   = [{"content": m.content, "content_type": m.material_type} for m in result.materials]
    safety_results = await safety_batch_check(safety_items)

    for mat in result.materials:
        # 检查该条素材的安全审核结果
        mat_idx = result.materials.index(mat)
        mat_safety = safety_results[mat_idx] if mat_idx < len(safety_results) else None
        if mat_safety and not mat_safety.passed:
            blocked_count += 1
            result.errors.append(
                f"{mat.source_name}: 内容审核拦截（{mat_safety.reason}）"
            )
            continue

        # 构造 CreatorMaterial 对象（仅用来生成 material_id）
        from dataset.schema import CreatorMaterial as CM
        cm = CM(
            creator_id=creator_id,
            content_type=mat.material_type,
            content=mat.content,
            metadata={
                "domain": mat.domain,
                "tags":   mat.tags,
                "source": mat.source_name,
                "batch_upload": True,
            },
        )
        ok = storage.save_material(
            material_id=cm.material_id,
            creator_id=cm.creator_id,
            content_type=cm.content_type,
            content=cm.content,
            metadata=cm.metadata,
        )
        if ok:
            saved_ids.append(cm.material_id)
        else:
            fail_count += 1

    return {
        "uploaded":     len(saved_ids),
        "skipped":      result.skipped,
        "failed":       fail_count,
        "blocked":      blocked_count,
        "total_parsed": result.total,
        "material_ids": saved_ids,
        "errors":       result.errors,
        "filename":     filename,
    }


# ════════════════════════════════════════════════════════════════════
# v3 新增：监控 & 告警端点
# ════════════════════════════════════════════════════════════════════
from dataset.pipeline_monitor import PipelineMonitor

_monitor = PipelineMonitor.instance()


@dataset_router.get("/api/platform/monitor", summary="流水线监控快照")
async def platform_monitor():
    """返回最近 20 个 job 的阶段指标 + 未解决告警列表。"""
    return _monitor.get_snapshot()


@dataset_router.get("/api/platform/alerts", summary="告警列表")
async def platform_alerts(
    include_resolved: bool = Query(False),
    limit: int = Query(50, le=200),
):
    return {"alerts": _monitor.get_alerts(include_resolved, limit)}


@dataset_router.post("/api/platform/alerts/{alert_id}/resolve", summary="标记告警已解决")
async def resolve_alert(alert_id: str):
    ok = _monitor.resolve_alert(alert_id)
    if not ok:
        raise HTTPException(404, f"告警 {alert_id} 不存在")
    return {"alert_id": alert_id, "resolved": True}


# ════════════════════════════════════════════════════════════════════
# v3 新增：SQLite 账本 & 统计查询端点（P1 升级）
# ════════════════════════════════════════════════════════════════════
import store.db as _store_db


@dataset_router.get("/api/creator/balance", summary="查询创作者余额")
async def get_creator_balance(
    creator: dict = Depends(get_current_creator),
):
    """
    从 CreatorLedger（唯一真实账本）读取余额。
    v3 修正：pipeline 分润只写 CreatorLedger，此处统一对齐。
    """
    creator_id = creator["creator_id"]
    balance    = _ledger.get_balance(creator_id)          # {pending_cny, paid_cny, total_earned}
    records    = _ledger.get_creator_records(creator_id)
    return {
        "creator_id":      creator_id,
        "balance_cny":     balance.get("total_earned", 0.0),
        "pending_cny":     balance.get("pending_cny", 0.0),
        "paid_cny":        balance.get("paid_cny", 0.0),
        "revenue_summary": {
            "record_count":    len(records),
            "total_earned":    balance.get("total_earned", 0.0),
            "total_settled":   balance.get("paid_cny", 0.0),
            "pending":         balance.get("pending_cny", 0.0),
        },
    }


@dataset_router.get("/api/creator/ledger", summary="创作者账本流水")
async def get_creator_ledger(
    limit: int = Query(50, le=200),
    creator: dict = Depends(get_current_creator),
):
    """查询创作者分润流水（从 CreatorLedger 读取，与 pipeline 分润写入统一）"""
    records = _ledger.get_creator_records(creator["creator_id"])
    # 格式化为前端期望的 entries 结构
    entries = [
        {
            "entry_id":    r.get("record_id", ""),
            "creator_id":  r.get("creator_id", ""),
            "amount":      r.get("creator_share", 0.0),
            "balance_after": 0.0,   # CreatorLedger 记录不含累计余额，前端忽略
            "entry_type":  "credit",
            "reference_id": r.get("package_id", ""),
            "note":        f"数据集销售分润 · 包 {r.get('package_id','')[:8]}…",
            "created_at":  r.get("created_at", ""),
        }
        for r in records[:limit]
    ]
    return {"entries": entries, "count": len(entries)}


@dataset_router.get("/api/platform/stats", summary="平台整体统计")
async def platform_stats():
    """
    平台统计：样本/包数量从 store.db 读取；
    总收益从 CreatorLedger 读取（pipeline 分润的实际写入位置）。
    """
    db_stats = _store_db.get_platform_stats()
    all_balances = _ledger.get_all_balances()
    total_revenue = sum(b.get("total_earned", 0.0) for b in all_balances)
    creator_count = len(all_balances)
    return {
        **db_stats,
        "total_revenue":  round(total_revenue, 2),
        "creator_count":  creator_count,
    }


@dataset_router.get("/api/dataset/packages", summary="数据集包列表")
async def list_packages(limit: int = Query(20, le=100)):
    """列出最近生产的数据集包（从 SQLite 读取，持久化）"""
    return {"packages": _store_db.list_packages(limit)}


# ════════════════════════════════════════════════════════════════════
# v3 修复：SSE 实时进度推送（DatasetProductionScreen 订阅此端点）
# 原 stream_url 指向 /api/dataset/jobs（错误），现在提供真实 SSE 流
# ════════════════════════════════════════════════════════════════════
import asyncio as _asyncio
from fastapi.responses import StreamingResponse as _StreamingResponse
import json as _json_sse


@dataset_router.get(
    "/api/dataset/job/{job_id}/stream",
    summary="任务进度 SSE 流（前端 EventSource 订阅）",
)
async def job_stream(job_id: str):
    """
    Server-Sent Events：每秒推送一次任务状态，直到 done/failed。
    前端 DatasetProductionScreen 通过 EventSource 订阅。
    """
    async def _generate():
        max_polls = 600   # 最多等 10 分钟
        for _ in range(max_polls):
            job = _pipeline.get_job_status(job_id)
            if not job:
                yield f"data: {_json_sse.dumps({'error': 'job_not_found'})}\n\n"
                return

            yield f"data: {_json_sse.dumps(job)}\n\n"

            if job.get("stage") in ("done", "failed"):
                return

            await _asyncio.sleep(1)

        # 超时
        yield f"data: {_json_sse.dumps({'stage': 'failed', 'error': 'SSE timeout'})}\n\n"

    return _StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 关闭 Nginx 缓冲，保证实时推送
        },
    )


# ════════════════════════════════════════════════════════════════════
# v3 新增：版本管理端点（versioning v2 — SQLite）
# ════════════════════════════════════════════════════════════════════
from dataset.versioning import version_manager as _vm


@dataset_router.get("/api/dataset/versions", summary="列出数据集版本")
async def list_versions(
    name:  Optional[str] = Query(None, description="按数据集名过滤"),
    limit: int           = Query(50, le=200),
):
    """列出所有版本快照（SQLite 持久化，重启不丢）"""
    return {"versions": _vm.list_versions(name=name, limit=limit)}


@dataset_router.get("/api/dataset/version/{version_id}", summary="版本详情")
async def get_version(version_id: str):
    v = _vm.get_version(version_id)
    if not v:
        raise HTTPException(404, "版本不存在")
    return v


@dataset_router.get("/api/dataset/version/diff", summary="版本 Diff")
async def version_diff(
    from_id: str = Query(..., alias="from"),
    to_id:   str = Query(..., alias="to"),
):
    """对比两个版本：样本增减、质量变化"""
    return _vm.diff(from_id, to_id)

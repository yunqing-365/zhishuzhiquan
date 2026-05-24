# enterprise_api.py
"""
息壤 · 企业交付 + 版本管理 API

路由：
  /api/enterprise/*   企业客户侧（订单、令牌、下载）
  /api/versions/*     数据集版本管理（平台运营侧）
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from enterprise.delivery import (
    create_order, confirm_order, list_orders,
    issue_download_token, consume_token, stream_zip,
)
from dataset.versioning import (
    create_version, list_versions, diff_versions,
)

enterprise_router = APIRouter(prefix="/api/enterprise", tags=["Enterprise Delivery"])
versioning_router = APIRouter(prefix="/api/versions",   tags=["Dataset Versioning"])


# ════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════

class CreateOrderRequest(BaseModel):
    buyer_id:    str
    buyer_name:  str  = ""
    buyer_email: str  = ""
    package_id:  str
    price_cny:   float = Field(..., gt=0)
    note:        str  = ""

class IssueTokenRequest(BaseModel):
    order_id:  str
    ttl_hours: int = Field(24, ge=1, le=168)   # 最长7天

class CreateVersionRequest(BaseModel):
    package_id: str
    bump_type:  str  = Field("minor", pattern="^(major|minor|patch)$")
    changelog:  str
    created_by: str  = "operator"


# ════════════════════════════════════════════════════════
# 企业交付端点
# ════════════════════════════════════════════════════════

@enterprise_router.post("/orders")
async def api_create_order(req: CreateOrderRequest):
    """创建订单（平台运营在买家付款前调用）"""
    return await create_order(
        buyer_id    = req.buyer_id,
        buyer_name  = req.buyer_name,
        buyer_email = req.buyer_email,
        package_id  = req.package_id,
        price_cny   = req.price_cny,
        note        = req.note,
    )


@enterprise_router.post("/orders/{order_id}/confirm")
async def api_confirm_order(order_id: str):
    """
    确认订单已付款。
    自动触发分润计算并写入 SQLite，同时可立即签发下载令牌。
    """
    result = await confirm_order(order_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"订单 {order_id} 不存在")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@enterprise_router.get("/orders")
async def api_list_orders(buyer_id: str = None, limit: int = 50):
    return await list_orders(buyer_id=buyer_id, limit=limit)


@enterprise_router.post("/tokens")
async def api_issue_token(req: IssueTokenRequest):
    """
    为已付款订单签发下载令牌（24h 有效，单次使用）。
    返回的 token 交给买家，买家用 GET /download/{token} 下载。
    """
    result = await issue_download_token(req.order_id, req.ttl_hours)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@enterprise_router.get("/download/{token}")
async def api_download(token: str, request: Request):
    """
    买家用令牌下载数据集 ZIP（流式传输，令牌消费后立即失效）。
    首次调用锁定客户端 IP，防止令牌泄漏。
    """
    client_ip = request.client.host if request.client else ""
    result    = await consume_token(token, client_ip)

    code = result.get("code", 500)
    if code != 200:
        raise HTTPException(status_code=code, detail=result.get("error"))

    zip_path   = result["zip_path"]
    package_id = result["package_id"]

    import os
    filename = f"dataset_{package_id}.zip"
    file_size = os.path.getsize(zip_path)

    return StreamingResponse(
        stream_zip(zip_path),
        media_type   = "application/zip",
        headers      = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(file_size),
            "X-Package-Id":        package_id,
        },
    )


# ════════════════════════════════════════════════════════
# 版本管理端点
# ════════════════════════════════════════════════════════

@versioning_router.post("/create")
async def api_create_version(req: CreateVersionRequest):
    """
    基于当前所有 approved 样本创建版本快照并重新打包。
    bump_type: major / minor / patch（遵循 semver）
    """
    result = await create_version(
        package_id = req.package_id,
        bump_type  = req.bump_type,
        changelog  = req.changelog,
        created_by = req.created_by,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@versioning_router.get("/{package_id}")
async def api_list_versions(package_id: str):
    """查看某数据集的所有历史版本"""
    versions = await list_versions(package_id)
    return {"package_id": package_id, "count": len(versions), "versions": versions}


@versioning_router.get("/diff/{version_id_a}/{version_id_b}")
async def api_diff_versions(version_id_a: str, version_id_b: str):
    """对比两个版本的样本差异"""
    return await diff_versions(version_id_a, version_id_b)

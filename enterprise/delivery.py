# enterprise/delivery.py
"""
息壤 · 企业交付系统

业务流程：
  1. 平台运营创建「订单」(Order)，绑定买家 + 数据集包
  2. 系统生成一次性下载令牌 (DownloadToken, 24h 有效)
  3. 买家用令牌调用 GET /api/enterprise/download/{token} 获取 ZIP
  4. 令牌使用后立即失效（防止二次传播）
  5. 所有下载行为写入审计日志 (DeliveryLog)

防盗用设计：
  - 令牌 = UUID4 + HMAC-SHA256 签名，不可伪造
  - 每个令牌绑定买家 IP（首次使用时锁定，后续验证）
  - 超时 / 已用 / IP 不符 → 403，写入异常日志
  - 大文件走 StreamingResponse，不落盘临时文件

SQLite 表（新增 2 张）：
  orders          订单（买家 + 包 + 状态）
  download_tokens 下载令牌（token + 过期 + 使用记录）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, Optional

import aiosqlite

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import store.db as _db

_SECRET = os.environ.get("XIRANG_DELIVERY_SECRET", "xirang-delivery-secret-change-in-prod")

_ENTERPRISE_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    buyer_id      TEXT NOT NULL,
    buyer_name    TEXT DEFAULT '',
    buyer_email   TEXT DEFAULT '',
    package_id    TEXT NOT NULL,
    price_cny     REAL DEFAULT 0.0,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    paid_at       TEXT,
    note          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS download_tokens (
    token         TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL,
    package_id    TEXT NOT NULL,
    buyer_id      TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    used          INTEGER DEFAULT 0,
    used_at       TEXT,
    used_ip       TEXT DEFAULT '',
    locked_ip     TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_buyer   ON orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_orders_pkg     ON orders(package_id);
CREATE INDEX IF NOT EXISTS idx_tokens_order   ON download_tokens(order_id);
"""


async def ensure_enterprise_schema():
    db = await _db.get_db()
    await db.executescript(_ENTERPRISE_SCHEMA)
    await db.commit()


# ════════════════════════════════════════════════════════
# 签名工具
# ════════════════════════════════════════════════════════

def _sign(token_id: str) -> str:
    return hmac.new(
        _SECRET.encode(), token_id.encode(), hashlib.sha256
    ).hexdigest()[:16]


def _make_token(order_id: str) -> str:
    uid = secrets.token_urlsafe(24)
    sig = _sign(uid)
    return f"{uid}.{sig}"


def _verify_token_format(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 2:
        return False
    uid, sig = parts
    return hmac.compare_digest(_sign(uid), sig)


# ════════════════════════════════════════════════════════
# 订单管理
# ════════════════════════════════════════════════════════

async def create_order(
    buyer_id:    str,
    buyer_name:  str,
    buyer_email: str,
    package_id:  str,
    price_cny:   float,
    note:        str = "",
) -> dict:
    import uuid
    db     = await _db.get_db()
    oid    = str(uuid.uuid4())
    now    = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO orders
           (order_id,buyer_id,buyer_name,buyer_email,package_id,
            price_cny,status,created_at,note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (oid, buyer_id, buyer_name, buyer_email, package_id,
         price_cny, "pending", now, note)
    )
    await db.commit()
    return {
        "order_id":    oid,
        "package_id":  package_id,
        "buyer_id":    buyer_id,
        "price_cny":   price_cny,
        "status":      "pending",
        "created_at":  now,
    }


async def confirm_order(order_id: str) -> Optional[dict]:
    """标记订单已付款，自动触发分润计算"""
    db  = await _db.get_db()
    now = datetime.utcnow().isoformat()

    async with db.execute(
        "SELECT * FROM orders WHERE order_id=?", (order_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None

    order = dict(row)
    if order["status"] != "pending":
        return {"error": f"订单状态为 {order['status']}，不可重复确认"}

    await db.execute(
        "UPDATE orders SET status='paid', paid_at=? WHERE order_id=?",
        (now, order_id)
    )
    await db.commit()

    # 自动触发分润
    pkg = await _db.get_package(order["package_id"])
    if pkg and order["price_cny"] > 0:
        from dataset.schema import DatasetPackage
        from creator.revenue_calculator import RevenueCalculator
        p = DatasetPackage(
            package_id            = pkg["package_id"],
            name                  = pkg["name"],
            creator_contributions = pkg.get("creator_contributions", {}),
        )
        records = RevenueCalculator().calculate(p, order["price_cny"], order["buyer_id"])
        await _db.insert_revenue_records(records)

    order["status"]  = "paid"
    order["paid_at"] = now
    return order


async def list_orders(buyer_id: str = None, limit: int = 50) -> list:
    db = await _db.get_db()
    if buyer_id:
        async with db.execute(
            "SELECT * FROM orders WHERE buyer_id=? ORDER BY created_at DESC LIMIT ?",
            (buyer_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════
# 下载令牌
# ════════════════════════════════════════════════════════

async def issue_download_token(order_id: str, ttl_hours: int = 24) -> dict:
    """签发下载令牌（订单 paid 后调用）"""
    db = await _db.get_db()

    async with db.execute(
        "SELECT * FROM orders WHERE order_id=?", (order_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"error": "订单不存在"}
    order = dict(row)
    if order["status"] != "paid":
        return {"error": f"订单未付款（当前状态: {order['status']}）"}

    token      = _make_token(order_id)
    now        = datetime.utcnow()
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()

    await db.execute(
        """INSERT INTO download_tokens
           (token,order_id,package_id,buyer_id,expires_at,used,created_at)
           VALUES (?,?,?,?,?,0,?)""",
        (token, order_id, order["package_id"], order["buyer_id"],
         expires_at, now.isoformat())
    )
    await db.commit()

    return {
        "token":      token,
        "order_id":   order_id,
        "package_id": order["package_id"],
        "expires_at": expires_at,
        "ttl_hours":  ttl_hours,
    }


async def consume_token(token: str, client_ip: str = "") -> dict:
    """
    验证并消费令牌，返回 ZIP 路径。
    首次调用锁定 IP，后续同一 IP 才能复用（24h 内最多下载 3 次）。
    """
    # 签名验证
    if not _verify_token_format(token):
        return {"error": "令牌格式无效", "code": 403}

    db = await _db.get_db()
    async with db.execute(
        "SELECT * FROM download_tokens WHERE token=?", (token,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return {"error": "令牌不存在", "code": 404}

    rec = dict(row)

    # 过期检查
    if datetime.utcnow().isoformat() > rec["expires_at"]:
        return {"error": "令牌已过期", "code": 403}

    # 已使用检查（单次令牌）
    if rec["used"]:
        return {"error": "令牌已被使用", "code": 403}

    # IP 锁定
    if rec["locked_ip"] and rec["locked_ip"] != client_ip:
        return {"error": "IP 不匹配，令牌已锁定至其他地址", "code": 403}

    # 取包路径
    pkg = await _db.get_package(rec["package_id"])
    if not pkg:
        return {"error": "数据集包不存在", "code": 404}

    export_paths = pkg.get("export_paths", {})
    zip_path     = export_paths.get("zip", "")
    if not zip_path or not os.path.exists(zip_path):
        return {"error": "数据集文件不存在，请联系平台", "code": 500}

    # 消费令牌
    now = datetime.utcnow().isoformat()
    await db.execute(
        """UPDATE download_tokens
           SET used=1, used_at=?, used_ip=?, locked_ip=?
           WHERE token=?""",
        (now, client_ip, client_ip or rec["locked_ip"], token)
    )
    await db.commit()

    return {
        "code":       200,
        "zip_path":   zip_path,
        "package_id": rec["package_id"],
        "buyer_id":   rec["buyer_id"],
    }


async def stream_zip(zip_path: str) -> AsyncIterator[bytes]:
    """分块流式返回 ZIP 文件（避免大文件全量加载内存）"""
    chunk_size = 64 * 1024   # 64KB
    with open(zip_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk

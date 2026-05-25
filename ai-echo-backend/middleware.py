"""
middleware.py — 安全中间件
===========================
架构升级 v6 — 生产安全层

解决的核心问题:
  - oracle_engine.py 的 CORS 在未配置时允许所有来源 (*)
  - /api/valuate 无限流保护，单一 IP 可无限刷估值耗尽算力
  - 无请求日志，生产环境完全黑盒

Stage 2 新增:
  - ApiKeyMiddleware: Bearer Token 认证，保护 B2B 接口
    配置方式: 环境变量 AI_ECHO_API_KEY（逗号分隔多 key）
    受保护路由: /api/batch_valuate, /ws/valuate（WebSocket 握手）
    无 key 配置时自动旁路（开发模式）

提供:
  - RateLimiter: 基于内存的令牌桶限流（无 Redis 依赖，单进程适用）
  - ApiKeyMiddleware: Bearer Token / X-API-Key 认证（Stage 2 新增）
  - setup_security: 一键挂载所有中间件到 FastAPI app
  - RequestLogger: 结构化请求日志（耗时 + 状态码 + 路由）

使用方式 (在 oracle_engine.py 中):
    from middleware import setup_security
    setup_security(app)
"""

import os
import time
import logging
from collections import defaultdict
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("ai-echo")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── 令牌桶限流器（纯内存，无外部依赖）────────────────────────────────
class TokenBucket:
    """
    每个 key 独立维护令牌桶。
    capacity: 桶容量（突发上限）
    refill_rate: 每秒补充令牌数
    """
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity    = capacity
        self.refill_rate = refill_rate
        self._buckets: dict[str, dict] = defaultdict(lambda: {
            "tokens": float(capacity),
            "last":   time.monotonic(),
        })

    def consume(self, key: str, tokens: int = 1) -> bool:
        """尝试消耗 tokens 个令牌。返回 True = 允许，False = 限流"""
        now    = time.monotonic()
        bucket = self._buckets[key]
        elapsed       = now - bucket["last"]
        bucket["last"] = now
        # 补充令牌（不超过容量上限）
        bucket["tokens"] = min(
            self.capacity,
            bucket["tokens"] + elapsed * self.refill_rate
        )
        if bucket["tokens"] >= tokens:
            bucket["tokens"] -= tokens
            return True
        return False

    def cleanup(self, max_age_secs: float = 3600.0):
        """清理超过 max_age_secs 未活跃的桶，防止内存泄漏"""
        now = time.monotonic()
        stale = [k for k, v in self._buckets.items() if now - v["last"] > max_age_secs]
        for k in stale:
            del self._buckets[k]


# ── 路由级限流配置 ────────────────────────────────────────────────────
# path_prefix -> (capacity, refill_per_second)
# /api/valuate:              每分钟 20 次
# /api/history:              每分钟 60 次
# /api/dataset/ingest:       每分钟 30 次（单条上传）
# /api/dataset/batch_ingest: 每分钟 5 次（批量上传，重操作）
# /api/dataset/produce:      每分钟 10 次（生产任务）
# /api/dataset/sell:         每分钟 20 次（购买）
# /api/auth/login:           每分钟 10 次（防爆破）
# /api/auth/register:        每分钟 5 次（防批量注册）
ROUTE_LIMITS: dict[str, tuple[int, float]] = {
    "/api/valuate":               (20,  20  / 60),
    "/api/history":               (60,  60  / 60),
    "/api/dataset/ingest":        (30,  30  / 60),
    "/api/dataset/batch_ingest":  (5,   5   / 60),
    "/api/dataset/produce":       (10,  10  / 60),
    "/api/dataset/sell":          (20,  20  / 60),
    "/api/auth/login":            (10,  10  / 60),
    "/api/auth/register":         (5,   5   / 60),
}

_buckets: dict[str, TokenBucket] = {
    path: TokenBucket(cap, rate)
    for path, (cap, rate) in ROUTE_LIMITS.items()
}


def _client_key(request: Request) -> str:
    """提取客户端唯一标识（优先取真实 IP）"""
    # X-Forwarded-For: Nginx/Cloudflare 反向代理场景
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── 限流中间件 ────────────────────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path   = request.url.path
        bucket = _buckets.get(path)
        if bucket:
            key = _client_key(request)
            if not bucket.consume(key):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": f"请求过于频繁，请稍后再试。{path} 限制每分钟 {ROUTE_LIMITS[path][0]} 次。",
                        "retry_after": 60,
                    },
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)


# ── 请求日志中间件 ────────────────────────────────────────────────────
class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start   = time.monotonic()
        method  = request.method
        path    = request.url.path
        client  = _client_key(request)
        response: Response = await call_next(request)
        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s %d %.1fms [%s]",
            method, path, response.status_code, elapsed, client,
        )
        return response


# ── 一键挂载所有安全中间件 ────────────────────────────────────────────


# ── API Key 认证中间件（Stage 2: 保护 B2B 接口）──────────────────────
# 受保护路由: /api/batch_valuate, /ws/valuate
# 认证方式 (任一即可):
#   Header: Authorization: Bearer <key>
#   Header: X-API-Key: <key>
#   Query:  ?api_key=<key>
# 配置: 环境变量 AI_ECHO_API_KEY，逗号分隔多个 key
#   export AI_ECHO_API_KEY="key_prod_abc123,key_test_xyz789"
# 无配置时自动开放（开发模式），并打印警告。

_PROTECTED_ROUTES = frozenset([
    "/api/batch_valuate",
    "/ws/valuate",
])


def _load_api_keys() -> frozenset:
    """从环境变量加载允许的 API Key 集合。空集合 = 开放模式。"""
    raw = os.environ.get("AI_ECHO_API_KEY", "").strip()
    if not raw:
        return frozenset()
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip())
    return keys


_API_KEYS: frozenset = _load_api_keys()
if not _API_KEYS:
    logger.warning(
        ">> [security] AI_ECHO_API_KEY 未配置，/api/batch_valuate 和 /ws/valuate 开放访问（开发模式）"
    )
else:
    logger.info(">> [security] API Key 认证已启用，保护路由: %s", ", ".join(_PROTECTED_ROUTES))


def _extract_api_key(request: Request) -> Optional[str]:
    """按优先级提取请求中携带的 API Key。"""
    # 1. Authorization: Bearer <key>
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 2. X-API-Key: <key>
    x_key = request.headers.get("X-API-Key", "").strip()
    if x_key:
        return x_key
    # 3. ?api_key=<key>  (WebSocket 握手常用)
    q_key = request.query_params.get("api_key", "").strip()
    if q_key:
        return q_key
    return None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Bearer Token / X-API-Key 认证中间件。
    仅拦截 _PROTECTED_ROUTES 中的路径，其余路由直接放行。
    _API_KEYS 为空时（开发模式）一律放行。
    """
    async def dispatch(self, request: Request, call_next):
        if not _API_KEYS:
            return await call_next(request)

        path = request.url.path
        if path not in _PROTECTED_ROUTES:
            return await call_next(request)

        key = _extract_api_key(request)
        if key and key in _API_KEYS:
            return await call_next(request)

        logger.warning(
            "[security] API Key 验证失败: %s %s [%s]",
            request.method, path, _client_key(request),
        )
        return JSONResponse(
            status_code=401,
            content={
                "error":   "unauthorized",
                "message": "此接口需要有效的 API Key。请在 Authorization: Bearer <key> 或 X-API-Key: <key> 中提供。",
                "docs":    "https://docs.ai-echo.io/authentication",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def setup_security(app) -> None:
    """
    在 oracle_engine.py 的 FastAPI app 上挂载安全层。
    调用时机: CORS 中间件之后，路由注册之前。

    使用方式:
        from middleware import setup_security
        setup_security(app)   # 加在 CORS add_middleware 之后即可
    """
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggerMiddleware)
    # ★ Stage 2: API Key 认证（挂载顺序: 先认证，后限流）
    app.add_middleware(ApiKeyMiddleware)
    logger.info(">> [security] 限流 + 请求日志 + API Key 认证中间件已挂载")
    for path, (cap, rate) in ROUTE_LIMITS.items():
        logger.info("   %s: burst=%d, refill=%.2f/s", path, cap, rate)

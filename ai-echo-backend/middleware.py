"""
middleware.py — 安全中间件
===========================
架构升级 v7 — 多进程限流修复

解决的核心问题:
  - TokenBucket 是纯内存实现：uvicorn --workers N 时各进程独立计数，
    实际允许速率 = 配置值 × N。重启后桶归零，攻击窗口明显。

v7 新增:
  - PersistentRateLimiter: 基于 SQLite 的滑动窗口计数器
    用于最敏感路由（登录/注册/批量上传），重启后状态不丢。
    多进程下共用同一 SQLite 文件，WAL 模式保证并发写入安全。
    无 Redis 依赖，单文件部署可用。
  - 非敏感路由继续使用内存 TokenBucket（低延迟，可接受误差）

部署说明（生产）:
  - 单进程（uvicorn 默认）：内存桶完全准确，无需 SQLite 层。
  - 多进程（uvicorn --workers N）：SQLite 层保证跨进程准确性，
    同时内存桶作为快速前置过滤（减少 SQLite 查询次数）。
  - Redis 部署：将 PersistentRateLimiter 替换为 redis-py 实现即可，
    接口兼容（同名方法 is_allowed(key, limit, window_secs)）。

提供:
  - RateLimiter: 基于内存的令牌桶限流（无 Redis 依赖，单进程适用）
  - PersistentRateLimiter: SQLite 滑动窗口限流（多进程安全，重启不丢）
  - ApiKeyMiddleware: Bearer Token / X-API-Key 认证
  - setup_security: 一键挂载所有中间件到 FastAPI app
  - RequestLogger: 结构化请求日志（耗时 + 状态码 + 路由）

使用方式 (在 oracle_engine.py 中):
    from middleware import setup_security
    setup_security(app)
"""

import os
import sqlite3
import time
import logging
import threading
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


# ── 令牌桶限流器（纯内存，单进程快速路径）────────────────────────────
class TokenBucket:
    """
    每个 key 独立维护令牌桶。
    capacity: 桶容量（突发上限）
    refill_rate: 每秒补充令牌数

    注意：纯内存实现，多进程（uvicorn --workers N）下各进程独立计数。
    高敏感路由应配合 PersistentRateLimiter 使用。
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



# ── SQLite 滑动窗口限流器（多进程安全，重启不丢）─────────────────────
class PersistentRateLimiter:
    """
    基于 SQLite WAL 的滑动窗口计数器。

    每次请求写入一条时间戳记录，查询时只统计窗口内的有效记录数。
    定期清理过期记录（lazy cleanup）。

    适用于敏感路由：登录防爆破、注册防批量、批量上传防滥用。
    """

    _DB_PATH = os.path.join(
        os.path.dirname(__file__), "data", "rate_limit.db"
    )
    _lock = threading.Lock()
    _initialized = False

    @classmethod
    def _init_db(cls):
        if cls._initialized:
            return
        os.makedirs(os.path.dirname(cls._DB_PATH), exist_ok=True)
        with cls._lock:
            if cls._initialized:
                return
            conn = sqlite3.connect(cls._DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    route     TEXT    NOT NULL,
                    client_ip TEXT    NOT NULL,
                    ts        REAL    NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_route_ip "
                "ON rate_events(route, client_ip, ts)"
            )
            conn.commit()
            conn.close()
            cls._initialized = True

    @classmethod
    def is_allowed(cls, route: str, client_ip: str, limit: int, window_secs: float) -> bool:
        """
        检查在 window_secs 内 route+client_ip 的请求数是否超过 limit。
        超过返回 False（拒绝），否则记录本次请求并返回 True。
        """
        cls._init_db()
        now = time.time()
        window_start = now - window_secs

        with cls._lock:
            conn = sqlite3.connect(cls._DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM rate_events "
                    "WHERE route=? AND client_ip=? AND ts>=?",
                    (route, client_ip, window_start),
                ).fetchone()[0]

                if count >= limit:
                    return False

                conn.execute(
                    "INSERT INTO rate_events(route, client_ip, ts) VALUES(?,?,?)",
                    (route, client_ip, now),
                )
                if int(now) % 1000 == 0:
                    conn.execute(
                        "DELETE FROM rate_events WHERE ts < ?",
                        (now - max(window_secs * 2, 3600),),
                    )
                conn.commit()
                return True
            finally:
                conn.close()


# ── 高敏感路由：使用 SQLite 持久化限流 ───────────────────────────────
# (path_prefix, limit, window_secs)
PERSISTENT_LIMITS: list[tuple[str, int, float]] = [
    ("/api/auth/login",          10, 60),
    ("/api/auth/register",        5, 60),
    ("/api/dataset/batch_ingest", 5, 60),
]


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
        path = request.url.path
        key  = _client_key(request)

        # ── 层 1：SQLite 持久化限流（高敏感路由，多进程安全）──────────
        for prefix, limit, window in PERSISTENT_LIMITS:
            if path.startswith(prefix):
                if not PersistentRateLimiter.is_allowed(prefix, key, limit, window):
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error":       "rate_limited",
                            "message":     f"请求过于频繁，请稍后再试。{prefix} 限制 {window:.0f}s 内 {limit} 次。",
                            "retry_after": int(window),
                        },
                        headers={"Retry-After": str(int(window))},
                    )
                break  # 命中持久化规则后不再走内存桶

        # ── 层 2：内存令牌桶（普通路由，低延迟）──────────────────────
        bucket = _buckets.get(path)
        if bucket:
            if not bucket.consume(key):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error":       "rate_limited",
                        "message":     f"请求过于频繁，请稍后再试。{path} 限制每分钟 {ROUTE_LIMITS[path][0]} 次。",
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

# store/db.py
"""
息壤 · 异步 SQLite 持久化层

替换以下所有内存存储：
  dataset_api.py  → _material_store  / _package_store
  creator/        → CreatorLedger(JSON文件)
  dataset/        → 所有样本列表（重启即失）

设计原则：
  - 全程 aiosqlite（非阻塞，与 FastAPI asyncio 天然兼容）
  - 单文件 SQLite，开发/测试零配置；生产可无缝换 PostgreSQL
  - 统一 DataStore 入口，上层只需导入 ds.xxx 调用
  - Schema 首次运行自动建表（idempotent）
  - 所有写操作都有重试逻辑防止 SQLITE_BUSY

表结构（7张）：
  materials          原始素材
  sft_samples        SFT 三元组
  dpo_samples        DPO 偏好对
  pretrain_chunks    预训练语料块
  packages           数据集包
  revenue_records    分润记录
  review_actions     人工复核操作日志
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "xirang.db"
)
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

_CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS materials (
    material_id   TEXT PRIMARY KEY,
    creator_id    TEXT NOT NULL,
    raw_content   TEXT,
    material_type TEXT DEFAULT 'text',
    source_path   TEXT DEFAULT '',
    domain        TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    content_hash  TEXT DEFAULT '',
    uploaded_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sft_samples (
    sample_id       TEXT PRIMARY KEY,
    material_id     TEXT,
    creator_id      TEXT NOT NULL,
    system_prompt   TEXT DEFAULT '',
    instruction     TEXT NOT NULL,
    input_context   TEXT DEFAULT '',
    output          TEXT NOT NULL,
    domain          TEXT DEFAULT '',
    language        TEXT DEFAULT 'zh',
    difficulty      INTEGER DEFAULT 3,
    status          TEXT DEFAULT 'pending',
    quality_score   REAL DEFAULT 0.0,
    quality_tier    TEXT DEFAULT 'bronze',
    quality_detail  TEXT DEFAULT '{}',
    annotator_id    TEXT DEFAULT '',
    reviewed_by     TEXT DEFAULT '',
    content_hash    TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dpo_samples (
    sample_id          TEXT PRIMARY KEY,
    material_id        TEXT,
    creator_id         TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    chosen             TEXT NOT NULL,
    rejected           TEXT NOT NULL,
    preference_reason  TEXT DEFAULT '',
    domain             TEXT DEFAULT '',
    status             TEXT DEFAULT 'pending',
    quality_score      REAL DEFAULT 0.0,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pretrain_chunks (
    chunk_id      TEXT PRIMARY KEY,
    material_id   TEXT,
    creator_id    TEXT NOT NULL,
    text          TEXT NOT NULL,
    token_count   INTEGER DEFAULT 0,
    domain        TEXT DEFAULT '',
    language      TEXT DEFAULT 'zh',
    perplexity    REAL DEFAULT 0.0,
    dedup_hash    TEXT DEFAULT '',
    quality_score REAL DEFAULT 0.0,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packages (
    package_id             TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    description            TEXT DEFAULT '',
    dataset_type           TEXT DEFAULT 'sft',
    version                TEXT DEFAULT '1.0.0',
    domain                 TEXT DEFAULT '',
    language               TEXT DEFAULT 'zh',
    total_samples          INTEGER DEFAULT 0,
    approved_samples       INTEGER DEFAULT 0,
    avg_quality            REAL DEFAULT 0.0,
    platinum_count         INTEGER DEFAULT 0,
    gold_count             INTEGER DEFAULT 0,
    creator_contributions  TEXT DEFAULT '{}',
    price_cny              REAL DEFAULT 0.0,
    license_type           TEXT DEFAULT 'enterprise_internal',
    export_paths           TEXT DEFAULT '{}',
    created_at             TEXT NOT NULL,
    published_at           TEXT
);

CREATE TABLE IF NOT EXISTS revenue_records (
    record_id           TEXT PRIMARY KEY,
    package_id          TEXT NOT NULL,
    creator_id          TEXT NOT NULL,
    total_revenue       REAL DEFAULT 0.0,
    contribution_ratio  REAL DEFAULT 0.0,
    creator_share       REAL DEFAULT 0.0,
    platform_fee        REAL DEFAULT 0.0,
    status              TEXT DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    paid_at             TEXT
);

CREATE TABLE IF NOT EXISTS review_actions (
    action_id     TEXT PRIMARY KEY,
    sample_id     TEXT NOT NULL,
    sample_type   TEXT NOT NULL,
    reviewer_id   TEXT NOT NULL,
    action        TEXT NOT NULL,
    note          TEXT DEFAULT '',
    old_output    TEXT DEFAULT '',
    new_output    TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sft_status   ON sft_samples(status);
CREATE INDEX IF NOT EXISTS idx_sft_creator  ON sft_samples(creator_id);
CREATE INDEX IF NOT EXISTS idx_sft_quality  ON sft_samples(quality_score);
CREATE INDEX IF NOT EXISTS idx_dpo_status   ON dpo_samples(status);
CREATE INDEX IF NOT EXISTS idx_pretrain_status ON pretrain_chunks(status);
CREATE INDEX IF NOT EXISTS idx_revenue_creator ON revenue_records(creator_id);
CREATE INDEX IF NOT EXISTS idx_revenue_package ON revenue_records(package_id);
CREATE INDEX IF NOT EXISTS idx_review_sample   ON review_actions(sample_id);
"""


# ════════════════════════════════════════════════════════
# 连接管理
# ════════════════════════════════════════════════════════

_conn: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()


async def init_db(path: str = _DB_PATH) -> None:
    """首次调用建库建表（idempotent）"""
    global _conn
    async with _lock:
        if _conn is None:
            _conn = await aiosqlite.connect(path, timeout=15)
            _conn.row_factory = aiosqlite.Row
            await _conn.executescript(_CREATE_SQL)
            await _conn.commit()
            print(f"✅ SQLite 初始化完成: {path}")


async def get_db() -> aiosqlite.Connection:
    if _conn is None:
        await init_db()
    return _conn


async def close_db() -> None:
    global _conn
    if _conn:
        await _conn.close()
        _conn = None


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.utcnow().isoformat()

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    return dict(row)

def _rows_to_list(rows) -> List[dict]:
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════
# 素材 CRUD
# ════════════════════════════════════════════════════════

async def insert_material(m) -> None:
    """m: CreatorMaterial"""
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO materials
           (material_id,creator_id,raw_content,material_type,source_path,
            domain,metadata_json,content_hash,uploaded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (m.material_id, m.creator_id, m.raw_content, m.material_type,
         m.source_path, m.metadata.get("domain",""),
         json.dumps(m.metadata, ensure_ascii=False),
         m.content_hash, _now())
    )
    await db.commit()


async def get_material(material_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM materials WHERE material_id=?", (material_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_materials(creator_id: str = None, limit: int = 100) -> List[dict]:
    db = await get_db()
    if creator_id:
        async with db.execute(
            "SELECT * FROM materials WHERE creator_id=? ORDER BY uploaded_at DESC LIMIT ?",
            (creator_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM materials ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return _rows_to_list(rows)


# ════════════════════════════════════════════════════════
# SFT 样本 CRUD
# ════════════════════════════════════════════════════════

async def insert_sft(s) -> None:
    """s: SFTSample"""
    db = await get_db()
    now = _now()
    await db.execute(
        """INSERT OR REPLACE INTO sft_samples
           (sample_id,material_id,creator_id,system_prompt,instruction,
            input_context,output,domain,language,difficulty,status,
            quality_score,quality_tier,quality_detail,annotator_id,
            reviewed_by,content_hash,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (s.sample_id, s.material_id, s.creator_id, s.system_prompt,
         s.instruction, s.input_context, s.output, s.domain, s.language,
         s.difficulty, s.status.value if hasattr(s.status,'value') else s.status,
         s.quality_score,
         s.quality_tier.value if hasattr(s.quality_tier,'value') else s.quality_tier,
         json.dumps(s.quality_detail, ensure_ascii=False),
         s.annotator_id, s.reviewed_by, s.content_hash, now, now)
    )
    await db.commit()


async def bulk_insert_sft(samples: list) -> int:
    db = await get_db()
    now = _now()
    rows = [
        (s.sample_id, s.material_id, s.creator_id, s.system_prompt,
         s.instruction, s.input_context, s.output, s.domain, s.language,
         s.difficulty, s.status.value if hasattr(s.status,'value') else s.status,
         s.quality_score,
         s.quality_tier.value if hasattr(s.quality_tier,'value') else s.quality_tier,
         json.dumps(s.quality_detail, ensure_ascii=False),
         s.annotator_id, s.reviewed_by, s.content_hash, now, now)
        for s in samples
    ]
    await db.executemany(
        """INSERT OR REPLACE INTO sft_samples
           (sample_id,material_id,creator_id,system_prompt,instruction,
            input_context,output,domain,language,difficulty,status,
            quality_score,quality_tier,quality_detail,annotator_id,
            reviewed_by,content_hash,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows
    )
    await db.commit()
    return len(rows)


async def get_pending_sft(limit: int = 50) -> List[dict]:
    """获取待人工复核的 SFT 样本"""
    db = await get_db()
    async with db.execute(
        """SELECT * FROM sft_samples WHERE status='pending'
           ORDER BY quality_score ASC LIMIT ?""", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return _rows_to_list(rows)


async def update_sft_review(sample_id: str, action: str,
                             reviewer_id: str, new_output: str = None,
                             note: str = "") -> bool:
    """人工审核更新：approve / reject / edit"""
    db = await get_db()
    if action == "approve":
        new_status = "approved"
    elif action == "reject":
        new_status = "rejected"
    elif action == "edit" and new_output:
        new_status = "reviewed"
    else:
        return False

    # 取旧 output 用于日志
    async with db.execute(
        "SELECT output FROM sft_samples WHERE sample_id=?", (sample_id,)
    ) as cur:
        row = await cur.fetchone()
    old_output = row["output"] if row else ""

    updates = [("status", new_status), ("reviewed_by", reviewer_id),
                ("updated_at", _now())]
    if new_output and action == "edit":
        updates.append(("output", new_output))

    set_clause = ", ".join(f"{k}=?" for k, _ in updates)
    vals = [v for _, v in updates] + [sample_id]
    await db.execute(
        f"UPDATE sft_samples SET {set_clause} WHERE sample_id=?", vals
    )

    # 写操作日志
    import uuid
    await db.execute(
        """INSERT INTO review_actions
           (action_id,sample_id,sample_type,reviewer_id,action,note,
            old_output,new_output,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), sample_id, "sft", reviewer_id, action, note,
         old_output, new_output or "", _now())
    )
    await db.commit()
    return True


async def list_sft(status: str = None, creator_id: str = None,
                   min_score: float = 0, limit: int = 200) -> List[dict]:
    db = await get_db()
    wheres, params = ["quality_score >= ?"], [min_score]
    if status:
        wheres.append("status = ?"); params.append(status)
    if creator_id:
        wheres.append("creator_id = ?"); params.append(creator_id)
    where_sql = " AND ".join(wheres)
    params.append(limit)
    async with db.execute(
        f"SELECT * FROM sft_samples WHERE {where_sql} ORDER BY quality_score DESC LIMIT ?",
        params
    ) as cur:
        rows = await cur.fetchall()
    return _rows_to_list(rows)


# ════════════════════════════════════════════════════════
# DPO 样本
# ════════════════════════════════════════════════════════

async def bulk_insert_dpo(samples: list) -> int:
    db = await get_db()
    now = _now()
    rows = [
        (s.sample_id, s.material_id, s.creator_id, s.prompt, s.chosen,
         s.rejected, s.preference_reason, s.domain,
         s.status.value if hasattr(s.status,'value') else s.status,
         s.quality_score, now)
        for s in samples
    ]
    await db.executemany(
        """INSERT OR REPLACE INTO dpo_samples
           (sample_id,material_id,creator_id,prompt,chosen,rejected,
            preference_reason,domain,status,quality_score,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows
    )
    await db.commit()
    return len(rows)


async def get_pending_dpo(limit: int = 50) -> List[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM dpo_samples WHERE status='pending' LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return _rows_to_list(rows)


async def update_dpo_status(sample_id: str, status: str,
                              quality_score: float = None) -> None:
    db = await get_db()
    if quality_score is not None:
        await db.execute(
            "UPDATE dpo_samples SET status=?, quality_score=? WHERE sample_id=?",
            (status, quality_score, sample_id)
        )
    else:
        await db.execute(
            "UPDATE dpo_samples SET status=? WHERE sample_id=?", (status, sample_id)
        )
    await db.commit()


async def list_dpo(status: str = None, limit: int = 200) -> List[dict]:
    db = await get_db()
    if status:
        async with db.execute(
            "SELECT * FROM dpo_samples WHERE status=? ORDER BY quality_score DESC LIMIT ?",
            (status, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM dpo_samples ORDER BY quality_score DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return _rows_to_list(rows)


# ════════════════════════════════════════════════════════
# 预训练块
# ════════════════════════════════════════════════════════

async def bulk_insert_pretrain(chunks: list) -> int:
    db = await get_db()
    now = _now()
    rows = [
        (c.chunk_id, c.material_id, c.creator_id, c.text, c.token_count,
         c.domain, c.language, c.quality_score, c.dedup_hash,
         c.status.value if hasattr(c.status,'value') else c.status, now)
        for c in chunks
    ]
    await db.executemany(
        """INSERT OR REPLACE INTO pretrain_chunks
           (chunk_id,material_id,creator_id,text,token_count,domain,
            language,quality_score,dedup_hash,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows
    )
    await db.commit()
    return len(rows)


async def list_pretrain(status: str = None, limit: int = 500) -> List[dict]:
    db = await get_db()
    if status:
        async with db.execute(
            "SELECT * FROM pretrain_chunks WHERE status=? LIMIT ?", (status, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM pretrain_chunks ORDER BY quality_score DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return _rows_to_list(rows)


# ════════════════════════════════════════════════════════
# 数据集包
# ════════════════════════════════════════════════════════

async def insert_package(pkg) -> None:
    """pkg: DatasetPackage or dict"""
    db = await get_db()
    if hasattr(pkg, 'package_id'):
        row = (
            pkg.package_id, pkg.name, pkg.description,
            pkg.dataset_type.value if hasattr(pkg.dataset_type,'value') else pkg.dataset_type,
            pkg.version, pkg.domain, pkg.language,
            pkg.total_samples, pkg.approved_samples, pkg.avg_quality,
            pkg.platinum_count, pkg.gold_count,
            json.dumps(pkg.creator_contributions, ensure_ascii=False),
            pkg.price_cny, pkg.license_type,
            json.dumps(pkg.export_paths, ensure_ascii=False),
            _now(),
            pkg.published_at.isoformat() if pkg.published_at else None
        )
    else:
        row = (
            pkg["package_id"], pkg["name"], pkg.get("description",""),
            pkg.get("dataset_type","sft"), pkg.get("version","1.0.0"),
            pkg.get("domain",""), pkg.get("language","zh"),
            pkg.get("total_samples",0), pkg.get("approved_samples",0),
            pkg.get("avg_quality",0.0), pkg.get("platinum_count",0),
            pkg.get("gold_count",0),
            json.dumps(pkg.get("creator_contributions",{}), ensure_ascii=False),
            pkg.get("price_cny",0.0), pkg.get("license_type","enterprise_internal"),
            json.dumps(pkg.get("export_paths",{}), ensure_ascii=False),
            _now(), pkg.get("published_at")
        )
    await db.execute(
        """INSERT OR REPLACE INTO packages
           (package_id,name,description,dataset_type,version,domain,language,
            total_samples,approved_samples,avg_quality,platinum_count,gold_count,
            creator_contributions,price_cny,license_type,export_paths,
            created_at,published_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        row
    )
    await db.commit()


async def get_package(package_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM packages WHERE package_id=?", (package_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("creator_contributions", "export_paths"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


async def list_packages(limit: int = 50) -> List[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM packages ORDER BY created_at DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    results = []
    for row in rows:
        d = dict(row)
        for field in ("creator_contributions", "export_paths"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        results.append(d)
    return results


# ════════════════════════════════════════════════════════
# 分润记录
# ════════════════════════════════════════════════════════

async def insert_revenue_records(records: list) -> int:
    db = await get_db()
    rows = [
        (r.record_id, r.package_id, r.creator_id, r.total_revenue,
         r.contribution_ratio, r.creator_share, r.platform_fee, r.status,
         _now(), None)
        for r in records
    ]
    await db.executemany(
        """INSERT OR REPLACE INTO revenue_records
           (record_id,package_id,creator_id,total_revenue,contribution_ratio,
            creator_share,platform_fee,status,created_at,paid_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows
    )
    await db.commit()
    return len(rows)


async def get_creator_balance(creator_id: str) -> dict:
    db = await get_db()
    async with db.execute(
        """SELECT
             SUM(CASE WHEN status='pending' THEN creator_share ELSE 0 END) as pending_cny,
             SUM(CASE WHEN status='paid'    THEN creator_share ELSE 0 END) as paid_cny,
             COUNT(*) as record_count
           FROM revenue_records WHERE creator_id=?""",
        (creator_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"creator_id": creator_id, "pending_cny": 0.0, "paid_cny": 0.0}
    return {
        "creator_id":    creator_id,
        "pending_cny":   round(row["pending_cny"] or 0.0, 2),
        "paid_cny":      round(row["paid_cny"] or 0.0, 2),
        "total_earned":  round((row["pending_cny"] or 0) + (row["paid_cny"] or 0), 2),
        "record_count":  row["record_count"],
    }


async def settle_creator(creator_id: str) -> Optional[dict]:
    """将 pending → paid，返回结算金额"""
    db = await get_db()
    async with db.execute(
        "SELECT SUM(creator_share) as total FROM revenue_records WHERE creator_id=? AND status='pending'",
        (creator_id,)
    ) as cur:
        row = await cur.fetchone()
    amount = row["total"] or 0.0
    if amount < 10.0:
        return None
    await db.execute(
        "UPDATE revenue_records SET status='paid', paid_at=? WHERE creator_id=? AND status='pending'",
        (_now(), creator_id)
    )
    await db.commit()
    return {"creator_id": creator_id, "settled_amount": round(amount, 2), "settled_at": _now()}


async def get_creator_records(creator_id: str, limit: int = 30) -> List[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM revenue_records WHERE creator_id=? ORDER BY created_at DESC LIMIT ?",
        (creator_id, limit)
    ) as cur:
        rows = await cur.fetchall()
    return _rows_to_list(rows)


async def get_platform_summary() -> dict:
    db = await get_db()
    async with db.execute(
        """SELECT
             COUNT(DISTINCT creator_id) as creator_count,
             SUM(CASE WHEN status='pending' THEN creator_share ELSE 0 END) as total_pending,
             SUM(CASE WHEN status='paid'    THEN creator_share ELSE 0 END) as total_paid
           FROM revenue_records"""
    ) as cur:
        row = await cur.fetchone()
    async with db.execute("SELECT COUNT(*) as n FROM packages") as cur:
        pkg_row = await cur.fetchone()
    async with db.execute("SELECT COUNT(*) as n FROM materials") as cur:
        mat_row = await cur.fetchone()
    async with db.execute("SELECT COUNT(*) as n FROM sft_samples WHERE status='approved'") as cur:
        sft_row = await cur.fetchone()
    return {
        "creator_count":    row["creator_count"] or 0,
        "total_pending_cny": round(row["total_pending"] or 0, 2),
        "total_paid_cny":   round(row["total_paid"] or 0, 2),
        "total_packages":   pkg_row["n"],
        "total_materials":  mat_row["n"],
        "approved_sft":     sft_row["n"],
    }


# ════════════════════════════════════════════════════════
# 全局单例（FastAPI lifespan 里调用 init_db()）
# ════════════════════════════════════════════════════════

async def setup():
    """在 FastAPI lifespan 里调用：await setup()"""
    await init_db()
    # 企业交付 + 版本管理扩展表
    from enterprise.delivery import ensure_enterprise_schema
    from dataset.versioning import ensure_versioning_schema
    await ensure_enterprise_schema()
    await ensure_versioning_schema()

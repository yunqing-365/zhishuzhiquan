# store/db.py
"""
知数知圈 · SQLite 持久层 v1

P1 升级：账本 & 样本全部迁移至 SQLite。
  - SFT / DPO / Pretrain 样本持久化（重启不丢）
  - 创作者收益账本（CreatorLedger）迁移至 SQLite，替代 JSON 文件
  - 线程安全写入（asyncio + threading.Lock 双保险）

数据库文件：ai-echo-backend/data/zszq.db
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH   = os.path.join(_DATA_DIR, "zszq.db")

_lock = threading.Lock()


# ════════════════════════════════════════════════════════════════════
# 初始化 & 迁移
# ════════════════════════════════════════════════════════════════════

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 写前日志，提高并发安全
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """建表（幂等）"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sft_samples (
                sample_id      TEXT PRIMARY KEY,
                material_id    TEXT NOT NULL,
                creator_id     TEXT NOT NULL,
                package_id     TEXT DEFAULT '',
                instruction    TEXT NOT NULL,
                input          TEXT DEFAULT '',
                output         TEXT NOT NULL,
                quality_score  REAL DEFAULT 0.0,
                quality_tier   TEXT DEFAULT 'silver',
                domain         TEXT DEFAULT '',
                language       TEXT DEFAULT 'zh',
                token_count    INTEGER DEFAULT 0,
                annotation_meta TEXT DEFAULT '{}',
                created_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS dpo_samples (
                sample_id      TEXT PRIMARY KEY,
                material_id    TEXT NOT NULL,
                creator_id     TEXT NOT NULL,
                package_id     TEXT DEFAULT '',
                prompt         TEXT NOT NULL,
                chosen         TEXT NOT NULL,
                rejected       TEXT NOT NULL,
                quality_score  REAL DEFAULT 0.0,
                quality_tier   TEXT DEFAULT 'silver',
                domain         TEXT DEFAULT '',
                language       TEXT DEFAULT 'zh',
                token_count    INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pretrain_chunks (
                chunk_id       TEXT PRIMARY KEY,
                material_id    TEXT NOT NULL,
                creator_id     TEXT NOT NULL,
                text           TEXT NOT NULL,
                quality_score  REAL DEFAULT 0.0,
                domain         TEXT DEFAULT '',
                language       TEXT DEFAULT 'zh',
                token_count    INTEGER DEFAULT 0,
                source_url     TEXT DEFAULT '',
                created_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS dataset_packages (
                package_id     TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT DEFAULT '',
                version        TEXT DEFAULT '1.0.0',
                total_samples  INTEGER DEFAULT 0,
                sft_count      INTEGER DEFAULT 0,
                dpo_count      INTEGER DEFAULT 0,
                pretrain_count INTEGER DEFAULT 0,
                avg_quality    REAL DEFAULT 0.0,
                price_cny      REAL DEFAULT 0.0,
                license_type   TEXT DEFAULT 'enterprise_internal',
                export_paths   TEXT DEFAULT '{}',
                created_at     TEXT DEFAULT (datetime('now'))
            );

            -- 购买记录（买家下载鉴权依据）
            CREATE TABLE IF NOT EXISTS dataset_purchases (
                sale_id        TEXT PRIMARY KEY,
                package_id     TEXT NOT NULL,
                buyer_id       TEXT NOT NULL,
                price_cny      REAL DEFAULT 0.0,
                purchased_at   TEXT DEFAULT (datetime('now')),
                download_count INTEGER DEFAULT 0,
                last_download  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_purchase_buyer   ON dataset_purchases(buyer_id);
            CREATE INDEX IF NOT EXISTS idx_purchase_package ON dataset_purchases(package_id);

            -- 索引加速查询
            CREATE INDEX IF NOT EXISTS idx_sft_creator   ON sft_samples(creator_id);
            CREATE INDEX IF NOT EXISTS idx_dpo_creator   ON dpo_samples(creator_id);
        """)
        conn.commit()

        # ── 在线迁移：为已存在的旧库补加 package_id 列 ────────────────
        # ALTER TABLE ADD COLUMN 若列已存在会报错，捕获后忽略
        for tbl in ("sft_samples", "dpo_samples"):
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN package_id TEXT DEFAULT ''")
                conn.commit()
                print(f"  [migrate] {tbl}.package_id 列已添加")
            except Exception:
                pass  # 列已存在，忽略

        # 补充 package_id 索引（若已存在则忽略）
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sft_package ON sft_samples(package_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dpo_package ON dpo_samples(package_id)")
            conn.commit()
        except Exception:
            pass

        conn.close()
    print(f"✅ [store/db] SQLite 初始化完成 (样本+包元数据): {DB_PATH}")


# 启动时自动建表
init_db()


# ════════════════════════════════════════════════════════════════════
# SFT / DPO / Pretrain 批量写入
# ════════════════════════════════════════════════════════════════════

async def bulk_insert_sft(samples) -> int:
    """批量写入 SFT 样本（忽略重复 sample_id）"""
    if not samples:
        return 0

    def _do():
        with _lock:
            conn = _get_conn()
            try:
                rows = []
                for s in samples:
                    rows.append((
                        s.sample_id, s.material_id, s.creator_id,
                        getattr(s, "package_id", ""),
                        s.instruction, s.input, s.output,
                        s.quality_score, str(s.quality_tier.value if hasattr(s.quality_tier, 'value') else s.quality_tier),
                        s.domain, s.language, s.token_count,
                        json.dumps(s.annotation_meta, ensure_ascii=False),
                    ))
                conn.executemany("""
                    INSERT OR IGNORE INTO sft_samples
                    (sample_id,material_id,creator_id,package_id,instruction,input,output,
                     quality_score,quality_tier,domain,language,token_count,annotation_meta)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, _do)
    return count


async def bulk_insert_dpo(samples) -> int:
    """批量写入 DPO 样本"""
    if not samples:
        return 0

    def _do():
        with _lock:
            conn = _get_conn()
            try:
                rows = [(
                    s.sample_id, s.material_id, s.creator_id,
                    getattr(s, "package_id", ""),
                    s.prompt, s.chosen, s.rejected,
                    s.quality_score, str(s.quality_tier.value if hasattr(s.quality_tier, 'value') else s.quality_tier),
                    s.domain, s.language, s.token_count,
                ) for s in samples]
                conn.executemany("""
                    INSERT OR IGNORE INTO dpo_samples
                    (sample_id,material_id,creator_id,package_id,prompt,chosen,rejected,
                     quality_score,quality_tier,domain,language,token_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do)


async def bulk_insert_pretrain(chunks) -> int:
    """批量写入 Pretrain chunks"""
    if not chunks:
        return 0

    def _do():
        with _lock:
            conn = _get_conn()
            try:
                rows = [(
                    c.chunk_id, c.material_id, c.creator_id,
                    c.text, c.quality_score,
                    c.domain, c.language, c.token_count, c.source_url,
                ) for c in chunks]
                conn.executemany("""
                    INSERT OR IGNORE INTO pretrain_chunks
                    (chunk_id,material_id,creator_id,text,quality_score,
                     domain,language,token_count,source_url)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do)


# ════════════════════════════════════════════════════════════════════
# 收益记录
# ════════════════════════════════════════════════════════════════════

async def insert_revenue_records(records) -> int:
    """批量写入分润记录"""
    if not records:
        return 0

    def _do():
        with _lock:
            conn = _get_conn()
            try:
                rows = []
                for r in records:
                    rows.append((
                        getattr(r, 'record_id', str(uuid.uuid4())),
                        r.creator_id,
                        r.package_id,
                        getattr(r, 'buyer_id', ''),
                        r.sale_amount,
                        r.creator_amount,
                        r.platform_fee,
                        getattr(r, 'contribution_weight', 0.0),
                        getattr(r, 'sample_count', 0),
                        getattr(r, 'token_count', 0),
                        'pending',
                    ))
                conn.executemany("""
                    INSERT OR IGNORE INTO revenue_records
                    (record_id,creator_id,package_id,buyer_id,sale_amount,creator_amount,
                     platform_fee,contribution_weight,sample_count,token_count,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do)


def get_creator_revenue(creator_id: str) -> dict:
    """查询创作者总收益（同步版）"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("""
                SELECT
                    COUNT(*) as record_count,
                    COALESCE(SUM(creator_amount), 0) as total_earned,
                    COALESCE(SUM(CASE WHEN status='settled' THEN creator_amount ELSE 0 END), 0) as total_settled,
                    COALESCE(SUM(CASE WHEN status='pending' THEN creator_amount ELSE 0 END), 0) as pending
                FROM revenue_records WHERE creator_id = ?
            """, (creator_id,)).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# 账本 (Ledger)
# ════════════════════════════════════════════════════════════════════

def ledger_credit(creator_id: str, amount: float, reference_id: str = "", note: str = "") -> dict:
    """向创作者账本记入收益（同步，有锁）"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN entry_type='credit' THEN amount ELSE -amount END), 0) as bal "
                "FROM creator_ledger WHERE creator_id=?", (creator_id,)
            ).fetchone()
            balance_before = row["bal"] if row else 0.0
            balance_after  = balance_before + amount

            entry_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO creator_ledger
                (entry_id,creator_id,amount,balance_after,entry_type,reference_id,note)
                VALUES (?,?,?,?,'credit',?,?)
            """, (entry_id, creator_id, amount, balance_after, reference_id, note))
            conn.commit()
            return {"entry_id": entry_id, "balance_after": balance_after}
        finally:
            conn.close()


def get_creator_balance(creator_id: str) -> float:
    """查询创作者当前余额"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN entry_type='credit' THEN amount ELSE -amount END), 0) as bal "
                "FROM creator_ledger WHERE creator_id=?", (creator_id,)
            ).fetchone()
            return float(row["bal"]) if row else 0.0
        finally:
            conn.close()


def get_ledger_history(creator_id: str, limit: int = 50) -> list:
    """查询账本流水"""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT * FROM creator_ledger WHERE creator_id=?
                ORDER BY created_at DESC LIMIT ?
            """, (creator_id, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# 数据集包
# ════════════════════════════════════════════════════════════════════

async def save_package(package) -> bool:
    """持久化 DatasetPackage 元数据"""
    def _do():
        with _lock:
            conn = _get_conn()
            try:
                export_paths = getattr(package, 'export_paths', {})
                conn.execute("""
                    INSERT OR REPLACE INTO dataset_packages
                    (package_id,name,description,version,total_samples,sft_count,
                     dpo_count,pretrain_count,avg_quality,price_cny,license_type,export_paths)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    package.package_id, package.name, package.description,
                    getattr(package, 'version', '1.0.0'),
                    package.total_samples, package.sft_count, package.dpo_count,
                    package.pretrain_count, package.avg_quality, package.price_cny,
                    package.license_type,
                    json.dumps(export_paths, ensure_ascii=False),
                ))
                conn.commit()
                return True
            finally:
                conn.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do)


def list_packages(limit: int = 20) -> list:
    """列出最近的数据集包"""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM dataset_packages ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# 统计
# ════════════════════════════════════════════════════════════════════

def get_platform_stats() -> dict:
    """平台样本/包统计（收益数据由 dataset_api 从 CreatorLedger 叠加）"""
    with _lock:
        conn = _get_conn()
        try:
            sft_count = conn.execute("SELECT COUNT(*) FROM sft_samples").fetchone()[0]
            dpo_count = conn.execute("SELECT COUNT(*) FROM dpo_samples").fetchone()[0]
            pre_count = conn.execute("SELECT COUNT(*) FROM pretrain_chunks").fetchone()[0]
            pkg_count = conn.execute("SELECT COUNT(*) FROM dataset_packages").fetchone()[0]
            return {
                "sft_samples":     sft_count,
                "dpo_samples":     dpo_count,
                "pretrain_chunks": pre_count,
                "total_samples":   sft_count + dpo_count + pre_count,
                "packages":        pkg_count,
                # total_revenue / creator_count injected by dataset_api from CreatorLedger
            }
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# 购买记录（买家下载鉴权）
# ════════════════════════════════════════════════════════════════════

def record_purchase(sale_id: str, package_id: str, buyer_id: str, price_cny: float) -> None:
    """写入购买记录，幂等（同 sale_id 重复调用安全）。"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO dataset_purchases
                   (sale_id, package_id, buyer_id, price_cny)
                   VALUES (?, ?, ?, ?)""",
                (sale_id, package_id, buyer_id, price_cny),
            )
            conn.commit()
        finally:
            conn.close()


def check_purchase(buyer_id: str, package_id: str) -> bool:
    """检查 buyer_id 是否已购买 package_id。"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT sale_id FROM dataset_purchases WHERE buyer_id=? AND package_id=?",
                (buyer_id, package_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def increment_download(buyer_id: str, package_id: str) -> None:
    """每次下载后递增计数并记录时间。"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE dataset_purchases
                   SET download_count = download_count + 1,
                       last_download  = datetime('now')
                   WHERE buyer_id=? AND package_id=?""",
                (buyer_id, package_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_package(package_id: str) -> dict | None:
    """按 package_id 查询单个数据集包详情，不存在返回 None。"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM dataset_packages WHERE package_id = ?", (package_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_samples_by_package(package_id: str, limit: int = 3) -> list:
    """
    返回属于某个数据集包的样本预览（SFT + DPO 混合，最多 limit 条）。
    注意：dataset_packages 表没有直接关联 sample，通过 package_id 字段关联。
    如果 sft_samples / dpo_samples 没有 package_id 列，则降级返回空列表。
    """
    with _lock:
        conn = _get_conn()
        try:
            samples = []
            # SFT 样本
            try:
                rows = conn.execute(
                    "SELECT instruction, input, output, quality_score, 'sft' AS sample_type "
                    "FROM sft_samples WHERE package_id = ? LIMIT ?",
                    (package_id, limit),
                ).fetchall()
                samples.extend([dict(r) for r in rows])
            except Exception:
                pass
            # DPO 样本（补足到 limit）
            if len(samples) < limit:
                try:
                    rows = conn.execute(
                        "SELECT prompt AS instruction, chosen, rejected, quality_score, 'dpo' AS sample_type "
                        "FROM dpo_samples WHERE package_id = ? LIMIT ?",
                        (package_id, limit - len(samples)),
                    ).fetchall()
                    samples.extend([dict(r) for r in rows])
                except Exception:
                    pass
            return samples[:limit]
        finally:
            conn.close()

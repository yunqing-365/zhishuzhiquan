"""
storage.py — AI-Echo 数据持久层 v2
=====================================
v1 → v2 升级:
  [修复] 新增 search_history()：oracle_engine /api/history/search 端点调用此函数，
         v1 中缺失，导致搜索端点 500 崩溃。
  [新增] get_modality_stats()：按模态 + 场景的详细统计，供 /api/stats 展示。
  [新增] delete_valuation()：按 id 删除单条记录，对应 DELETE /api/history/{id}。
  [新增] get_top_assets()：按动态报价降序返回 Top-N 资产，供排行榜展示。
使用 SQLite（零依赖、无需单独部署）存储估值历史。

数据库文件: ai-echo-backend/data/history.db
ChromaDB 持久化: ai-echo-backend/data/chroma_db/

设计原则：
  - 所有操作封装为纯函数，oracle_engine.py 只调用函数，不接触 sqlite3
  - 失败静默（存储失败不影响主流程，仅打印警告）
  - history.db 与 chroma_db/ 均放在 data/ 目录，方便整体备份
"""

import os
import json
import time
import sqlite3
import threading
from typing import Optional

# ── 路径配置 ──────────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH   = os.path.join(_DATA_DIR, "history.db")
CHROMA_PATH = os.path.join(_DATA_DIR, "chroma_db")

# ── 线程锁（SQLite 在多线程 FastAPI 下需要串行写入）─────────────────
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
# 初始化
# ─────────────────────────────────────────────────────────────────────

def _migrate_db(conn: sqlite3.Connection) -> None:
    """
    安全迁移旧版数据库（幂等）。
    只做 ADD COLUMN，不破坏已有数据。
    ALTER TABLE 若列已存在会抛 OperationalError，静默忽略。
    """
    migrations = [
        "ALTER TABLE valuations ADD COLUMN zk_commitment TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过


def init_db() -> bool:
    """创建数据库表（幂等，重复调用安全）"""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS valuations (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp         INTEGER NOT NULL,
                    asset_hash        TEXT,
                    modality          TEXT,
                    scene             TEXT,
                    audio_scene       TEXT,
                    composite_quality REAL,
                    dynamic_price     INTEGER,
                    base_value        REAL,
                    option_premium    INTEGER,
                    creator_ratio     REAL,
                    vector_distance   REAL,
                    description_preview TEXT,
                    full_result       TEXT         -- JSON 完整 response
                )
            """)
            # 快速查询索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON valuations(timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_modality ON valuations(modality)"
            )
            # ★ v3: 对旧库执行迁移（新建库此步骤无副作用）
            _migrate_db(conn)
            conn.commit()
            conn.close()
        print(f">> [storage] SQLite 初始化完成 (v3): {DB_PATH}")
        return True
    except Exception as e:
        print(f"!! [storage] 初始化失败 (不影响估值): {e}")
        return False


# ─────────────────────────────────────────────────────────────────────
# 写入
# ─────────────────────────────────────────────────────────────────────

def save_valuation(
    result: dict,
    description: str,
    vector_distance: float = 0.0,
) -> Optional[int]:
    """
    保存一次估值结果到 SQLite。
    返回插入的 row id，失败返回 None（静默，不抛异常）。
    """
    try:
        sc  = result.get("scene_classification", {})
        fv  = result.get("final_valuation", {})
        zk  = result.get("zk_proof") or {}
        zk_commitment = zk.get("commitment") if zk else None
        preview = (description[:120] + "…") if len(description) > 120 else description

        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cur = conn.execute(
                """
                INSERT INTO valuations (
                    timestamp, asset_hash, modality, scene, audio_scene,
                    composite_quality, dynamic_price, base_value,
                    option_premium, creator_ratio, vector_distance,
                    description_preview, full_result, zk_commitment
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(time.time()),
                    result.get("asset_hash", ""),
                    result.get("meta", {}).get("modality", ""),
                    sc.get("scene", ""),
                    sc.get("audio_scene"),
                    fv.get("composite_quality", 0),
                    fv.get("dynamic_price", 0),
                    fv.get("base_value", 0),
                    fv.get("option_premium", 0),
                    fv.get("creator_ratio", 0),
                    round(vector_distance, 4),
                    preview,
                    json.dumps(result, ensure_ascii=False),
                    zk_commitment,   # ★ v3: bytes32 hex 或 NULL
                ),
            )
            row_id = cur.lastrowid
            conn.commit()
            conn.close()
        return row_id
    except Exception as e:
        print(f"!! [storage] save_valuation 失败 (不影响估值): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# 查询
# ─────────────────────────────────────────────────────────────────────

def get_history(limit: int = 20, modality: Optional[str] = None) -> list:
    """
    返回最近 limit 条估值记录（轻量摘要，不含完整 JSON）。
    modality 可选过滤: 'text' | 'image' | 'audio' | 'video'
    """
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row

            if modality:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, asset_hash, modality, scene, audio_scene,
                           composite_quality, dynamic_price, base_value,
                           option_premium, creator_ratio, vector_distance,
                           description_preview
                    FROM valuations
                    WHERE modality = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (modality, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, asset_hash, modality, scene, audio_scene,
                           composite_quality, dynamic_price, base_value,
                           option_premium, creator_ratio, vector_distance,
                           description_preview
                    FROM valuations
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"!! [storage] get_history 失败: {e}")
        return []


def get_stats() -> dict:
    """返回整体统计信息，供 /api/health 展示"""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            total   = conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0]
            by_mod  = dict(
                conn.execute(
                    "SELECT modality, COUNT(*) FROM valuations GROUP BY modality"
                ).fetchall()
            )
            avg_price = conn.execute(
                "SELECT AVG(dynamic_price) FROM valuations WHERE dynamic_price > 0"
            ).fetchone()[0] or 0
            conn.close()
        return {
            "total_valuations": total,
            "by_modality":      by_mod,
            "avg_dynamic_price": round(avg_price),
        }
    except Exception:
        return {"total_valuations": 0, "by_modality": {}, "avg_dynamic_price": 0}


def get_valuation_by_id(row_id: int) -> Optional[dict]:
    """返回单条完整估值结果（含 full_result JSON）"""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM valuations WHERE id = ?", (row_id,)
            ).fetchone()
            conn.close()
        if not row:
            return None
        d = dict(row)
        if d.get("full_result"):
            d["full_result"] = json.loads(d["full_result"])
        return d
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# v2 新增函数
# ─────────────────────────────────────────────────────────────────────

def search_history(query: str, limit: int = 20) -> list:
    """
    全文搜索历史记录（模糊匹配 description_preview、scene、asset_hash）。
    oracle_engine /api/history/search 端点调用此函数。
    v1 中缺失，导致搜索端点启动即 ImportError 500 崩溃。
    """
    if not query:
        return get_history(limit=limit)
    try:
        pattern = f"%{query}%"
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, timestamp, asset_hash, modality, scene, audio_scene,
                       composite_quality, dynamic_price, base_value,
                       option_premium, creator_ratio, vector_distance,
                       description_preview
                FROM valuations
                WHERE description_preview LIKE ?
                   OR scene              LIKE ?
                   OR asset_hash         LIKE ?
                   OR modality           LIKE ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, limit),
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"!! [storage] search_history 失败: {e}")
        return []


def delete_valuation(row_id: int) -> bool:
    """
    按 id 删除单条估值记录。
    对应 DELETE /api/history/{id}，v1 该端点已存在但底层函数缺失。
    返回 True 表示成功删除（即使记录不存在也返回 True，幂等）。
    """
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("DELETE FROM valuations WHERE id = ?", (row_id,))
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        print(f"!! [storage] delete_valuation 失败: {e}")
        return False


def get_modality_stats() -> dict:
    """
    按模态 + 场景维度的详细统计，供 /api/stats 增强端点展示。
    返回结构:
      {
        "by_modality": {"text": {"count": 12, "avg_price": 3200, "max_price": 8800}, ...},
        "top_scenes":  [{"scene": "medical_sft", "count": 5, "avg_price": 9100}, ...],
        "total":       42,
        "avg_quality": 72.3,
      }
    """
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row

            total = conn.execute("SELECT COUNT(*) as c FROM valuations").fetchone()["c"]
            avg_q = conn.execute(
                "SELECT AVG(composite_quality) as q FROM valuations WHERE composite_quality > 0"
            ).fetchone()["q"] or 0

            mod_rows = conn.execute(
                """
                SELECT modality,
                       COUNT(*)           AS cnt,
                       AVG(dynamic_price) AS avg_price,
                       MAX(dynamic_price) AS max_price
                FROM valuations
                GROUP BY modality
                """
            ).fetchall()

            scene_rows = conn.execute(
                """
                SELECT scene,
                       COUNT(*)           AS cnt,
                       AVG(dynamic_price) AS avg_price
                FROM valuations
                WHERE scene != ''
                GROUP BY scene
                ORDER BY cnt DESC
                LIMIT 10
                """
            ).fetchall()

            conn.close()

        by_modality = {
            r["modality"]: {
                "count":     r["cnt"],
                "avg_price": round(r["avg_price"] or 0),
                "max_price": r["max_price"] or 0,
            }
            for r in mod_rows if r["modality"]
        }
        top_scenes = [
            {
                "scene":     r["scene"],
                "count":     r["cnt"],
                "avg_price": round(r["avg_price"] or 0),
            }
            for r in scene_rows
        ]
        return {
            "total":       total,
            "avg_quality": round(avg_q, 1),
            "by_modality": by_modality,
            "top_scenes":  top_scenes,
        }
    except Exception as e:
        print(f"!! [storage] get_modality_stats 失败: {e}")
        return {"total": 0, "avg_quality": 0, "by_modality": {}, "top_scenes": []}


def get_top_assets(limit: int = 10, modality: Optional[str] = None) -> list:
    """
    按动态报价降序返回 Top-N 资产，供排行榜/看板展示。
    """
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if modality:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, asset_hash, modality, scene, audio_scene,
                           composite_quality, dynamic_price, description_preview
                    FROM valuations
                    WHERE modality = ?
                    ORDER BY dynamic_price DESC LIMIT ?
                    """,
                    (modality, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, asset_hash, modality, scene, audio_scene,
                           composite_quality, dynamic_price, description_preview
                    FROM valuations
                    ORDER BY dynamic_price DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"!! [storage] get_top_assets 失败: {e}")
        return []

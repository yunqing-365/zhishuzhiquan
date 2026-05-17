"""
storage.py — AI-Echo 数据持久层 v1
=====================================
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
            conn.commit()
            conn.close()
        print(f">> [storage] SQLite 初始化完成: {DB_PATH}")
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
        preview = (description[:120] + "…") if len(description) > 120 else description

        with _lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cur = conn.execute(
                """
                INSERT INTO valuations (
                    timestamp, asset_hash, modality, scene, audio_scene,
                    composite_quality, dynamic_price, base_value,
                    option_premium, creator_ratio, vector_distance,
                    description_preview, full_result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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

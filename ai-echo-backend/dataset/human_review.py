# dataset/human_review.py  v2
"""
知数知圈 · 人工复核队列 v2

升级日志 v2:
  [P0修复] 所有写操作加 threading.Lock，消除多用户并发写同一文件导致的数据覆盖
  [P0修复] 持久化从 JSON 迁移到 SQLite（复用 storage.DB_PATH），重启安全
  [保留]   list_pending / approve / reject 接口完全兼容旧调用方
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage   # 复用同一 SQLite 文件

_lock = threading.Lock()

# ── 确保表存在（幂等）─────────────────────────────────────────────
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS review_queue (
    review_id   TEXT PRIMARY KEY,
    sample_id   TEXT NOT NULL,
    sample_type TEXT NOT NULL,
    content     TEXT DEFAULT '{}',
    reason      TEXT DEFAULT '',
    score       REAL DEFAULT 0.0,
    status      TEXT DEFAULT 'pending',
    reviewer    TEXT,
    reviewed_at TEXT,
    created_at  TEXT NOT NULL
);
"""

def _ensure_table():
    try:
        conn = sqlite3.connect(storage.DB_PATH, check_same_thread=False)
        conn.execute(_CREATE_SQL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status)"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"!! [review_queue] 建表失败: {e}")

_ensure_table()


# ════════════════════════════════════════════════════════════════════

import json as _json

class HumanReviewQueue:
    """
    人工复核队列 — SQLite 持久化，线程安全。
    所有方法接口与 v1 完全兼容。
    """

    # ── 写操作 ───────────────────────────────────────────────────────

    def enqueue(
        self,
        sample_id:   str,
        sample_type: str,
        content:     dict,
        reason:      str = "",
        score:       float = 0.0,
    ) -> str:
        """将样本加入复核队列，返回 review_id。"""
        review_id  = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()

        with _lock:
            try:
                conn = sqlite3.connect(storage.DB_PATH, check_same_thread=False)
                conn.execute(
                    """INSERT INTO review_queue
                       (review_id, sample_id, sample_type, content, reason,
                        score, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (
                        review_id, sample_id, sample_type,
                        _json.dumps(content, ensure_ascii=False),
                        reason, score, created_at,
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"!! [review_queue] enqueue 失败: {e}")

        return review_id

    def approve(self, review_id: str, reviewer: str = "admin") -> bool:
        return self._set_status(review_id, "approved", reviewer)

    def reject(self, review_id: str, reviewer: str = "admin") -> bool:
        return self._set_status(review_id, "rejected", reviewer)

    def _set_status(self, review_id: str, status: str, reviewer: str) -> bool:
        reviewed_at = datetime.utcnow().isoformat()
        with _lock:
            try:
                conn = sqlite3.connect(storage.DB_PATH, check_same_thread=False)
                cur = conn.execute(
                    """UPDATE review_queue
                       SET status=?, reviewer=?, reviewed_at=?
                       WHERE review_id=?""",
                    (status, reviewer, reviewed_at, review_id),
                )
                conn.commit()
                conn.close()
                return cur.rowcount > 0
            except Exception as e:
                print(f"!! [review_queue] _set_status 失败: {e}")
                return False

    # ── 读操作（无锁，SQLite 读是并发安全的）────────────────────────

    def list_pending(self) -> List[dict]:
        return self._query("SELECT * FROM review_queue WHERE status='pending' ORDER BY created_at DESC")

    def list_all(self, limit: int = 100) -> List[dict]:
        return self._query(
            "SELECT * FROM review_queue ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    def get(self, review_id: str) -> Optional[dict]:
        rows = self._query(
            "SELECT * FROM review_queue WHERE review_id=?", (review_id,)
        )
        return rows[0] if rows else None

    def count_pending(self) -> int:
        try:
            conn = sqlite3.connect(storage.DB_PATH, check_same_thread=False)
            n = conn.execute(
                "SELECT COUNT(*) FROM review_queue WHERE status='pending'"
            ).fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0

    def _query(self, sql: str, params: tuple = ()) -> List[dict]:
        try:
            conn = sqlite3.connect(storage.DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["content"] = _json.loads(d.get("content") or "{}")
                except Exception:
                    d["content"] = {}
                result.append(d)
            return result
        except Exception as e:
            print(f"!! [review_queue] query 失败: {e}")
            return []

# dataset/versioning.py  v2
"""
知数知圈 · 数据集版本管理 v2

v2 升级：
  - 版本记录从 JSON 文件迁移至 SQLite（重启不丢、可查询）
  - 自动在 pipeline 打包完成后创建版本快照
  - 支持版本 diff（新增/删除样本数、质量变化）

表：dataset_versions（在 store/db.py 初始化的 zszq.db 中）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

# 兼容直接运行和模块导入两种路径
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from store.db import DB_PATH, _lock as _DB_LOCK


# ── 建表（幂等，在 store/db.init_db 之后被调用也安全）──────────────

def _ensure_version_table() -> None:
    with _DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dataset_versions (
                version_id      TEXT PRIMARY KEY,
                package_id      TEXT NOT NULL,
                name            TEXT NOT NULL,
                version         TEXT NOT NULL,
                changelog       TEXT DEFAULT '',
                sft_count       INTEGER DEFAULT 0,
                dpo_count       INTEGER DEFAULT 0,
                pretrain_count  INTEGER DEFAULT 0,
                total_samples   INTEGER DEFAULT 0,
                avg_quality     REAL DEFAULT 0.0,
                delta_samples   INTEGER DEFAULT 0,   -- vs previous version
                delta_quality   REAL DEFAULT 0.0,
                export_paths    TEXT DEFAULT '{}',
                metadata        TEXT DEFAULT '{}',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ver_name ON dataset_versions(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ver_pkg  ON dataset_versions(package_id)")
        conn.commit()
        conn.close()


_ensure_version_table()


# ════════════════════════════════════════════════════════════════════
# DatasetVersionManager v2
# ════════════════════════════════════════════════════════════════════

class DatasetVersionManager:
    """
    数据集版本管理器 v2 — SQLite 持久化

    使用示例（pipeline 打包完成后）：
        vm = DatasetVersionManager()
        record = vm.snapshot_from_package(package, changelog="新增医疗 DPO 数据 200 条")
    """

    # ── 核心：从 DatasetPackage 自动快照 ─────────────────────────

    def snapshot_from_package(
        self,
        package,
        changelog: str = "",
    ) -> dict:
        """
        打包完成后自动调用，将包的完整元信息写入版本表。
        自动计算与上一版本的 delta。
        """
        name        = package.name
        prev        = self._latest(name)
        prev_total  = prev["total_samples"] if prev else 0
        prev_quality = prev["avg_quality"]  if prev else 0.0
        total       = package.total_samples
        avg_q       = package.avg_quality

        record = {
            "version_id":     str(uuid.uuid4()),
            "package_id":     package.package_id,
            "name":           name,
            "version":        self._next_version(name),
            "changelog":      changelog,
            "sft_count":      package.sft_count,
            "dpo_count":      package.dpo_count,
            "pretrain_count": package.pretrain_count,
            "total_samples":  total,
            "avg_quality":    avg_q,
            "delta_samples":  total - prev_total,
            "delta_quality":  round(avg_q - prev_quality, 4),
            "export_paths":   json.dumps(getattr(package, "export_paths", {}), ensure_ascii=False),
            "metadata":       json.dumps({
                "price_cny":    package.price_cny,
                "license_type": package.license_type,
                "created_at":   package.created_at.isoformat() if hasattr(package.created_at, 'isoformat') else str(package.created_at),
            }, ensure_ascii=False),
            "created_at":     datetime.utcnow().isoformat(),
        }
        self._insert(record)
        print(f"📌 [versioning] 版本快照: {name} {record['version']} "
              f"(Δ样本 {record['delta_samples']:+d}, Δ质量 {record['delta_quality']:+.2f})")
        return record

    # ── 创建自定义版本 ──────────────────────────────────────────

    def create_version(
        self,
        package_id: str,
        name: str,
        version: str = None,
        changelog: str = "",
        metadata: dict = None,
        sft_count: int = 0,
        dpo_count: int = 0,
        pretrain_count: int = 0,
        avg_quality: float = 0.0,
    ) -> dict:
        """兼容旧接口，手动创建版本记录"""
        total = sft_count + dpo_count + pretrain_count
        prev  = self._latest(name)
        record = {
            "version_id":     str(uuid.uuid4()),
            "package_id":     package_id,
            "name":           name,
            "version":        version or self._next_version(name),
            "changelog":      changelog,
            "sft_count":      sft_count,
            "dpo_count":      dpo_count,
            "pretrain_count": pretrain_count,
            "total_samples":  total,
            "avg_quality":    avg_quality,
            "delta_samples":  total - (prev["total_samples"] if prev else 0),
            "delta_quality":  round(avg_quality - (prev["avg_quality"] if prev else 0.0), 4),
            "export_paths":   "{}",
            "metadata":       json.dumps(metadata or {}, ensure_ascii=False),
            "created_at":     datetime.utcnow().isoformat(),
        }
        self._insert(record)
        return record

    # ── 查询 ─────────────────────────────────────────────────────

    def list_versions(self, name: str = None, limit: int = 50) -> List[dict]:
        """列出版本（可按包名过滤），最新的在前"""
        with _DB_LOCK:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                if name:
                    rows = conn.execute(
                        "SELECT * FROM dataset_versions WHERE name=? ORDER BY created_at DESC LIMIT ?",
                        (name, limit)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM dataset_versions ORDER BY created_at DESC LIMIT ?",
                        (limit,)
                    ).fetchall()
                return [self._parse(r) for r in rows]
            finally:
                conn.close()

    def get_version(self, version_id: str) -> Optional[dict]:
        with _DB_LOCK:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM dataset_versions WHERE version_id=?", (version_id,)
                ).fetchone()
                return self._parse(row) if row else None
            finally:
                conn.close()

    def diff(self, version_id_a: str, version_id_b: str) -> dict:
        """对比两个版本之间的差异"""
        a = self.get_version(version_id_a)
        b = self.get_version(version_id_b)
        if not a or not b:
            return {"error": "版本 ID 不存在"}
        return {
            "from": a["version"], "to": b["version"],
            "delta_samples":  b["total_samples"] - a["total_samples"],
            "delta_quality":  round(b["avg_quality"] - a["avg_quality"], 4),
            "delta_sft":      b["sft_count"] - a["sft_count"],
            "delta_dpo":      b["dpo_count"] - a["dpo_count"],
            "delta_pretrain": b["pretrain_count"] - a["pretrain_count"],
            "changelog":      b.get("changelog", ""),
        }

    # ── 内部工具 ─────────────────────────────────────────────────

    def _next_version(self, name: str) -> str:
        prev = self._latest(name)
        if not prev:
            return "1.0.0"
        parts = prev["version"].split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except Exception:
            parts = ["1", "0", "0"]
        return ".".join(parts)

    def _latest(self, name: str) -> Optional[dict]:
        with _DB_LOCK:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM dataset_versions WHERE name=? ORDER BY created_at DESC LIMIT 1",
                    (name,)
                ).fetchone()
                return self._parse(row) if row else None
            finally:
                conn.close()

    def _insert(self, record: dict) -> None:
        with _DB_LOCK:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO dataset_versions
                    (version_id,package_id,name,version,changelog,
                     sft_count,dpo_count,pretrain_count,total_samples,
                     avg_quality,delta_samples,delta_quality,export_paths,metadata,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    record["version_id"], record["package_id"], record["name"],
                    record["version"], record["changelog"],
                    record["sft_count"], record["dpo_count"], record["pretrain_count"],
                    record["total_samples"], record["avg_quality"],
                    record["delta_samples"], record["delta_quality"],
                    record["export_paths"], record["metadata"], record["created_at"],
                ))
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _parse(row) -> dict:
        d = dict(row)
        try:
            d["export_paths"] = json.loads(d.get("export_paths", "{}"))
        except Exception:
            d["export_paths"] = {}
        try:
            d["metadata"] = json.loads(d.get("metadata", "{}"))
        except Exception:
            d["metadata"] = {}
        return d


# 全局单例
version_manager = DatasetVersionManager()

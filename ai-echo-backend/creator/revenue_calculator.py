# creator/revenue_calculator.py
"""
息壤 · 创作者分润引擎

分润公式：
  创作者实得 = 数据集售价 × 平台分润比 × 创作者贡献权重

平台分润比（可配置）：
  平台留存 30%，创作者池 70%

贡献权重来自 DatasetPackager._calc_contributions()：
  综合考虑样本类型（SFT/DPO/Pretrain）、质量等级（铂金/黄金/白银）、token量

分润记录全程可审计（RevenueRecord）
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataset.schema import DatasetPackage, RevenueRecord


# 平台可配置参数
PLATFORM_FEE_RATIO   = 0.30    # 平台留存 30%
CREATOR_POOL_RATIO   = 0.70    # 创作者池 70%
PAYMENT_THRESHOLD    = 10.0    # 最低结算金额（元），低于此不触发结算


# ════════════════════════════════════════════════════════
# 分润计算器
# ════════════════════════════════════════════════════════

class RevenueCalculator:
    """
    接收一笔数据集销售事件，计算每位创作者的应得金额
    并生成 RevenueRecord 列表（可写入数据库）
    """

    def calculate(
        self,
        package: DatasetPackage,
        sale_amount: float,          # 本次实际销售金额（元）
        buyer_id: str = "",
    ) -> List[RevenueRecord]:
        """
        根据一次销售，生成各创作者的分润记录

        Returns:
          List[RevenueRecord]，每位创作者一条
        """
        if not package.creator_contributions:
            print("⚠️  该数据集无创作者贡献记录，无法分润")
            return []

        creator_pool = round(sale_amount * CREATOR_POOL_RATIO, 4)
        platform_fee_total = round(sale_amount * PLATFORM_FEE_RATIO, 4)

        records = []
        for creator_id, ratio in package.creator_contributions.items():
            creator_share = round(creator_pool * ratio, 4)
            # 平台费按贡献比例摊算（便于审计）
            platform_portion = round(platform_fee_total * ratio, 4)

            record = RevenueRecord(
                package_id=package.package_id,
                creator_id=creator_id,
                total_revenue=sale_amount,
                contribution_ratio=ratio,
                creator_share=creator_share,
                platform_fee=platform_portion,
                status="pending",
            )
            records.append(record)

        print(f"💰 销售额 ¥{sale_amount:.2f} → "
              f"创作者池 ¥{creator_pool:.2f}（{len(records)} 人）| "
              f"平台留存 ¥{platform_fee_total:.2f}")
        return records


# ════════════════════════════════════════════════════════
# 创作者收益账本（内存版，生产应替换为 DB）
# ════════════════════════════════════════════════════════

# ── SQLite 建表（幂等，与 storage.DB_PATH 复用同一文件）──────────
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    import storage as _storage
    _DB_PATH = _storage.DB_PATH
except Exception:
    _DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "history.db")

_ledger_lock = threading.Lock()

def _ensure_ledger_tables():
    try:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_balances (
                creator_id TEXT PRIMARY KEY,
                pending    REAL DEFAULT 0.0,
                paid       REAL DEFAULT 0.0,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_records (
                record_id          TEXT PRIMARY KEY,
                package_id         TEXT NOT NULL,
                creator_id         TEXT NOT NULL,
                total_revenue      REAL DEFAULT 0.0,
                contribution_ratio REAL DEFAULT 0.0,
                creator_share      REAL DEFAULT 0.0,
                platform_fee       REAL DEFAULT 0.0,
                status             TEXT DEFAULT 'pending',
                created_at         TEXT NOT NULL,
                paid_at            TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_creator ON ledger_records(creator_id)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"!! [ledger] 建表失败: {e}")

_ensure_ledger_tables()


class CreatorLedger:
    """
    创作者收益总账 v2

    升级日志:
      [P0修复] 所有写操作加 threading.Lock，消除并发覆盖
      [P0修复] 持久化从 JSON 迁移到 SQLite（与 history.db 共用）
      [保留]   所有公开接口向后兼容
    """

    def __init__(self, persist_path: str = None):
        # persist_path 保留参数签名兼容性，实际不再使用（改为 SQLite）
        self._persist_path = persist_path  # 仅用于旧数据迁移检查
        self._migrate_json_if_needed()

    def _migrate_json_if_needed(self):
        """首次启动时把旧 JSON 账本迁移进 SQLite，之后不再重复。"""
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, encoding='utf-8') as f:
                data = json.load(f)
            if not data.get('ledger'):
                return
            print(f">> [ledger] 迁移旧 JSON 账本 → SQLite ...")
            with _ledger_lock:
                conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
                for cid, v in data.get('ledger', {}).items():
                    conn.execute(
                        """INSERT OR IGNORE INTO ledger_balances
                           (creator_id, pending, paid, updated_at) VALUES (?,?,?,?)""",
                        (cid, v.get('pending',0), v.get('paid',0), datetime.utcnow().isoformat())
                    )
                for rid, r in data.get('records', {}).items():
                    conn.execute(
                        """INSERT OR IGNORE INTO ledger_records
                           (record_id,package_id,creator_id,total_revenue,
                            contribution_ratio,creator_share,platform_fee,
                            status,created_at,paid_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (rid, r['package_id'], r['creator_id'],
                         r['total_revenue'], r['contribution_ratio'],
                         r['creator_share'], r['platform_fee'],
                         r['status'], r['created_at'], r.get('paid_at'))
                    )
                conn.commit()
                conn.close()
            os.rename(self._persist_path, self._persist_path + '.migrated')
            print(">> [ledger] 迁移完成，旧文件已改名为 .migrated")
        except Exception as e:
            print(f"!! [ledger] 迁移失败（不影响运行）: {e}")

    def add_records(self, records: List[RevenueRecord]):
        """入账新的分润记录（线程安全）"""
        now = datetime.utcnow().isoformat()
        with _ledger_lock:
            try:
                conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
                for r in records:
                    conn.execute(
                        """INSERT OR REPLACE INTO ledger_records
                           (record_id,package_id,creator_id,total_revenue,
                            contribution_ratio,creator_share,platform_fee,
                            status,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (r.record_id, r.package_id, r.creator_id,
                         r.total_revenue, r.contribution_ratio,
                         r.creator_share, r.platform_fee,
                         r.status, r.created_at.isoformat())
                    )
                    # 原子更新余额：INSERT + ON CONFLICT 累加
                    conn.execute(
                        """INSERT INTO ledger_balances (creator_id, pending, paid, updated_at)
                           VALUES (?, ?, 0.0, ?)
                           ON CONFLICT(creator_id) DO UPDATE SET
                             pending    = pending + excluded.pending,
                             updated_at = excluded.updated_at""",
                        (r.creator_id, r.creator_share, now)
                    )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"!! [ledger] add_records 失败: {e}")

    def get_balance(self, creator_id: str) -> dict:
        """查询创作者账户余额"""
        try:
            conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            row = conn.execute(
                "SELECT pending, paid FROM ledger_balances WHERE creator_id=?",
                (creator_id,)
            ).fetchone()
            conn.close()
            pending = row[0] if row else 0.0
            paid    = row[1] if row else 0.0
        except Exception:
            pending, paid = 0.0, 0.0
        return {
            "creator_id":   creator_id,
            "pending_cny":  round(pending, 2),
            "paid_cny":     round(paid, 2),
            "total_earned": round(pending + paid, 2),
        }

    def settle(self, creator_id: str) -> Optional[dict]:
        """触发结算：将 pending → paid（线程安全，原子操作）"""
        with _ledger_lock:
            try:
                conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
                row = conn.execute(
                    "SELECT pending FROM ledger_balances WHERE creator_id=?",
                    (creator_id,)
                ).fetchone()
                if not row or row[0] < PAYMENT_THRESHOLD:
                    conn.close()
                    print(f"⚠️  {creator_id} 待结算 ¥{row[0] if row else 0:.2f} < 阈值")
                    return None
                amount = row[0]
                now    = datetime.utcnow().isoformat()
                conn.execute(
                    """UPDATE ledger_balances SET paid=paid+?, pending=0.0, updated_at=?
                       WHERE creator_id=?""",
                    (amount, now, creator_id)
                )
                conn.execute(
                    """UPDATE ledger_records SET status='paid', paid_at=?
                       WHERE creator_id=? AND status='pending'""",
                    (now, creator_id)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"!! [ledger] settle 失败: {e}")
                return None
        print(f"✅ 结算成功: {creator_id} ¥{amount:.2f}")
        return {"creator_id": creator_id, "settled_amount": round(amount, 2),
                "settled_at": datetime.utcnow().isoformat()}

    def get_all_balances(self) -> List[dict]:
        """获取所有创作者余额（管理后台用）"""
        try:
            conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            rows = conn.execute(
                "SELECT creator_id, pending, paid FROM ledger_balances"
            ).fetchall()
            conn.close()
            return [
                {"creator_id": r[0], "pending_cny": round(r[1],2),
                 "paid_cny": round(r[2],2), "total_earned": round(r[1]+r[2],2)}
                for r in rows
            ]
        except Exception:
            return []

    def get_top_earners(self, limit: int = 10) -> List[dict]:
        """收益排行榜"""
        balances = self.get_all_balances()
        return sorted(balances, key=lambda x: x["total_earned"], reverse=True)[:limit]

    def get_creator_records(self, creator_id: str) -> List[dict]:
        """查询创作者历史分润明细"""
        try:
            conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM ledger_records WHERE creator_id=?
                   ORDER BY created_at DESC LIMIT 200""",
                (creator_id,)
            ).fetchall()
            conn.close()
            return [
                {
                    "record_id":          r["record_id"],
                    "package_id":         r["package_id"],
                    "contribution_ratio": round(r["contribution_ratio"], 4),
                    "sale_total":         r["total_revenue"],
                    "creator_share":      round(r["creator_share"], 2),
                    "status":             r["status"],
                    "created_at":         r["created_at"],
                    "paid_at":            r["paid_at"],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"!! [ledger] get_creator_records: {e}")
            return []

    # _save / _load 已移除，所有写操作直接操作 SQLite（线程安全）


# ════════════════════════════════════════════════════════
# 贡献度分析报告
# ════════════════════════════════════════════════════════

class ContributionAnalyzer:
    """
    分析创作者在各个数据集中的贡献情况
    用于创作者门户展示和运营决策
    """

    def __init__(self, ledger: CreatorLedger):
        self.ledger = ledger

    def creator_report(self, creator_id: str) -> dict:
        """生成创作者个人贡献报告"""
        balance = self.ledger.get_balance(creator_id)
        records = self.ledger.get_creator_records(creator_id)

        # 参与的数据集
        packages = list({r["package_id"] for r in records})

        # 月度收益趋势
        monthly = defaultdict(float)
        for r in records:
            if r["status"] in ("paid", "pending"):
                month = r["created_at"][:7]  # YYYY-MM
                monthly[month] += r["creator_share"]

        return {
            "creator_id":        creator_id,
            "balance":           balance,
            "packages_count":    len(packages),
            "total_records":     len(records),
            "monthly_earnings":  dict(sorted(monthly.items())),
            "recent_records":    records[:10],
        }

    def platform_summary(self) -> dict:
        """平台级汇总（管理员视图）"""
        all_balances = self.ledger.get_all_balances()
        total_pending = sum(b["pending_cny"] for b in all_balances)
        total_paid    = sum(b["paid_cny"] for b in all_balances)

        return {
            "creator_count":     len(all_balances),
            "total_pending_cny": round(total_pending, 2),
            "total_paid_cny":    round(total_paid, 2),
            "top_earners":       self.ledger.get_top_earners(5),
        }


# ════════════════════════════════════════════════════════
# 全局单例
# ════════════════════════════════════════════════════════

_ledger = CreatorLedger()
_calculator = RevenueCalculator()
_analyzer = ContributionAnalyzer(_ledger)

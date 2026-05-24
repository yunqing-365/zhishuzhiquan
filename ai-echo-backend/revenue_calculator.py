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

class CreatorLedger:
    """
    创作者收益总账
    记录每位创作者的累计收益、待结算金额、已结算金额

    生产环境建议替换为 PostgreSQL + 行级锁确保原子性
    """

    def __init__(self, persist_path: str = None):
        # {creator_id: {"pending": float, "paid": float, "records": [record_id,...]}}
        self._ledger: Dict[str, dict] = defaultdict(
            lambda: {"pending": 0.0, "paid": 0.0, "records": []}
        )
        self._records: Dict[str, RevenueRecord] = {}
        self._persist_path = persist_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "creator_ledger.json"
        )
        self._load()

    def add_records(self, records: List[RevenueRecord]):
        """入账新的分润记录"""
        for r in records:
            self._records[r.record_id] = r
            self._ledger[r.creator_id]["pending"] += r.creator_share
            self._ledger[r.creator_id]["records"].append(r.record_id)
        self._save()

    def get_balance(self, creator_id: str) -> dict:
        """查询创作者账户余额"""
        entry = self._ledger.get(creator_id, {"pending": 0.0, "paid": 0.0})
        return {
            "creator_id":  creator_id,
            "pending_cny": round(entry["pending"], 2),
            "paid_cny":    round(entry["paid"], 2),
            "total_earned": round(entry["pending"] + entry["paid"], 2),
        }

    def settle(self, creator_id: str) -> Optional[dict]:
        """
        触发结算：将 pending → paid
        低于最低结算阈值时拒绝
        """
        entry = self._ledger.get(creator_id)
        if not entry or entry["pending"] < PAYMENT_THRESHOLD:
            print(f"⚠️  {creator_id} 待结算金额 ¥{entry['pending'] if entry else 0:.2f} "
                  f"< 最低阈值 ¥{PAYMENT_THRESHOLD}，暂不结算")
            return None

        amount = entry["pending"]
        entry["paid"] += amount
        entry["pending"] = 0.0

        # 标记相关记录为已结算
        for rid in entry["records"]:
            if rid in self._records and self._records[rid].status == "pending":
                self._records[rid].status = "paid"
                self._records[rid].paid_at = datetime.utcnow()

        self._save()
        print(f"✅ 结算成功: {creator_id} 获得 ¥{amount:.2f}")
        return {
            "creator_id": creator_id,
            "settled_amount": round(amount, 2),
            "settled_at": datetime.utcnow().isoformat(),
        }

    def get_all_balances(self) -> List[dict]:
        """获取所有创作者余额（管理后台用）"""
        return [self.get_balance(cid) for cid in self._ledger]

    def get_top_earners(self, limit: int = 10) -> List[dict]:
        """收益排行榜"""
        balances = self.get_all_balances()
        return sorted(balances, key=lambda x: x["total_earned"], reverse=True)[:limit]

    def get_creator_records(self, creator_id: str) -> List[dict]:
        """查询创作者历史分润明细"""
        entry = self._ledger.get(creator_id, {})
        records = []
        for rid in entry.get("records", []):
            r = self._records.get(rid)
            if r:
                records.append({
                    "record_id":      r.record_id,
                    "package_id":     r.package_id,
                    "contribution":   f"{r.contribution_ratio:.1%}",
                    "sale_total":     r.total_revenue,
                    "creator_share":  r.creator_share,
                    "status":         r.status,
                    "created_at":     r.created_at.isoformat(),
                    "paid_at":        r.paid_at.isoformat() if r.paid_at else None,
                })
        return sorted(records, key=lambda x: x["created_at"], reverse=True)

    # ── 持久化 ──────────────────────────────────────────

    def _save(self):
        os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
        data = {
            "ledger": {
                cid: {
                    "pending": v["pending"],
                    "paid":    v["paid"],
                    "records": v["records"],
                }
                for cid, v in self._ledger.items()
            },
            "records": {
                rid: {
                    "record_id":         r.record_id,
                    "package_id":        r.package_id,
                    "creator_id":        r.creator_id,
                    "total_revenue":     r.total_revenue,
                    "contribution_ratio": r.contribution_ratio,
                    "creator_share":     r.creator_share,
                    "platform_fee":      r.platform_fee,
                    "status":            r.status,
                    "created_at":        r.created_at.isoformat(),
                    "paid_at":           r.paid_at.isoformat() if r.paid_at else None,
                }
                for rid, r in self._records.items()
            },
        }
        try:
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  账本持久化失败: {e}")

    def _load(self):
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                data = json.load(f)
            for cid, v in data.get("ledger", {}).items():
                self._ledger[cid] = v
            for rid, r in data.get("records", {}).items():
                rec = RevenueRecord(
                    record_id=r["record_id"],
                    package_id=r["package_id"],
                    creator_id=r["creator_id"],
                    total_revenue=r["total_revenue"],
                    contribution_ratio=r["contribution_ratio"],
                    creator_share=r["creator_share"],
                    platform_fee=r["platform_fee"],
                    status=r["status"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                    paid_at=datetime.fromisoformat(r["paid_at"]) if r["paid_at"] else None,
                )
                self._records[rid] = rec
        except Exception as e:
            print(f"⚠️  账本加载失败: {e}")


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

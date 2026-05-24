# dataset/human_review.py
"""
息壤 · 人工复核队列管理

解决的核心断链：
  annotator.py 将低置信度样本标记为 status='pending'
  但之前没有任何 API 让人类实际处理这些样本——断链！

本模块提供：
  - 取队列（按优先级：低分 pending 样本优先，方便快速通过高分的）
  - 单条审核（approve / reject / edit）
  - 批量操作（批量 approve 分数 >= 阈值的样本）
  - 审核统计（今日审核量、通过率、平均分提升）
  - 意见反馈回路（reviewer 的修改会计入再训练候选）

所有操作写入 SQLite review_actions 表，全程可审计。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import store.db as db


# ════════════════════════════════════════════════════════
# 复核队列读取
# ════════════════════════════════════════════════════════

class ReviewQueue:
    """
    人工复核队列

    取队逻辑：
      1. 先取 status='pending'（auto_review 模式低置信度的）
      2. 按 quality_score 升序排（最差的先审，避免高分样本卡在队列）
      3. 支持按 domain 过滤（让不同专家审不同领域）
    """

    # ── SFT 队列 ────────────────────────────────────────

    async def get_sft_queue(
        self,
        domain:  str = None,
        limit:   int = 20,
    ) -> dict:
        """获取待审核 SFT 样本队列"""
        conn = await db.get_db()
        where = "status='pending'"
        params: list = []
        if domain:
            where += " AND domain=?"
            params.append(domain)
        params.append(limit)

        async with conn.execute(
            f"""SELECT sample_id, instruction, input_context, output,
                       system_prompt, domain, difficulty, quality_score,
                       annotator_id, created_at
                FROM sft_samples
                WHERE {where}
                ORDER BY quality_score ASC
                LIMIT ?""",
            params
        ) as cur:
            rows = await cur.fetchall()

        total_pending = await self._count_pending_sft(domain)

        return {
            "queue_type":    "sft",
            "total_pending": total_pending,
            "returned":      len(rows),
            "items":         [dict(r) for r in rows],
        }

    async def _count_pending_sft(self, domain: str = None) -> int:
        conn = await db.get_db()
        where = "status='pending'"
        params = []
        if domain:
            where += " AND domain=?"
            params.append(domain)
        async with conn.execute(
            f"SELECT COUNT(*) as n FROM sft_samples WHERE {where}", params
        ) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    # ── DPO 队列 ────────────────────────────────────────

    async def get_dpo_queue(self, limit: int = 20) -> dict:
        """获取待审核 DPO 样本"""
        rows = await db.get_pending_dpo(limit)
        conn = await db.get_db()
        async with conn.execute(
            "SELECT COUNT(*) as n FROM dpo_samples WHERE status='pending'"
        ) as cur:
            cnt = await cur.fetchone()
        return {
            "queue_type":    "dpo",
            "total_pending": cnt["n"] if cnt else 0,
            "returned":      len(rows),
            "items":         rows,
        }


# ════════════════════════════════════════════════════════
# 单条审核操作
# ════════════════════════════════════════════════════════

class ReviewOperator:
    """执行单条/批量审核操作"""

    async def review_sft(
        self,
        sample_id:   str,
        action:      str,        # "approve" | "reject" | "edit"
        reviewer_id: str,
        new_output:  str = None,
        note:        str = "",
    ) -> dict:
        """
        审核单条 SFT 样本

        action:
          approve → status=approved（质量确认，进入数据集）
          reject  → status=rejected（不入库）
          edit    → 修改 output 后 status=reviewed（等待二次质检）
        """
        if action not in ("approve", "reject", "edit"):
            return {"success": False, "error": f"未知 action: {action}"}
        if action == "edit" and not new_output:
            return {"success": False, "error": "edit 操作必须提供 new_output"}

        ok = await db.update_sft_review(
            sample_id=sample_id,
            action=action,
            reviewer_id=reviewer_id,
            new_output=new_output,
            note=note,
        )
        return {
            "success":   ok,
            "sample_id": sample_id,
            "action":    action,
            "reviewer":  reviewer_id,
        }

    async def review_dpo(
        self,
        sample_id:   str,
        action:      str,
        reviewer_id: str,
        note:        str = "",
    ) -> dict:
        if action == "approve":
            new_status = "approved"
        elif action == "reject":
            new_status = "rejected"
        else:
            return {"success": False, "error": f"DPO 暂不支持 action: {action}"}

        await db.update_dpo_status(sample_id, new_status)

        # 写日志
        conn = await db.get_db()
        await conn.execute(
            """INSERT INTO review_actions
               (action_id,sample_id,sample_type,reviewer_id,action,note,
                old_output,new_output,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), sample_id, "dpo", reviewer_id, action,
             note, "", "", datetime.utcnow().isoformat())
        )
        await conn.commit()

        return {"success": True, "sample_id": sample_id, "action": action}

    async def batch_approve_sft(
        self,
        reviewer_id:    str,
        min_auto_score: float = 7.5,
        domain:         str = None,
        limit:          int = 500,
    ) -> dict:
        """
        批量通过高分 pending 样本（分数 >= min_auto_score 的无需逐条人工检查）
        适合处理自动标注置信度高但仍走了 auto_review 流程的批量样本
        """
        conn = await db.get_db()
        where = "status='pending' AND quality_score >= ?"
        params: list = [min_auto_score]
        if domain:
            where += " AND domain=?"
            params.append(domain)
        params.append(limit)

        async with conn.execute(
            f"SELECT sample_id FROM sft_samples WHERE {where} LIMIT ?", params
        ) as cur:
            rows = await cur.fetchall()

        sample_ids = [r["sample_id"] for r in rows]
        if not sample_ids:
            return {"approved": 0, "message": "没有符合条件的样本"}

        now = datetime.utcnow().isoformat()
        await conn.executemany(
            "UPDATE sft_samples SET status='approved', reviewed_by=?, updated_at=? WHERE sample_id=?",
            [(reviewer_id, now, sid) for sid in sample_ids]
        )
        # 批量写日志
        await conn.executemany(
            """INSERT INTO review_actions
               (action_id,sample_id,sample_type,reviewer_id,action,note,
                old_output,new_output,created_at)
               VALUES (?,?,'sft',?,'approve','batch_auto_approve','','',?)""",
            [(str(uuid.uuid4()), sid, reviewer_id, now) for sid in sample_ids]
        )
        await conn.commit()

        return {
            "approved":   len(sample_ids),
            "min_score":  min_auto_score,
            "reviewer":   reviewer_id,
            "message":    f"批量通过 {len(sample_ids)} 条 SFT 样本",
        }


# ════════════════════════════════════════════════════════
# 审核统计
# ════════════════════════════════════════════════════════

class ReviewStats:
    """审核效率和质量统计"""

    async def daily_stats(self, reviewer_id: str = None) -> dict:
        """今日审核统计"""
        conn  = await db.get_db()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        where = "created_at >= ?"
        params: list = [today]
        if reviewer_id:
            where += " AND reviewer_id=?"
            params.append(reviewer_id)

        async with conn.execute(
            f"""SELECT
                  COUNT(*) as total,
                  SUM(CASE WHEN action='approve' THEN 1 ELSE 0 END) as approved,
                  SUM(CASE WHEN action='reject'  THEN 1 ELSE 0 END) as rejected,
                  SUM(CASE WHEN action='edit'    THEN 1 ELSE 0 END) as edited
                FROM review_actions WHERE {where}""",
            params
        ) as cur:
            row = await cur.fetchone()

        total     = row["total"] or 0
        approved  = row["approved"] or 0
        rejected  = row["rejected"] or 0
        edited    = row["edited"] or 0
        pass_rate = round(approved / total, 3) if total else 0.0

        return {
            "date":       today,
            "reviewer_id": reviewer_id or "all",
            "total":      total,
            "approved":   approved,
            "rejected":   rejected,
            "edited":     edited,
            "pass_rate":  pass_rate,
        }

    async def queue_overview(self) -> dict:
        """队列全貌（管理员视图）"""
        conn = await db.get_db()

        async def _count(table, where="1=1"):
            async with conn.execute(
                f"SELECT COUNT(*) as n FROM {table} WHERE {where}"
            ) as cur:
                r = await cur.fetchone()
            return r["n"] if r else 0

        return {
            "sft_pending":      await _count("sft_samples",    "status='pending'"),
            "sft_approved":     await _count("sft_samples",    "status='approved'"),
            "sft_rejected":     await _count("sft_samples",    "status='rejected'"),
            "dpo_pending":      await _count("dpo_samples",    "status='pending'"),
            "dpo_approved":     await _count("dpo_samples",    "status='approved'"),
            "pretrain_pending": await _count("pretrain_chunks","status='pending'"),
            "pretrain_approved":await _count("pretrain_chunks","status='approved'"),
            "total_actions":    await _count("review_actions"),
        }

    async def reviewer_leaderboard(self, days: int = 7) -> list:
        """审核员排行榜（过去 N 天审核量）"""
        conn  = await db.get_db()
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        async with conn.execute(
            """SELECT reviewer_id,
                      COUNT(*) as total,
                      SUM(CASE WHEN action='approve' THEN 1 ELSE 0 END) as approved
               FROM review_actions
               WHERE created_at >= ?
               GROUP BY reviewer_id
               ORDER BY total DESC
               LIMIT 20""",
            (since,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════
# 全局单例
# ════════════════════════════════════════════════════════

review_queue    = ReviewQueue()
review_operator = ReviewOperator()
review_stats    = ReviewStats()

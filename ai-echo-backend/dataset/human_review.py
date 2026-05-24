# dataset/human_review.py
"""
知数知圈 · 人工复核队列

低分样本 / 有争议样本进入人工复核队列，
管理员通过后台界面审核并标记 approve / reject。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional


_QUEUE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "human_review_queue.json"
)


class HumanReviewQueue:
    """人工复核队列（JSON 文件持久化，生产可换 DB）"""

    def __init__(self):
        self._queue: Dict[str, dict] = {}
        self._load()

    def enqueue(
        self,
        sample_id:   str,
        sample_type: str,       # sft / dpo / pretrain
        content:     dict,
        reason:      str = "",
        score:       float = 0.0,
    ) -> str:
        """将样本加入复核队列，返回 review_id"""
        review_id = str(uuid.uuid4())
        self._queue[review_id] = {
            "review_id":   review_id,
            "sample_id":   sample_id,
            "sample_type": sample_type,
            "content":     content,
            "reason":      reason,
            "score":       score,
            "status":      "pending",    # pending / approved / rejected
            "reviewer":    None,
            "reviewed_at": None,
            "created_at":  datetime.utcnow().isoformat(),
        }
        self._save()
        return review_id

    def approve(self, review_id: str, reviewer: str = "admin") -> bool:
        if review_id not in self._queue:
            return False
        self._queue[review_id]["status"] = "approved"
        self._queue[review_id]["reviewer"] = reviewer
        self._queue[review_id]["reviewed_at"] = datetime.utcnow().isoformat()
        self._save()
        return True

    def reject(self, review_id: str, reviewer: str = "admin") -> bool:
        if review_id not in self._queue:
            return False
        self._queue[review_id]["status"] = "rejected"
        self._queue[review_id]["reviewer"] = reviewer
        self._queue[review_id]["reviewed_at"] = datetime.utcnow().isoformat()
        self._save()
        return True

    def list_pending(self) -> List[dict]:
        return [v for v in self._queue.values() if v["status"] == "pending"]

    def list_all(self, limit: int = 100) -> List[dict]:
        items = sorted(self._queue.values(), key=lambda x: x["created_at"], reverse=True)
        return items[:limit]

    def get(self, review_id: str) -> Optional[dict]:
        return self._queue.get(review_id)

    def _save(self):
        os.makedirs(os.path.dirname(_QUEUE_PATH), exist_ok=True)
        try:
            with open(_QUEUE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  复核队列保存失败: {e}")

    def _load(self):
        if not os.path.exists(_QUEUE_PATH):
            return
        try:
            with open(_QUEUE_PATH, encoding="utf-8") as f:
                self._queue = json.load(f)
        except Exception:
            self._queue = {}

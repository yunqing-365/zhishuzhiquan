# dataset/versioning.py
"""
知数知圈 · 数据集版本管理

记录每次数据集生产的版本信息，支持版本对比和回溯。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional


_VERSION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "versions"
)
os.makedirs(_VERSION_DIR, exist_ok=True)


class DatasetVersionManager:
    """数据集版本管理器"""

    def create_version(
        self,
        package_id: str,
        name: str,
        version: str = None,
        changelog: str = "",
        metadata: dict = None,
    ) -> dict:
        """创建新版本记录"""
        version_id = str(uuid.uuid4())
        record = {
            "version_id":  version_id,
            "package_id":  package_id,
            "name":        name,
            "version":     version or self._next_version(name),
            "changelog":   changelog,
            "metadata":    metadata or {},
            "created_at":  datetime.utcnow().isoformat(),
        }
        path = os.path.join(_VERSION_DIR, f"{version_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record

    def list_versions(self, name: str = None) -> List[dict]:
        """列出所有版本（可按名称过滤）"""
        versions = []
        for fname in sorted(os.listdir(_VERSION_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(_VERSION_DIR, fname), encoding="utf-8") as f:
                    v = json.load(f)
                if name is None or v.get("name") == name:
                    versions.append(v)
            except Exception:
                pass
        return versions

    def get_version(self, version_id: str) -> Optional[dict]:
        path = os.path.join(_VERSION_DIR, f"{version_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _next_version(self, name: str) -> str:
        existing = self.list_versions(name)
        if not existing:
            return "1.0.0"
        latest = existing[0].get("version", "1.0.0")
        parts = latest.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except Exception:
            parts = ["1", "0", "0"]
        return ".".join(parts)

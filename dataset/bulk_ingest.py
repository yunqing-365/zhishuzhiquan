# dataset/bulk_ingest.py
"""
息壤 · 批量素材导入引擎

支持三种导入方式：
  1. ZIP 包  —— 解压后按文件类型自动识别（txt/md/json/jpg/png）
  2. 纯文本文件 —— 按段落/章节自动切分为多条 CreatorMaterial
  3. JSONL 文件 —— 每行一个 {"content":..., "domain":..., "creator_id":...}

每条素材自动计算 content_hash，去除与库中已有素材重复的内容（精确哈希）。
导入完成后返回摘要（总数/入库/去重跳过/失败）。

使用方式：
  POST /api/dataset/bulk-ingest/zip       上传 ZIP 文件 (multipart/form-data)
  POST /api/dataset/bulk-ingest/text      上传纯文本文件
  POST /api/dataset/bulk-ingest/jsonl     上传 JSONL 文件
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from typing import List, Tuple

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataset.schema import CreatorMaterial
import store.db as db

# 支持的文本扩展名
_TEXT_EXTS  = {".txt", ".md", ".rst", ".text"}
_JSON_EXTS  = {".json", ".jsonl"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# 单条素材最大字节数（防止单条素材过大撑爆内存）
_MAX_BYTES  = 512 * 1024   # 512 KB


# ════════════════════════════════════════════════════════
# 核心导入逻辑
# ════════════════════════════════════════════════════════

class BulkIngestor:

    def __init__(self, creator_id: str, domain: str = ""):
        self.creator_id = creator_id
        self.domain     = domain

    # ── ZIP 包 ─────────────────────────────────────────

    async def ingest_zip(self, zip_bytes: bytes) -> dict:
        """解压 ZIP，按文件类型分发到各处理器"""
        materials: List[CreatorMaterial] = []
        skipped_names = []

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    ext  = os.path.splitext(name.lower())[1]
                    if info.file_size > _MAX_BYTES:
                        skipped_names.append(f"{name}(过大)")
                        continue
                    raw = zf.read(name)

                    if ext in _TEXT_EXTS:
                        text = raw.decode("utf-8", errors="ignore")
                        mats = self._split_text(text, source_path=name)
                        materials.extend(mats)
                    elif ext in _IMAGE_EXTS:
                        import base64
                        b64 = base64.b64encode(raw).decode()
                        mat = CreatorMaterial(
                            creator_id    = self.creator_id,
                            raw_content   = b64,
                            material_type = "image",
                            source_path   = name,
                            metadata      = {"domain": self.domain},
                        )
                        mat.compute_hash()
                        materials.append(mat)
                    elif ext == ".jsonl":
                        text = raw.decode("utf-8", errors="ignore")
                        mats = self._parse_jsonl(text, source_path=name)
                        materials.extend(mats)
                    else:
                        skipped_names.append(name)
        except zipfile.BadZipFile as e:
            return {"error": f"无效 ZIP 文件: {e}"}

        return await self._persist(materials, skipped=skipped_names)

    # ── 纯文本文件 ───────────────────────────────────────

    async def ingest_text(self, text: str, filename: str = "") -> dict:
        materials = self._split_text(text, source_path=filename)
        return await self._persist(materials)

    # ── JSONL 文件 ───────────────────────────────────────

    async def ingest_jsonl(self, text: str, filename: str = "") -> dict:
        materials = self._parse_jsonl(text, source_path=filename)
        return await self._persist(materials)

    # ── 文本切分 ─────────────────────────────────────────

    def _split_text(self, text: str, source_path: str = "") -> List[CreatorMaterial]:
        """
        将长文本切分为段落级素材。
        策略：
          1. 先尝试按章节（# / 第X章 / 卷）切分
          2. 无章节标记则按双换行切段落，合并到 ~800 汉字
        """
        # 尝试章节切分
        chapter_pat = re.compile(
            r'(?m)^(?:#{1,3}\s+.+|第[一二三四五六七八九十百千\d]+[章节卷回].{0,30})$'
        )
        chapters = chapter_pat.split(text)
        titles   = chapter_pat.findall(text)

        materials = []
        if len(chapters) > 2:
            # 有章节结构
            for i, chunk in enumerate(chapters):
                chunk = chunk.strip()
                if len(chunk) < 50:
                    continue
                title = titles[i - 1] if i > 0 and i - 1 < len(titles) else ""
                content = f"{title}\n{chunk}".strip() if title else chunk
                mat = self._make_mat(content, source_path, "text")
                if mat:
                    materials.append(mat)
        else:
            # 无章节，按段落合并
            paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
            buf = ""
            for para in paragraphs:
                if len(buf) + len(para) < 800:
                    buf += para + "\n"
                else:
                    if buf.strip():
                        mat = self._make_mat(buf.strip(), source_path, "text")
                        if mat:
                            materials.append(mat)
                    buf = para + "\n"
            if buf.strip():
                mat = self._make_mat(buf.strip(), source_path, "text")
                if mat:
                    materials.append(mat)

        return materials

    def _parse_jsonl(self, text: str, source_path: str = "") -> List[CreatorMaterial]:
        """
        解析 JSONL，支持字段：
          content / text / raw_content   → raw_content
          domain                         → metadata.domain
          creator_id                     → 覆盖默认 creator_id（可选）
          material_type                  → 默认 text
        """
        materials = []
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            content = (obj.get("content") or obj.get("text") or
                       obj.get("raw_content") or "").strip()
            if not content:
                continue

            creator = obj.get("creator_id", self.creator_id)
            domain  = obj.get("domain", self.domain)
            mtype   = obj.get("material_type", "text")

            mat = CreatorMaterial(
                creator_id    = creator,
                raw_content   = content,
                material_type = mtype,
                source_path   = f"{source_path}:L{lineno}",
                metadata      = {"domain": domain},
            )
            mat.compute_hash()
            materials.append(mat)
        return materials

    def _make_mat(self, content: str, source: str,
                  mtype: str = "text") -> CreatorMaterial | None:
        if len(content) < 30:
            return None
        mat = CreatorMaterial(
            creator_id    = self.creator_id,
            raw_content   = content,
            material_type = mtype,
            source_path   = source,
            metadata      = {"domain": self.domain},
        )
        mat.compute_hash()
        return mat

    # ── 持久化（精确去重）────────────────────────────────

    async def _persist(
        self,
        materials: List[CreatorMaterial],
        skipped:   List[str] = None,
    ) -> dict:
        if not materials:
            return {
                "total": 0, "inserted": 0,
                "deduped": 0, "failed": 0,
                "skipped_files": skipped or [],
            }

        conn    = await db.get_db()
        inserted = 0
        deduped  = 0
        failed   = 0

        # 批量查已有 hash（一次 SQL 完成，避免 N+1）
        hashes   = [m.content_hash for m in materials]
        placeholders = ",".join("?" * len(hashes))
        async with conn.execute(
            f"SELECT content_hash FROM materials WHERE content_hash IN ({placeholders})",
            hashes
        ) as cur:
            rows = await cur.fetchall()
        existing_hashes = {r["content_hash"] for r in rows}

        # 过滤新素材
        new_mats = [m for m in materials if m.content_hash not in existing_hashes]
        deduped  = len(materials) - len(new_mats)

        # 批量插入
        if new_mats:
            from datetime import datetime
            now = datetime.utcnow().isoformat()
            rows_to_insert = []
            for m in new_mats:
                try:
                    rows_to_insert.append((
                        m.material_id, m.creator_id, m.raw_content,
                        m.material_type, m.source_path,
                        m.metadata.get("domain", ""),
                        json.dumps(m.metadata, ensure_ascii=False),
                        m.content_hash, now,
                    ))
                except Exception:
                    failed += 1

            try:
                await conn.executemany(
                    """INSERT OR IGNORE INTO materials
                       (material_id,creator_id,raw_content,material_type,source_path,
                        domain,metadata_json,content_hash,uploaded_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    rows_to_insert,
                )
                await conn.commit()
                inserted = len(rows_to_insert) - failed
            except Exception as e:
                failed = len(rows_to_insert)
                return {"error": f"批量写入失败: {e}"}

        return {
            "total":         len(materials),
            "inserted":      inserted,
            "deduped":       deduped,
            "failed":        failed,
            "skipped_files": skipped or [],
            "material_ids":  [m.material_id for m in new_mats[:20]],  # 前20个ID
        }

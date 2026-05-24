# dataset/versioning.py
"""
息壤 · 数据集版本管理

企业客户购买的数据集需要可追溯：
  - 同一数据集多次更新（增量补充、质量修复）能区分版本
  - 每个版本有独立的 ZIP 和 data_card
  - 可以查看两个版本之间的差异（新增/删除/修改的样本数）
  - 支持语义化版本号 (semver: MAJOR.MINOR.PATCH)

版本号规则：
  PATCH  (+0.0.1): 质量修复，已有样本 output 被修正
  MINOR  (+0.1.0): 增量新增样本，不删除
  MAJOR  (+1.0.0): 重构，有样本被删除或重大结构变化

SQLite 新表：
  package_versions    版本元信息
  version_sample_map  版本 ↔ 样本 ID 映射（记录每个版本包含哪些样本）
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import store.db as _db

_VERSIONING_SCHEMA = """
CREATE TABLE IF NOT EXISTS package_versions (
    version_id     TEXT PRIMARY KEY,
    package_id     TEXT NOT NULL,
    version_tag    TEXT NOT NULL,
    changelog      TEXT DEFAULT '',
    bump_type      TEXT DEFAULT 'minor',
    total_samples  INTEGER DEFAULT 0,
    avg_quality    REAL DEFAULT 0.0,
    export_zip     TEXT DEFAULT '',
    created_by     TEXT DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS version_sample_map (
    version_id  TEXT NOT NULL,
    sample_id   TEXT NOT NULL,
    sample_type TEXT NOT NULL,
    PRIMARY KEY (version_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_pkg_versions ON package_versions(package_id);
"""

_SEMVER_BUMPS = {"major": 0, "minor": 1, "patch": 2}


def _bump_version(current: str, bump_type: str) -> str:
    """'1.2.3' + 'minor' → '1.3.0'"""
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    idx = _SEMVER_BUMPS.get(bump_type, 1)
    parts[idx] += 1
    for i in range(idx + 1, 3):
        parts[i] = 0
    return ".".join(str(p) for p in parts)


async def ensure_versioning_schema():
    db = await _db.get_db()
    await db.executescript(_VERSIONING_SCHEMA)
    await db.commit()


# ════════════════════════════════════════════════════════
# 创建新版本
# ════════════════════════════════════════════════════════

async def create_version(
    package_id: str,
    bump_type:  str,       # "major" | "minor" | "patch"
    changelog:  str,
    created_by: str = "system",
) -> dict:
    """
    基于当前已审批的样本创建新版本快照。
    自动打包、更新 ZIP，版本号自动递增。
    """
    import uuid
    db  = await _db.get_db()
    now = datetime.utcnow().isoformat()

    # 取最新版本号
    async with db.execute(
        "SELECT version_tag FROM package_versions WHERE package_id=? ORDER BY created_at DESC LIMIT 1",
        (package_id,)
    ) as cur:
        row = await cur.fetchone()
    current_tag = row["version_tag"] if row else "0.0.0"
    new_tag     = _bump_version(current_tag, bump_type)

    # 收集当前 approved 样本
    sft_rows     = await _db.list_sft(status="approved", limit=5000)
    dpo_rows     = await _db.list_dpo(status="approved", limit=5000)
    pretrain_rows = await _db.list_pretrain(status="approved", limit=10000)

    all_sample_ids = (
        [(r["sample_id"], "sft")     for r in sft_rows] +
        [(r["sample_id"], "dpo")     for r in dpo_rows] +
        [(r["chunk_id"],  "pretrain") for r in pretrain_rows]
    )
    total = len(all_sample_ids)

    avg_q = 0.0
    all_scores = (
        [r["quality_score"] for r in sft_rows if r["quality_score"]] +
        [r["quality_score"] for r in dpo_rows if r["quality_score"]] +
        [r["quality_score"] for r in pretrain_rows if r["quality_score"]]
    )
    if all_scores:
        avg_q = round(sum(all_scores) / len(all_scores), 2)

    # 重新打包
    pkg_data = await _db.get_package(package_id)
    if not pkg_data:
        return {"error": f"包 {package_id} 不存在"}

    from dataset.schema import (
        SFTSample, DPOSample, PretrainChunk,
        AnnotationStatus, QualityTier,
    )
    from dataset.packager import DatasetPackager

    def _rebuild_sft(r):
        s = SFTSample(
            sample_id=r["sample_id"], material_id=r.get("material_id",""),
            creator_id=r["creator_id"], system_prompt=r.get("system_prompt",""),
            instruction=r["instruction"], input_context=r.get("input_context",""),
            output=r["output"], domain=r.get("domain",""),
            difficulty=r.get("difficulty",3),
            quality_score=r.get("quality_score",0),
            quality_tier=QualityTier(r.get("quality_tier","bronze")),
        )
        s.status = AnnotationStatus.APPROVED
        return s

    def _rebuild_dpo(r):
        d = DPOSample(
            sample_id=r["sample_id"], material_id=r.get("material_id",""),
            creator_id=r["creator_id"], prompt=r["prompt"],
            chosen=r["chosen"], rejected=r["rejected"],
            preference_reason=r.get("preference_reason",""),
            quality_score=r.get("quality_score",0),
        )
        d.status = AnnotationStatus.APPROVED
        return d

    def _rebuild_pretrain(r):
        c = PretrainChunk(
            chunk_id=r["chunk_id"], material_id=r.get("material_id",""),
            creator_id=r["creator_id"], text=r["text"],
            token_count=r.get("token_count",0),
            quality_score=r.get("quality_score",0),
        )
        c.status = AnnotationStatus.APPROVED
        return c

    sft_objs     = [_rebuild_sft(r) for r in sft_rows]
    dpo_objs     = [_rebuild_dpo(r) for r in dpo_rows]
    pretrain_objs = [_rebuild_pretrain(r) for r in pretrain_rows]

    packager = DatasetPackager()
    pkg_name = f"{pkg_data['name']}_v{new_tag}"
    package  = packager.pack(
        name           = pkg_name,
        description    = f"{pkg_data.get('description','')} [版本 {new_tag}]",
        sft_samples    = sft_objs,
        dpo_samples    = dpo_objs,
        pretrain_chunks = pretrain_objs,
        formats        = ["jsonl", "zip"],
        min_quality    = 0.0,   # 版本快照包含所有 approved 样本
        price_cny      = pkg_data.get("price_cny", 0),
    )

    zip_path = package.export_paths.get("zip", "")

    # 写版本记录
    vid = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO package_versions
           (version_id,package_id,version_tag,changelog,bump_type,
            total_samples,avg_quality,export_zip,created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (vid, package_id, new_tag, changelog, bump_type,
         total, avg_q, zip_path, created_by, now)
    )

    # 写样本映射（批量）
    if all_sample_ids:
        await db.executemany(
            "INSERT OR IGNORE INTO version_sample_map (version_id,sample_id,sample_type) VALUES (?,?,?)",
            [(vid, sid, stype) for sid, stype in all_sample_ids]
        )

    await db.commit()

    print(f"📌 版本 {new_tag} 创建完成: {total} 样本 | 平均质量 {avg_q} | ZIP: {zip_path}")
    return {
        "version_id":    vid,
        "package_id":    package_id,
        "version_tag":   new_tag,
        "prev_version":  current_tag,
        "bump_type":     bump_type,
        "changelog":     changelog,
        "total_samples": total,
        "avg_quality":   avg_q,
        "zip_path":      zip_path,
        "created_at":    now,
    }


# ════════════════════════════════════════════════════════
# 版本列表 & 对比
# ════════════════════════════════════════════════════════

async def list_versions(package_id: str) -> list:
    db = await _db.get_db()
    async with db.execute(
        """SELECT * FROM package_versions
           WHERE package_id=? ORDER BY created_at DESC""",
        (package_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def diff_versions(version_id_a: str, version_id_b: str) -> dict:
    """
    对比两个版本的样本集合差异。
    返回：新增样本数 / 删除样本数 / 质量变化
    """
    db = await _db.get_db()

    async def _get_ids(vid):
        async with db.execute(
            "SELECT sample_id, sample_type FROM version_sample_map WHERE version_id=?", (vid,)
        ) as cur:
            rows = await cur.fetchall()
        return {r["sample_id"]: r["sample_type"] for r in rows}

    async def _get_meta(vid):
        async with db.execute(
            "SELECT * FROM package_versions WHERE version_id=?", (vid,)
        ) as cur:
            r = await cur.fetchone()
        return dict(r) if r else {}

    ids_a    = await _get_ids(version_id_a)
    ids_b    = await _get_ids(version_id_b)
    meta_a   = await _get_meta(version_id_a)
    meta_b   = await _get_meta(version_id_b)

    set_a, set_b = set(ids_a), set(ids_b)
    added        = set_b - set_a
    removed      = set_a - set_b
    common       = set_a & set_b

    # 按类型拆分
    def _by_type(id_set, id_map):
        counts = {}
        for sid in id_set:
            t = id_map.get(sid, "unknown")
            counts[t] = counts.get(t, 0) + 1
        return counts

    return {
        "version_a":     meta_a.get("version_tag", version_id_a),
        "version_b":     meta_b.get("version_tag", version_id_b),
        "added":         len(added),
        "removed":       len(removed),
        "unchanged":     len(common),
        "added_by_type": _by_type(added, ids_b),
        "removed_by_type": _by_type(removed, ids_a),
        "quality_change": round(
            (meta_b.get("avg_quality", 0) or 0) -
            (meta_a.get("avg_quality", 0) or 0), 2
        ),
        "sample_change": (meta_b.get("total_samples", 0) or 0) -
                         (meta_a.get("total_samples", 0) or 0),
    }

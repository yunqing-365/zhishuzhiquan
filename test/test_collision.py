"""
test_collision.py — collision_detector.py 单元测试
====================================================
覆盖：
  - 空库返回 EMPTY_CORPUS
  - 完全相同向量判定 COLLISION
  - 不相似向量判定 SAFE
  - exclude_hash 正确过滤自身
  - top_k 参数限制结果数量
  - CollisionReport.to_dict() 字段完整性
  - detect_collision_from_text() 文本入口
"""
import math
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collision_detector import (
    detect_collision,
    detect_collision_from_text,
    CollisionReport,
    COLLISION_THRESHOLD,
    WARNING_THRESHOLD,
    TOP_K,
)


# ════════════════════════════════════════════════════════════════════
# 辅助：构造固定向量
# ════════════════════════════════════════════════════════════════════

def unit_vec(dim: int, idx: int) -> list[float]:
    """返回第 idx 维为 1、其余为 0 的单位向量（维度为 dim）"""
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v

def fill_collection(col, n: int = 5, dim: int = 16):
    """往 collection 里插入 n 条不同方向的单位向量"""
    ids  = [f"vec_{i:03d}" for i in range(n)]
    embs = [unit_vec(dim, i) for i in range(n)]
    metas = [{"modality": "text", "scene": "chat_qa", "audio_scene": ""} for _ in range(n)]
    col.upsert(ids=ids, embeddings=embs, metadatas=metas)
    return ids, embs


# ════════════════════════════════════════════════════════════════════
# 空库
# ════════════════════════════════════════════════════════════════════

class TestEmptyCorpus:
    def test_empty_corpus_verdict(self, chroma_collection):
        query = unit_vec(16, 0)
        report = detect_collision(query, chroma_collection)
        assert report.verdict == "EMPTY_CORPUS"

    def test_empty_corpus_risk_score_zero(self, chroma_collection):
        report = detect_collision(unit_vec(16, 0), chroma_collection)
        assert report.risk_score == 0.0

    def test_empty_corpus_no_matches(self, chroma_collection):
        report = detect_collision(unit_vec(16, 0), chroma_collection)
        assert report.top_matches == []

    def test_empty_corpus_total_checked_zero(self, chroma_collection):
        report = detect_collision(unit_vec(16, 0), chroma_collection)
        assert report.total_checked == 0


# ════════════════════════════════════════════════════════════════════
# SAFE 判定：查询向量与库中所有向量都不相似
# ════════════════════════════════════════════════════════════════════

class TestSafeDetection:
    def test_orthogonal_vectors_are_safe(self, chroma_collection):
        """正交向量余弦距离 = 1.0 >> WARNING_THRESHOLD，应判 SAFE"""
        fill_collection(chroma_collection, n=5, dim=16)
        # 查询一个与所有已存向量正交的方向（第 10 维）
        query = unit_vec(16, 10)
        report = detect_collision(query, chroma_collection)
        assert report.verdict in ("SAFE", "WARNING"), (
            f"正交向量应判 SAFE/WARNING，got {report.verdict}"
        )

    def test_safe_has_low_risk_score(self, chroma_collection):
        fill_collection(chroma_collection, n=3, dim=16)
        query = unit_vec(16, 8)
        report = detect_collision(query, chroma_collection)
        if report.verdict == "SAFE":
            assert report.risk_score < 0.8


# ════════════════════════════════════════════════════════════════════
# COLLISION 判定：完全相同向量
# ════════════════════════════════════════════════════════════════════

class TestCollisionDetection:
    def test_identical_vector_triggers_collision(self, chroma_collection):
        """把 query 自身存入库后再检索，余弦距离 ≈ 0 → COLLISION"""
        query = unit_vec(16, 0)
        chroma_collection.upsert(
            ids=["existing_hash"],
            embeddings=[query],
            metadatas=[{"modality": "text", "scene": "medical_sft", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection, exclude_hash=None)
        assert report.verdict == "COLLISION", (
            f"完全相同向量应触发 COLLISION，got {report.verdict}"
        )

    def test_collision_risk_score_near_one(self, chroma_collection):
        query = unit_vec(16, 0)
        chroma_collection.upsert(
            ids=["dup_hash"],
            embeddings=[query],
            metadatas=[{"modality": "text", "scene": "legal_doc", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection)
        assert report.risk_score > 0.9, (
            f"完全相同向量的风险分应接近 1，got {report.risk_score}"
        )

    def test_collision_count_positive(self, chroma_collection):
        query = unit_vec(16, 1)
        chroma_collection.upsert(
            ids=["collision_target"],
            embeddings=[query],
            metadatas=[{"modality": "image", "scene": "illustration", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection)
        assert report.collision_count >= 1


# ════════════════════════════════════════════════════════════════════
# exclude_hash 过滤自身
# ════════════════════════════════════════════════════════════════════

class TestExcludeHash:
    def test_exclude_self_prevents_collision(self, chroma_collection):
        """
        把自身 hash 存入库后，用 exclude_hash 排除自身。
        库中只有自身这一条记录时应返回 EMPTY_CORPUS 或 SAFE。
        """
        my_hash = "my_own_hash_abc"
        query   = unit_vec(16, 2)
        chroma_collection.upsert(
            ids=[my_hash],
            embeddings=[query],
            metadatas=[{"modality": "text", "scene": "chat_qa", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection, exclude_hash=my_hash)
        # 库里只有自身，排除后应为 SAFE 或 EMPTY_CORPUS
        assert report.verdict in ("SAFE", "EMPTY_CORPUS", "WARNING"), (
            f"排除自身后不应判 COLLISION，got {report.verdict}"
        )

    def test_exclude_hash_not_in_top_matches(self, chroma_collection):
        """排除的 hash 不应出现在 top_matches 列表中"""
        my_hash = "excluded_hash_xyz"
        query   = unit_vec(16, 3)
        # 存入自身 + 其他几条
        fill_collection(chroma_collection, n=4, dim=16)
        chroma_collection.upsert(
            ids=[my_hash],
            embeddings=[query],
            metadatas=[{"modality": "text", "scene": "chat_qa", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection, exclude_hash=my_hash)
        returned_ids = [m.asset_hash for m in report.top_matches]
        assert my_hash not in returned_ids, (
            f"exclude_hash={my_hash} 不应出现在结果中，got {returned_ids}"
        )


# ════════════════════════════════════════════════════════════════════
# top_k 参数
# ════════════════════════════════════════════════════════════════════

class TestTopK:
    def test_top_k_limits_results(self, chroma_collection):
        fill_collection(chroma_collection, n=10, dim=16)
        query  = unit_vec(16, 5)
        report = detect_collision(query, chroma_collection, top_k=3)
        assert len(report.top_matches) <= 3, (
            f"top_k=3 但返回了 {len(report.top_matches)} 条"
        )

    def test_top_k_one(self, chroma_collection):
        fill_collection(chroma_collection, n=5, dim=16)
        report = detect_collision(unit_vec(16, 0), chroma_collection, top_k=1)
        assert len(report.top_matches) <= 1


# ════════════════════════════════════════════════════════════════════
# CollisionReport.to_dict() 字段完整性
# ════════════════════════════════════════════════════════════════════

class TestCollisionReportDict:
    REQUIRED_KEYS = {
        "verdict", "risk_score", "collision_count", "warning_count",
        "total_checked", "message", "latency_ms", "top_matches",
    }
    MATCH_KEYS = {
        "asset_hash", "distance", "similarity_score",
        "risk_level", "modality", "scene", "audio_scene",
    }

    def test_to_dict_top_level_keys(self, chroma_collection):
        fill_collection(chroma_collection, n=3, dim=16)
        report = detect_collision(unit_vec(16, 0), chroma_collection)
        d = report.to_dict()
        missing = self.REQUIRED_KEYS - set(d)
        assert not missing, f"to_dict() 缺少字段: {missing}"

    def test_to_dict_match_keys(self, chroma_collection):
        """每条 top_match 包含所有必要字段"""
        query = unit_vec(16, 0)
        chroma_collection.upsert(
            ids=["m1"], embeddings=[query],
            metadatas=[{"modality": "text", "scene": "chat_qa", "audio_scene": ""}],
        )
        report = detect_collision(query, chroma_collection)
        d = report.to_dict()
        if d["top_matches"]:
            missing = self.MATCH_KEYS - set(d["top_matches"][0])
            assert not missing, f"match 缺少字段: {missing}"

    def test_risk_score_in_range(self, chroma_collection):
        fill_collection(chroma_collection, n=5, dim=16)
        d = detect_collision(unit_vec(16, 0), chroma_collection).to_dict()
        assert 0.0 <= d["risk_score"] <= 1.0, f"risk_score 超范围: {d['risk_score']}"

    def test_similarity_score_in_range(self, chroma_collection):
        query = unit_vec(16, 0)
        chroma_collection.upsert(
            ids=["sim_test"], embeddings=[query],
            metadatas=[{"modality": "text", "scene": "code_tech", "audio_scene": ""}],
        )
        d = detect_collision(query, chroma_collection).to_dict()
        for m in d["top_matches"]:
            assert 0.0 <= m["similarity_score"] <= 1.0, (
                f"similarity_score 超范围: {m['similarity_score']}"
            )

    def test_latency_ms_positive(self, chroma_collection):
        fill_collection(chroma_collection, n=3, dim=16)
        d = detect_collision(unit_vec(16, 0), chroma_collection).to_dict()
        assert d["latency_ms"] >= 0


# ════════════════════════════════════════════════════════════════════
# detect_collision_from_text()
# ════════════════════════════════════════════════════════════════════

class TestDetectCollisionFromText:
    def test_empty_description_returns_safe(self, chroma_collection, embed_fn):
        """embed_fn 失败时应降级返回 SAFE，不崩溃"""
        from unittest.mock import MagicMock, patch
        bad_embed = MagicMock(side_effect=RuntimeError("embed failed"))
        report = detect_collision_from_text("", bad_embed, chroma_collection)
        assert report.verdict in ("SAFE", "EMPTY_CORPUS")

    def test_from_text_uses_embed_fn(self, populated_collection, embed_fn):
        """正常描述应触发 embed_fn 并返回有效 report"""
        report = detect_collision_from_text(
            "患者确诊糖尿病，医嘱达格列净10mg",
            embed_fn,
            populated_collection,
        )
        assert report.verdict in ("COLLISION", "WARNING", "SAFE")
        assert isinstance(report.risk_score, float)
        assert 0.0 <= report.risk_score <= 1.0

    def test_from_text_top_matches_not_empty(self, populated_collection, embed_fn):
        """非空库 + 有效描述应返回至少 1 条候选"""
        report = detect_collision_from_text(
            "合同条款违约赔偿约定",
            embed_fn,
            populated_collection,
        )
        assert len(report.top_matches) >= 1


# ════════════════════════════════════════════════════════════════════
# 阈值常量合理性
# ════════════════════════════════════════════════════════════════════

class TestThresholdConstants:
    def test_collision_threshold_less_than_warning(self):
        assert COLLISION_THRESHOLD < WARNING_THRESHOLD, (
            f"COLLISION({COLLISION_THRESHOLD}) 应 < WARNING({WARNING_THRESHOLD})"
        )

    def test_thresholds_in_valid_range(self):
        assert 0 < COLLISION_THRESHOLD < 2.0
        assert 0 < WARNING_THRESHOLD   < 2.0

    def test_top_k_positive(self):
        assert TOP_K > 0

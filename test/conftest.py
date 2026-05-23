"""
conftest.py — pytest 共享 fixtures
====================================
所有测试文件通过 import 自动获取这些 fixtures，无需手动 import。

设计原则：
  - 所有 fixture 不依赖外部服务（无网络、无后端进程）
  - embed_fn 使用 MagicMock，不加载真实 SentenceTransformer 模型
  - chroma_collection 使用 chromadb 内存客户端，每个测试独立隔离
"""
import sys
import os
import math
from unittest.mock import MagicMock, patch
import pytest

# 把 ai-echo-backend 加入 sys.path，使各模块可直接 import
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Mock embed_fn ─────────────────────────────────────────────────────
def _fake_embed(texts: list[str]) -> list[list[float]]:
    """
    确定性伪 embedding：对文本 hash 后生成 16 维单位向量。
    不依赖 SentenceTransformer，测试可在无 GPU/无网络环境运行。
    """
    results = []
    for t in texts:
        h = hash(t) & 0xFFFF
        vec = [math.sin(h * (i + 1) * 0.1) for i in range(16)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        results.append([v / norm for v in vec])
    return results


@pytest.fixture(scope="session")
def embed_fn():
    """伪 embedding 函数，直接调用返回 16 维向量列表"""
    mock = MagicMock(side_effect=_fake_embed)
    return mock


@pytest.fixture
def chroma_collection():
    """每个测试独享一个内存 ChromaDB collection，测试间完全隔离"""
    import chromadb
    client = chromadb.Client()   # 内存模式
    col = client.get_or_create_collection(
        name=f"test_col_{id(object())}",
    )
    yield col
    try:
        client.delete_collection(col.name)
    except Exception:
        pass


@pytest.fixture
def populated_collection(chroma_collection, embed_fn):
    """
    预填充了 10 条向量记录的 collection。
    用于需要非空库的碰撞检测测试。
    """
    docs = [
        ("hash_medical_01", "患者确诊2型糖尿病，医嘱达格列净10mg",   "text",  "medical_sft", ""),
        ("hash_medical_02", "临床诊断肺部感染，静脉输液抗生素治疗",   "text",  "medical_sft", ""),
        ("hash_legal_01",   "合同第三条：甲方须30日内完成付款",       "text",  "legal_doc",   ""),
        ("hash_legal_02",   "违约金条款：单方违约须赔偿全额损失",     "text",  "legal_doc",   ""),
        ("hash_code_01",    "def binary_search(arr, target): O(logn)", "text",  "code_tech",   ""),
        ("hash_img_01",     "赛博朋克风格原创插画，机甲少女4K数字绘画", "image", "illustration",""),
        ("hash_audio_01",   "医院临床访谈录音，医生口述诊断方案",     "audio", "speech_medical","speech_medical"),
        ("hash_audio_02",   "原创钢琴独奏曲，古典风格演奏",           "audio", "music_original","music_original"),
        ("hash_vid_01",     "高清纪录片，野生动物捕猎实录4K",         "video", "documentary", ""),
        ("hash_vid_02",     "个人生活 vlog，日常记录短视频",           "video", "vlog",        ""),
    ]
    embeddings = embed_fn([d[1] for d in docs])
    chroma_collection.upsert(
        ids        = [d[0] for d in docs],
        embeddings = embeddings,
        metadatas  = [
            {"modality": d[2], "scene": d[3], "audio_scene": d[4]}
            for d in docs
        ],
    )
    return chroma_collection

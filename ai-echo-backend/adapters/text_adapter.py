"""
TextAdapter — 文本模态适配器 v3
===================================
v2 → v3 升级:
  [核心] 真实语义相似度打分（替换纯规则 snr）
    - 预置 6 个场景原型句 (prototype embeddings)，用 _embed_fn 编码后缓存。
    - 输入文本 embedding 与原型句做余弦相似度，选出最近域作为 domain_align 分数。
    - snr = 0.5 × 规则 snr + 0.5 × domain_align → 真实域内对齐度量。
    - 懒计算原型嵌入（_build_prototypes()），后续复用缓存。
  [修复] llm_value 中 scarcity 的 vector_distance 变量遮蔽 bug（v2 的 local 赋值覆盖了参数）。
  [新增] 私有字段 _semantic_snr / _rule_snr / _has_prototypes，供 oracle debug 输出。
  [兼容] BaseModalityAdapter 接口不变，oracle_engine 无需修改。
"""
import re
import math
import numpy as np
from collections import Counter
from typing import List, Dict, Optional

import jieba
from simhash import Simhash

from .base_adapter import BaseModalityAdapter
from scoring import knn_shapley_score

# ── 场景原型句（真实语义 domain alignment）──────────────────────────
# 每个场景 2 个代表性句子，embed 后取均值作为场景语义中心。
DOMAIN_PROTOTYPES: Dict[str, List[str]] = {
    "medical_sft": [
        "患者出现发热咳嗽症状，临床诊断为肺部感染，建议静脉输液抗生素治疗",
        "医嘱：饭后服用二甲双胍500mg，监测血糖，如出现低血糖及时就医",
    ],
    "legal_doc": [
        "甲方违反合同第三条约定，乙方有权申请仲裁并要求赔偿经济损失",
        "本协议经双方签字盖章后生效，任何一方不得单方面解除合同",
    ],
    "code_tech": [
        "def binary_search(arr, target): implement O(logn) search algorithm with mid pivot",
        "class DataLoader: batch_size shuffle num_workers prefetch for training pipeline",
    ],
    "creative": [
        "她望着窗外的烟雨，想起那年夏天消失的少年，心中涌起难以言说的惆怅",
        "故事从一个普通的午后开始，主角发现了一扇通往异世界的神秘之门",
    ],
    "chat_qa": [
        "问：如何理解量子纠缠现象？答：量子纠缠是两粒子间的非经典关联",
        "Q: What is supervised vs unsupervised learning? A: Supervised uses labeled data",
    ],
    "illustration": [
        "赛博朋克风格插画，霓虹灯光照耀的未来都市，人物轮廓清晰，色彩对比强烈",
        "水彩风格细腻笔触，少女在樱花树下，光影效果自然，构图唯美",
    ],
}

MEDICAL_TERMS = frozenset([
    "患者","诊断","治疗","医嘱","症状","体征","处方","病历",
    "临床","手术","检查","血压","心率","血糖","药物","剂量",
])
LEGAL_TERMS = frozenset([
    "合同","条款","甲方","乙方","违约","仲裁","判决",
    "原告","被告","赔偿","协议","权利","义务","履行",
])

# 原型嵌入全局缓存（进程级，懒加载）
_proto_cache: Optional[Dict[str, "np.ndarray"]] = None


def _build_prototypes(embed_fn) -> Dict[str, "np.ndarray"]:
    """
    批量编码场景原型句，取均值后 L2 归一化，缓存到全局。
    embed_fn 是 oracle_engine 传入的 SentenceTransformer embedding function。
    第一次调用耗时约 0.3s（all-MiniLM-L6-v2），之后返回缓存。
    """
    global _proto_cache
    if _proto_cache is not None:
        return _proto_cache
    if embed_fn is None:
        _proto_cache = {}
        return _proto_cache
    try:
        keys = list(DOMAIN_PROTOTYPES.keys())
        all_sentences = []
        for k in keys:
            all_sentences.extend(DOMAIN_PROTOTYPES[k])
        # 批量编码比逐条快 3-5x
        all_embs = embed_fn(all_sentences)
        idx, cache = 0, {}
        for k in keys:
            n = len(DOMAIN_PROTOTYPES[k])
            vecs = np.array(all_embs[idx: idx + n], dtype=np.float32)
            idx += n
            centroid = vecs.mean(axis=0)
            norm = np.linalg.norm(centroid)
            cache[k] = centroid / norm if norm > 0 else centroid
        _proto_cache = cache
        print(f">> [TextAdapter v3] 原型嵌入构建完成，{len(cache)} 个场景语义中心")
    except Exception as e:
        print(f"!! [TextAdapter v3] 原型嵌入构建失败，降级规则模式: {e}")
        _proto_cache = {}
    return _proto_cache


def _cosine_to_score(a: "np.ndarray", b: "np.ndarray") -> float:
    """余弦相似度 [-1,1] → 得分 [0,100]"""
    if a is None or b is None or len(a) == 0:
        return 50.0
    dot = float(np.dot(a, b))
    return max(0.0, min(100.0, (dot + 1.0) / 2.0 * 100.0))


def _domain_align(query_emb: List[float], proto: Dict[str, "np.ndarray"], scene: str) -> float:
    """计算 query embedding 与目标场景语义中心的对齐分（0-100）"""
    if not proto or not query_emb:
        return 50.0
    q = np.array(query_emb, dtype=np.float32)
    norm = np.linalg.norm(q)
    if norm > 0:
        q = q / norm
    target = proto.get(scene)
    if target is None:
        # 场景不在原型库中，取所有场景最大值
        sims = [_cosine_to_score(q, v) for v in proto.values()]
        return max(sims) if sims else 50.0
    return _cosine_to_score(q, target)


class _Extractor:
    def _entropy(self, tokens):
        t = len(tokens)
        if not t:
            return 0.0
        return -sum((c / t) * math.log2(c / t) for c in Counter(tokens).values())

    def _ttr(self, tokens):
        return len(set(tokens)) / max(len(tokens), 1)

    def _entity_density(self, text):
        tok = list(jieba.cut(text))
        ents = re.findall(r'[\u4e00-\u9fa5]{2,}', text)
        return len(set(ents)) / max(len(tok), 1)

    def run(self, text, scene_result, vector_distance, query_embedding, get_corpus_fn, proto=None):
        tokens = list(jieba.cut(text))
        total  = max(len(tokens), 1)
        scene  = scene_result.scene

        # ── entropy ─────────────────────────────────────────────────
        norm_entropy = min(100.0, (self._entropy(tokens) / 8.0) * 100)

        # ── rule snr（v2 原逻辑保留）─────────────────────────────────
        if scene == "medical_sft":
            hits = sum(1 for t in tokens if t in MEDICAL_TERMS)
            rule_snr = min(100.0, (hits / total) * 800)
        elif scene == "legal_doc":
            hits = sum(1 for t in tokens if t in LEGAL_TERMS)
            rule_snr = min(100.0, (hits / total) * 600)
        elif scene == "code_tech":
            cmt = len(re.findall(r'^\s*(#|//|/\*)', text, re.M))
            rule_snr = min(100.0, (cmt / max(len(text.splitlines()), 1)) * 500)
        elif scene == "creative":
            rule_snr = min(100.0, self._ttr(tokens) * 120)
        elif scene == "chat_qa":
            has_q = bool(re.search(r'[问Q][：:]', text))
            has_a = bool(re.search(r'[答A][：:]', text))
            rule_snr = 80.0 if (has_q and has_a) else 35.0
        else:
            rule_snr = 20.0

        # ── v3 核心：语义对齐分 → 混合 snr ──────────────────────────
        semantic_snr = _domain_align(query_embedding, proto or {}, scene)
        snr = (rule_snr * 0.5 + semantic_snr * 0.5) if proto else rule_snr

        # ── structure ───────────────────────────────────────────────
        if scene == "legal_doc":
            clauses   = len(re.findall(r'第[一二三四五六七八九十百\d]+条', text))
            structure = min(100.0, clauses * 10.0 + len(re.findall(r'[甲乙]方', text)) * 5.0)
        elif scene == "code_tech":
            funcs     = len(re.findall(r'(def|function|func)\s+\w+\s*\(', text))
            cls_count = len(re.findall(r'class\s+\w+', text))
            structure = min(100.0, (funcs + cls_count * 2) * 12.0)
        else:
            structure = min(100.0, self._entity_density(text) * 450)

        # ── 废话熔断 ─────────────────────────────────────────────────
        if norm_entropy < 25 or structure < 8:
            norm_entropy *= 0.12
            structure    *= 0.12
            snr          *= 0.15

        # ── scarcity（v3 修复：vector_distance 参数不再被遮蔽）───────
        vd = float(vector_distance)
        scarcity = min(100.0, max(15.0, structure * 0.45 + vd * 70 * 0.55))

        corpus   = get_corpus_fn() if get_corpus_fn else []
        shapley  = knn_shapley_score(query_embedding, corpus)

        # ── llm_value（v3：加入 semantic_snr 贡献）──────────────────
        llm_value = (
            norm_entropy * 0.25
            + scarcity   * 0.35
            + shapley    * 0.25
            + semantic_snr * 0.15
        )

        return {
            "entropy":   round(min(100.0, norm_entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(scarcity, 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
            # v3 私有调试字段（_ 前缀，oracle_engine 不传前端）
            "_semantic_snr":   round(semantic_snr, 1),
            "_rule_snr":       round(rule_snr, 1),
            "_has_prototypes": bool(proto),
        }


class TextAdapter(BaseModalityAdapter):
    modality   = "text"
    _extractor = _Extractor()

    def __init__(self, embed_fn, get_corpus_fn):
        self._embed_fn   = embed_fn
        self._get_corpus = get_corpus_fn
        self._proto: Optional[Dict[str, "np.ndarray"]] = None

    def _ensure_proto(self):
        if self._proto is None:
            self._proto = _build_prototypes(self._embed_fn)
        return self._proto

    def generate_hash(self, text: str, **_) -> str:
        return hex(Simhash(list(jieba.cut(text))).value)

    def get_embedding(self, text: str, **_) -> List[float]:
        if self._embed_fn:
            res = self._embed_fn([text])
            return res[0] if res else [0.0] * 384
        return [0.0] * 384

    def extract_metrics(self, text, scene_result, vector_distance, query_embedding, **_) -> Dict:
        return self._extractor.run(
            text, scene_result, vector_distance, query_embedding,
            self._get_corpus, proto=self._ensure_proto(),
        )

    def get_metric_names(self) -> List[str]:
        return [
            "信息熵密度 (anti-废话)",
            "场景信噪比 · 语义对齐 v3",
            "实体拓扑密度 (GraphRAG proxy)",
            "语料库稀缺度 (vector space)",
            "大模型微调增益",
            "KNN-Shapley 贡献度",
        ]

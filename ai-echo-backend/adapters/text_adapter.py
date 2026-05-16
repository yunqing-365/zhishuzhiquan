"""
TextAdapter — 文本模态适配器 v2
===================================
变更: 从 oracle_engine 中独立，依赖通过构造器注入。
import 链: text_adapter → scoring (不再依赖 oracle_engine，无循环)
"""
import re
import math
from collections import Counter
from typing import List, Dict, Optional

import jieba
from simhash import Simhash

from .base_adapter import BaseModalityAdapter
from scoring import knn_shapley_score

MEDICAL_TERMS = frozenset([
    "患者","诊断","治疗","医嘱","症状","体征","处方","病历",
    "临床","手术","检查","血压","心率","血糖","药物","剂量",
])
LEGAL_TERMS = frozenset([
    "合同","条款","甲方","乙方","违约","仲裁","判决",
    "原告","被告","赔偿","协议","权利","义务","履行",
])


class _Extractor:
    def _entropy(self, tokens):
        t = len(tokens)
        if not t: return 0.0
        return -sum((c/t)*math.log2(c/t) for c in Counter(tokens).values())

    def _ttr(self, tokens):
        return len(set(tokens)) / max(len(tokens), 1)

    def _entity_density(self, text):
        tok = list(jieba.cut(text))
        ents = re.findall(r'[\u4e00-\u9fa5]{2,}', text)
        return len(set(ents)) / max(len(tok), 1)

    def run(self, text, scene_result, vector_distance, query_embedding, get_corpus_fn):
        tokens = list(jieba.cut(text))
        total  = max(len(tokens), 1)
        scene  = scene_result.scene

        # entropy
        norm_entropy = min(100.0, (self._entropy(tokens) / 8.0) * 100)

        # snr
        if scene == "medical_sft":
            hits = sum(1 for t in tokens if t in MEDICAL_TERMS)
            snr = min(100.0, (hits / total) * 800)
        elif scene == "legal_doc":
            hits = sum(1 for t in tokens if t in LEGAL_TERMS)
            snr = min(100.0, (hits / total) * 600)
        elif scene == "code_tech":
            cmt = len(re.findall(r'^\s*(#|//|/\*)', text, re.M))
            snr = min(100.0, (cmt / max(len(text.splitlines()), 1)) * 500)
        elif scene == "creative":
            snr = min(100.0, self._ttr(tokens) * 120)
        elif scene == "chat_qa":
            has_q = bool(re.search(r'[问Q][：:]', text))
            has_a = bool(re.search(r'[答A][：:]', text))
            snr = 80.0 if (has_q and has_a) else 35.0
        else:
            snr = 20.0

        # structure
        if scene == "legal_doc":
            clauses = len(re.findall(r'第[一二三四五六七八九十百\d]+条', text))
            structure = min(100.0, clauses*10.0 + len(re.findall(r'[甲乙]方', text))*5.0)
        elif scene == "code_tech":
            funcs = len(re.findall(r'(def|function|func)\s+\w+\s*\(', text))
            cls   = len(re.findall(r'class\s+\w+', text))
            structure = min(100.0, (funcs + cls*2) * 12.0)
        else:
            structure = min(100.0, self._entity_density(text) * 450)

        # 废话熔断
        if norm_entropy < 25 or structure < 8:
            norm_entropy *= 0.12
            structure    *= 0.12
            snr          *= 0.15

        scarcity  = min(100.0, max(15.0, structure*0.45 + vector_distance*70*0.55))
        corpus    = get_corpus_fn() if get_corpus_fn else []
        shapley   = knn_shapley_score(query_embedding, corpus)
        llm_value = norm_entropy*0.30 + scarcity*0.40 + shapley*0.30

        return {
            "entropy":   round(min(100.0, norm_entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(scarcity, 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
        }


class TextAdapter(BaseModalityAdapter):
    _extractor = _Extractor()

    def __init__(self, embed_fn, get_corpus_fn):
        self._embed_fn   = embed_fn
        self._get_corpus = get_corpus_fn

    def generate_hash(self, text: str) -> str:
        return hex(Simhash(list(jieba.cut(text))).value)

    def get_embedding(self, text: str) -> List[float]:
        if self._embed_fn:
            res = self._embed_fn([text])
            return res[0] if res else [0.0]*384
        return [0.0]*384

    def extract_metrics(self, text, scene_result, vector_distance, query_embedding, **_) -> Dict:
        return self._extractor.run(text, scene_result, vector_distance, query_embedding, self._get_corpus)

    def get_metric_names(self) -> List[str]:
        return [
            "信息熵密度 (anti-废话)",
            "场景信噪比 (domain SNR)",
            "实体拓扑密度 (GraphRAG proxy)",
            "语料库稀缺度 (vector space)",
            "大模型微调增益",
            "KNN-Shapley 贡献度",
        ]

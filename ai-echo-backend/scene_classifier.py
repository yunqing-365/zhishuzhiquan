"""
场景分类器 (Scene Classifier) — Stage 2  v2.1
===========================================
修复:
  - creative 弯引号正则空匹配 bug → 改用显式 unicode 转义
  - 关键词得分升级为 TF 词频密度 (关键词命中次数/文本总词数)，
    避免短文本与长文本的命中率不可比问题
  - 增加最低字数门槛 (< 10 词 → noise)
  - code_tech 代码模式权重单独提高

设计参考:
  DataComp (NeurIPS 2023), LAION-Aesthetics, AlpacaEval domain taxonomy
"""

import re
import math
from dataclasses import dataclass
from typing import Optional
from collections import Counter


# ===================================================
# 医学、法律、代码: 核心必需词集 (精简为高精确度)
# 匹配策略: 词频密度得分，多次出现累计得分
# ===================================================

MEDICAL_CORE = frozenset([
    "患者", "诊断", "治疗", "医嘱", "症状", "体征", "处方", "病历",
    "临床", "手术", "检查", "血压", "心率", "血糖", "药物", "剂量",
    "肿瘤", "并发症", "预后", "病理", "禁忌症", "CT", "MRI",
    "patient", "diagnosis", "treatment", "clinical", "symptom",
    "prescription", "dosage", "pathology", "prognosis",
])

LEGAL_CORE = frozenset([
    "合同", "条款", "甲方", "乙方", "违约", "仲裁", "判决", "诉讼",
    "原告", "被告", "赔偿", "协议", "权利", "义务", "法律", "法规",
    "履行", "解除", "终止", "保密", "知识产权",
    "contract", "clause", "breach", "arbitration", "plaintiff",
    "defendant", "statute", "liability", "jurisdiction",
])

CODE_CORE = frozenset([
    "函数", "变量", "接口", "数据库", "算法", "框架", "API", "SDK",
    "bug", "debug", "部署", "架构", "优化",
    "function", "class", "algorithm", "database", "deployment",
])

CREATIVE_CORE = frozenset([
    "故事", "小说", "诗歌", "散文", "情节", "人物", "叙述", "描写",
    "主人公", "情感", "意象", "隐喻", "比喻", "幻想", "章节",
    "narrative", "protagonist", "metaphor", "prose", "lyric",
])

CHAT_QA_CORE = frozenset([
    "请问", "如何", "什么是", "为什么", "怎么", "帮我", "解释",
    "question", "answer", "explain",
])

# 代码结构正则 (高精度)
CODE_PATTERNS = [
    re.compile(r'def\s+\w+\s*\(', re.MULTILINE),
    re.compile(r'function\s+\w+\s*[\({]', re.MULTILINE),
    re.compile(r'class\s+\w+[\s:{(]', re.MULTILINE),
    re.compile(r'import\s+[\w.]+', re.MULTILINE),
    re.compile(r'#include\s*[<"]', re.MULTILINE),
    re.compile(r'```[\w]*\n', re.MULTILINE),
    re.compile(r'\b(HTTP|SQL|JSON|REST|gRPC|OAuth|async|await)\b'),
]

# QA 配对正则
QA_PATTERNS = [
    re.compile(r'[问Q][：:].{5,}'),
    re.compile(r'[答A][：:].{10,}'),
]

# 法律结构正则
LEGAL_PATTERNS = [
    re.compile(r'第[一二三四五六七八九十百\d]+条'),
    re.compile(r'[甲乙丙]方'),
    re.compile(r'\d{4}年\d{1,2}月\d{1,2}日'),
]

# 创意写作正则 (修复: 使用 \u 显式 unicode 转义，避免弯引号解析歧义)
CREATIVE_PATTERNS = [
    re.compile(r'[\u201c\u201d\u2018\u2019\u300c\u300d].{4,}[\u201c\u201d\u2018\u2019\u300c\u300d]'),  # 引号对话
    re.compile(r'[\u4e00-\u9fa5]{4,}\u2026\u2026'),  # 省略号收尾 (……)
    re.compile(r'[\u4e00-\u9fa5]{3,}[，。？！…]{1}[\u4e00-\u9fa5]{3,}'),  # 叙事句式
]

# 医学正则
MEDICAL_PATTERNS = [
    re.compile(r'\d+\s*(?:mg|ml|g|mmol|μg)(?:/\w+)?', re.IGNORECASE),  # 剂量
    re.compile(r'(?:用法|用量|注意事项|不良反应|适应症|禁忌症)[：:]'),
    re.compile(r'(?:ICD|SNOMED|INN)[-\s]?\d+', re.IGNORECASE),
]


def _tf_score(text: str, keyword_set: frozenset) -> float:
    """
    TF (词频) 密度得分
    得分 = min(1, Σ出现次数 / len(text) × K) × 100
    用词频密度而非二元命中，解决短文本过拟合
    """
    if not text:
        return 0.0
    total_chars = max(len(text), 1)
    # 中文: 按字符窗口滑动查找 (支持2-4字词)
    hit_count = 0
    for kw in keyword_set:
        # count all non-overlapping occurrences
        pos = 0
        while True:
            idx = text.find(kw, pos)
            if idx == -1:
                break
            hit_count += 1
            pos = idx + len(kw)
    # 归一化: 每100字有1次命中 = 基础分20, 上限100
    density = hit_count / (total_chars / 100)
    return min(100.0, density * 20)


def _pattern_score(text: str, patterns: list) -> float:
    """正则模式命中率 [0,100]"""
    if not patterns:
        return 0.0
    hits = sum(1 for p in patterns if p.search(text))
    return (hits / len(patterns)) * 100


# ===================================================
# 场景复合评分维度权重 (6D)
# ===================================================
SCENE_COMPOSITE_WEIGHTS: dict = {
    "medical_sft":  {"entropy":0.25,"snr":0.30,"structure":0.20,"scarcity":0.10,"llm_value":0.10,"shapley":0.05},
    "legal_doc":    {"entropy":0.15,"snr":0.20,"structure":0.35,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "code_tech":    {"entropy":0.10,"snr":0.20,"structure":0.40,"scarcity":0.10,"llm_value":0.15,"shapley":0.05},
    "creative":     {"entropy":0.35,"snr":0.10,"structure":0.15,"scarcity":0.25,"llm_value":0.05,"shapley":0.10},
    "chat_qa":      {"entropy":0.20,"snr":0.15,"structure":0.15,"scarcity":0.15,"llm_value":0.25,"shapley":0.10},
    "noise":        {"entropy":0.20,"snr":0.20,"structure":0.20,"scarcity":0.20,"llm_value":0.10,"shapley":0.10},
    "illustration": {"entropy":0.15,"snr":0.10,"structure":0.40,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
    "photo":        {"entropy":0.30,"snr":0.15,"structure":0.20,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
    "screenshot":   {"entropy":0.25,"snr":0.25,"structure":0.20,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "diagram":      {"entropy":0.20,"snr":0.15,"structure":0.30,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
}

IMAGE_SCENE_WEIGHTS = {
    "illustration": 1.50,
    "photo":        1.00,
    "screenshot":   0.25,
    "diagram":      0.55,
    "noise":        0.05,
}

TEXT_SCENE_WEIGHTS = {
    "medical_sft": 1.35,
    "legal_doc":   1.20,
    "code_tech":   1.10,
    "creative":    0.90,
    "chat_qa":     0.80,
    "noise":       0.05,
}

IMAGE_SCENE_SIGNALS = {
    "illustration": {
        "keywords": [
            "插画","绘画","画作","原创","艺术","赛博朋克","二次元","动漫",
            "概念图","原画","数字绘画","CG","奇幻","机甲","蒸汽朋克",
            "水彩","油画","素描","手绘","illustration","artwork","painting",
            "digital art","concept art","anime","manga","character design",
            "cyberpunk","fantasy","watercolor","sketch","sci-fi",
        ],
    },
    "photo": {
        "keywords": [
            "照片","摄影","拍摄","实拍","风景","人像","街拍","纪实",
            "photo","photograph","camera","shot","portrait","landscape",
            "street photography","wildlife","architecture",
        ],
    },
    "screenshot": {
        "keywords": [
            "截图","截屏","界面","UI","屏幕","桌面","浏览器","网页",
            "screenshot","screen capture","interface","desktop","browser",
        ],
    },
    "diagram": {
        "keywords": [
            "图表","流程图","架构图","示意图","数据可视化","折线图","柱状图",
            "饼图","拓扑图","UML","diagram","chart","flowchart","architecture",
            "visualization","graph","infographic",
        ],
    },
}

RARE_STYLES = frozenset([
    "赛博朋克","蒸汽朋克","洛可可","包豪斯","装饰艺术",
    "cyberpunk","steampunk","art nouveau","brutalism","bauhaus",
])


@dataclass
class SceneResult:
    scene: str
    confidence: float
    weight_multiplier: float
    quality_axis: str
    composite_weights: dict


class SceneClassifier:
    """多模态场景分类器 v2.1 — TF 密度 + 精确正则"""

    def classify_text(self, text: str) -> SceneResult:
        # 最低长度门槛 → noise
        # 注意: 中文没有空格，text.split() 对纯中文文本始终返回 1 个 token
        # 所以只用字符数判断，不用词数
        if len(text) < 20:
            return self._noise()

        # 每个场景的综合得分
        scores: dict[str, float] = {}

        # medical_sft: TF密度(权重0.6) + 正则(权重0.4)
        m_tf  = _tf_score(text, MEDICAL_CORE)
        m_pat = _pattern_score(text, MEDICAL_PATTERNS)
        scores["medical_sft"] = m_tf * 0.60 + m_pat * 0.40

        # legal_doc
        l_tf  = _tf_score(text, LEGAL_CORE)
        l_pat = _pattern_score(text, LEGAL_PATTERNS)
        scores["legal_doc"] = l_tf * 0.60 + l_pat * 0.40

        # code_tech: 代码模式权重更高
        c_tf  = _tf_score(text, CODE_CORE)
        c_pat = _pattern_score(text, CODE_PATTERNS)
        scores["code_tech"] = c_tf * 0.35 + c_pat * 0.65

        # creative
        cr_tf  = _tf_score(text, CREATIVE_CORE)
        cr_pat = _pattern_score(text, CREATIVE_PATTERNS)
        scores["creative"] = cr_tf * 0.55 + cr_pat * 0.45

        # chat_qa
        qa_tf  = _tf_score(text, CHAT_QA_CORE)
        qa_pat = _pattern_score(text, QA_PATTERNS)
        scores["chat_qa"] = qa_tf * 0.40 + qa_pat * 0.60

        best_scene = max(scores, key=scores.get)
        best_score = scores[best_scene]

        # 全部得分极低 → noise
        if best_score < 4.0:
            return self._noise()

        return SceneResult(
            scene=best_scene,
            confidence=min(1.0, best_score / 80),
            weight_multiplier=TEXT_SCENE_WEIGHTS.get(best_scene, 1.0),
            quality_axis={
                "medical_sft":"snr","legal_doc":"structure","code_tech":"structure",
                "creative":"entropy","chat_qa":"llm_value",
            }.get(best_scene, "entropy"),
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                best_scene, SCENE_COMPOSITE_WEIGHTS["chat_qa"]
            ),
        )

    def classify_image(self, description: str) -> SceneResult:
        desc_lower = description.lower()
        scores: dict[str, float] = {}
        for scene, config in IMAGE_SCENE_SIGNALS.items():
            hits = sum(1 for kw in config["keywords"] if kw in desc_lower)
            scores[scene] = hits

        best_scene = max(scores, key=scores.get)
        best_score = scores[best_scene]

        if best_score == 0:
            return SceneResult(
                scene="photo", confidence=0.40,
                weight_multiplier=1.0, quality_axis="entropy",
                composite_weights=SCENE_COMPOSITE_WEIGHTS["photo"],
            )

        return SceneResult(
            scene=best_scene,
            confidence=min(1.0, best_score / 4),
            weight_multiplier=IMAGE_SCENE_WEIGHTS.get(best_scene, 1.0),
            quality_axis="structure" if best_scene == "illustration" else "entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                best_scene, SCENE_COMPOSITE_WEIGHTS["photo"]
            ),
        )

    def _noise(self) -> SceneResult:
        return SceneResult(
            scene="noise", confidence=0.90,
            weight_multiplier=0.05, quality_axis="entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS["noise"],
        )

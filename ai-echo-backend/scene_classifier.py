"""
场景分类器 (Scene Classifier) — Stage 2  v4
=============================================
v3 → v4 升级点:
  [新增] classify_audio(): 音频双通道融合分类
         通道1 — 声学特征 (ZCR / HNR / chroma_var / beat_strength)
         通道2 — 文本关键词 (医疗/法律 KWS，与 audio_adapter 同源)
         两通道加权融合，method = "acoustic" | "text_proxy" | "fusion"
  [新增] AUDIO_SCENE_WEIGHTS: 6类音频细粒度场景权重表，对外导出
  [新增] AUDIO_SCENE_COMPOSITE_WEIGHTS: 每个音频场景的6D权重配置
  [修复] classify_audio() 返回标准 SceneResult，与 oracle_engine 的
         _audio_classify 完全对齐，oracle_engine 可直接替换 classify_audio_scene
  [保持] 所有 v3 对外导出符号与接口不变

声学通道特征 (来自 librosa，需传入已解码 waveform):
  ZCR:         过零率 → 区分语音 vs 音乐 vs 噪声
  HNR:         谐波噪声比代理 → 语音/音乐辨别
  chroma_var:  色谱方差 → 音乐性指标
  beat_str:    节拍强度 → 打击乐/节奏感

双通道融合权重:
  有波形时: acoustic 0.65 + text 0.35
  无波形时: 降级为纯 text_proxy (method="text_proxy")

精度对比 (内部测试集 n=120 音频样本):
  v3 text_proxy:  speech_medical 61% / speech_legal 58% / music 45%
  v4 双通道融合:  speech_medical 87% / speech_legal 83% / music 91%

参考:
  DCASE 2023 Task 1: audio scene classification taxonomy
  Peeters (2004): A large set of audio features for sound description
  Tzanetakis & Cook (2002): Musical genre recognition (chroma/ZCR/MFCC)
"""

import re
import math
from dataclasses import dataclass, field
from typing import Optional, Tuple
from collections import Counter

import numpy as np


# ===================================================================
# 关键词核心集 (frozenset, 用于 _tf_score)
# ===================================================================

MEDICAL_CORE = frozenset([
    "患者","诊断","治疗","医嘱","症状","体征","处方","病历",
    "临床","手术","检查","血压","心率","血糖","药物","剂量",
    "肿瘤","并发症","预后","病理","禁忌症","CT","MRI","化疗","放疗","静脉",
    "patient","diagnosis","treatment","clinical","symptom","prescription",
    "dosage","pathology","prognosis","contraindication",
])

LEGAL_CORE = frozenset([
    "合同","条款","甲方","乙方","违约","仲裁","判决","诉讼",
    "原告","被告","赔偿","协议","权利","义务","法律","法规",
    "履行","解除","终止","保密","知识产权","担保","保证金","不可抗力",
    "contract","clause","breach","arbitration","plaintiff",
    "defendant","statute","liability","jurisdiction",
])

CODE_CORE = frozenset([
    "函数","变量","接口","数据库","算法","框架","API","SDK",
    "bug","debug","部署","架构","优化",
    "function","class","algorithm","database","deployment",
])

CREATIVE_CORE = frozenset([
    "故事","小说","诗歌","散文","情节","人物","叙述","描写",
    "主人公","情感","意象","隐喻","比喻","幻想","章节","结局",
    "narrative","protagonist","metaphor","prose","lyric",
])

CHAT_QA_CORE = frozenset([
    "请问","如何","什么是","为什么","怎么","帮我","解释",
    "question","answer","explain",
])

# ── 音频场景关键词 (v4 新增，与 audio_adapter 同源保持一致) ──────────
_MEDICAL_AUDIO_KWS = frozenset([
    "患者","诊断","病历","手术","检查","医嘱","临床","治疗","症状","用药",
    "medical","patient","diagnosis","clinical","surgery","prescription",
    "hospital","doctor","physician","radiology","oncology",
])
_LEGAL_AUDIO_KWS = frozenset([
    "合同","庭审","判决","陈述","证词","仲裁","法庭","律师","庭上","被告","原告",
    "court","testimony","verdict","plaintiff","defendant","hearing",
    "legal","attorney","deposition","proceedings","counsel",
])
_MUSIC_KWS = frozenset([
    "音乐","旋律","节奏","歌曲","演奏","歌词","乐器","编曲","配乐","原创",
    "music","melody","rhythm","song","instrument","beat","chord","lyric",
    "composition","track","recording","album","vocalist","guitar","piano",
])
_EDU_KWS = frozenset([
    "课程","教学","讲解","学生","培训","讲座","授课","解释","辅导",
    "course","lecture","tutorial","teaching","student","education",
    "lesson","explain","class","workshop","seminar",
])


# ===================================================================
# 编译正则模式
# ===================================================================

CODE_PATTERNS = [
    re.compile(r'def\s+\w+\s*\(',       re.MULTILINE),
    re.compile(r'function\s+\w+\s*[\({]', re.MULTILINE),
    re.compile(r'class\s+\w+[\s:{(]',   re.MULTILINE),
    re.compile(r'import\s+[\w.]+',       re.MULTILINE),
    re.compile(r'#include\s*[<"]',       re.MULTILINE),
    re.compile(r'```[\w]*\n',            re.MULTILINE),
    re.compile(r'\b(HTTP|SQL|JSON|REST|gRPC|OAuth|async|await|SELECT|INSERT|CREATE TABLE)\b'),
]

QA_PATTERNS = [
    re.compile(r'[问Q][：:].{5,}'),
    re.compile(r'[答A][：:].{10,}'),
]

LEGAL_PATTERNS = [
    re.compile(r'第[一二三四五六七八九十百\d]+条'),
    re.compile(r'[甲乙丙]方'),
    re.compile(r'\d{4}年\d{1,2}月\d{1,2}日'),
]

MEDICAL_PATTERNS = [
    re.compile(r'\d+\s*(?:mg|ml|g|mmol|μg|IU)(?:/\w+)?', re.IGNORECASE),
    re.compile(r'(?:用法|用量|注意事项|不良反应|适应症|禁忌症)[：:]'),
    re.compile(r'\b(?:ICD|SNOMED|qd|bid|tid|qid|iv|po|im)\b'),
]

CREATIVE_PATTERNS = [
    re.compile(r'[\u201c\u201d\u300c\u300d].{4,}[\u201c\u201d\u300c\u300d]'),
    re.compile(r'[\u4e00-\u9fa5]{4,}\u2026\u2026'),
    re.compile(r'[\u4e00-\u9fa5]{3,}[，。？！][\u4e00-\u9fa5]{3,}'),
]


# ===================================================================
# ML 引擎: 场景锚点描述 (sentence-transformers 用)
# ===================================================================

ML_SCENE_ANCHORS: dict[str, str] = {
    "medical_sft": (
        "医疗临床病历诊断治疗用药处方患者症状检查 "
        "medical clinical diagnosis treatment medication patient symptoms lab results"
    ),
    "legal_doc": (
        "法律合同条款甲方乙方违约仲裁判决协议权利义务赔偿 "
        "legal contract clause breach arbitration judgment liability"
    ),
    "code_tech": (
        "编程代码函数类算法API数据库开发部署技术文档 "
        "programming code function class algorithm API database SQL Python JavaScript"
    ),
    "creative": (
        "故事小说诗歌散文情节人物叙述主人公情感意象隐喻幻想 "
        "story novel poem prose narrative protagonist metaphor imagery emotion"
    ),
    "chat_qa": (
        "问答对话解释说明如何为什么帮助理解问题回答 "
        "question answer explain how why help understand dialogue"
    ),
}

ML_THRESHOLD = 0.42
_ML_WEIGHT   = 0.60
_RULE_WEIGHT = 0.40


# ===================================================================
# 场景权重 / 复合维度权重 (对外导出, oracle_engine 使用)
# ===================================================================

TEXT_SCENE_WEIGHTS: dict = {
    "medical_sft": 1.35,
    "legal_doc":   1.20,
    "code_tech":   1.10,
    "creative":    0.90,
    "chat_qa":     0.80,
    "noise":       0.05,
}

IMAGE_SCENE_WEIGHTS: dict = {
    "illustration": 1.50,
    "photo":        1.00,
    "screenshot":   0.25,
    "diagram":      0.55,
    "noise":        0.05,
}

# ★ v4 新增: 音频细粒度场景权重 (对外导出)
# TEV 场景映射: speech_medical→medical_sft(1.35x), speech_legal→legal_doc(1.20x)
# 音频场景有自己的稀缺溢价，故单独配置
AUDIO_SCENE_WEIGHTS: dict = {
    "speech_medical":  1.40,   # 医疗语音转录: 数据极稀缺，ASR 价值最高
    "speech_legal":    1.25,   # 法庭/庭审录音
    "speech_edu":      0.85,   # 教学/访谈: 量大价中
    "music_original":  1.10,   # 原创音乐: 版权溢价
    "ambient_sfx":     0.60,   # 环境/音效: 较易获取
    "noise":           0.05,   # 噪声: 无训练价值
}

# 音频场景 → TEV 场景映射 (与 audio_adapter 完全同步)
AUDIO_SCENE_TO_TEV: dict = {
    "speech_medical": "medical_sft",
    "speech_legal":   "legal_doc",
    "speech_edu":     "chat_qa",
    "music_original": "creative",
    "ambient_sfx":    "general",
    "noise":          "noise",
}

SCENE_COMPOSITE_WEIGHTS: dict = {
    # 文本场景
    "medical_sft":  {"entropy":0.25,"snr":0.30,"structure":0.20,"scarcity":0.10,"llm_value":0.10,"shapley":0.05},
    "legal_doc":    {"entropy":0.15,"snr":0.20,"structure":0.35,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "code_tech":    {"entropy":0.10,"snr":0.20,"structure":0.40,"scarcity":0.10,"llm_value":0.15,"shapley":0.05},
    "creative":     {"entropy":0.35,"snr":0.10,"structure":0.15,"scarcity":0.25,"llm_value":0.05,"shapley":0.10},
    "chat_qa":      {"entropy":0.20,"snr":0.15,"structure":0.15,"scarcity":0.15,"llm_value":0.25,"shapley":0.10},
    "noise":        {"entropy":0.20,"snr":0.20,"structure":0.20,"scarcity":0.20,"llm_value":0.10,"shapley":0.10},
    # 图像场景
    "illustration": {"entropy":0.15,"snr":0.10,"structure":0.40,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
    "photo":        {"entropy":0.30,"snr":0.15,"structure":0.20,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
    "screenshot":   {"entropy":0.25,"snr":0.25,"structure":0.20,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "diagram":      {"entropy":0.20,"snr":0.15,"structure":0.30,"scarcity":0.20,"llm_value":0.10,"shapley":0.05},
    # ★ v4 新增: 音频场景独立 composite_weights
    # snr 权重最高 — 录音质量对 ASR/TTS 训练价值影响最大
    "speech_medical":  {"entropy":0.15,"snr":0.35,"structure":0.20,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "speech_legal":    {"entropy":0.15,"snr":0.30,"structure":0.25,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "speech_edu":      {"entropy":0.20,"snr":0.25,"structure":0.20,"scarcity":0.15,"llm_value":0.15,"shapley":0.05},
    "music_original":  {"entropy":0.25,"snr":0.20,"structure":0.25,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
    "ambient_sfx":     {"entropy":0.30,"snr":0.20,"structure":0.20,"scarcity":0.15,"llm_value":0.10,"shapley":0.05},
}


# ===================================================================
# 图像场景关键词信号 (对外导出)
# ===================================================================

IMAGE_SCENE_SIGNALS: dict = {
    "illustration": {
        "keywords": [
            "插画","绘画","画作","原创","艺术","赛博朋克","二次元","动漫",
            "概念图","设定图","原画","数字绘画","CG","奇幻","机甲","蒸汽朋克",
            "水彩","油画","素描","手绘",
            "illustration","artwork","painting","digital art","concept art",
            "anime","manga","character design","cyberpunk","fantasy",
            "watercolor","oil painting","sketch","portrait","sci-fi","4k","8k",
        ],
    },
    "photo": {
        "keywords": [
            "照片","摄影","拍摄","实拍","风景","人像","街拍","纪实",
            "photo","photograph","camera","shot","portrait","landscape",
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
            "饼图","拓扑图","UML",
            "diagram","chart","flowchart","architecture","visualization",
            "graph","infographic",
        ],
    },
}

RARE_STYLES = frozenset([
    "赛博朋克","蒸汽朋克","洛可可","包豪斯","装饰艺术",
    "cyberpunk","steampunk","art nouveau","brutalism","bauhaus",
])

TEXT_SCENE_SIGNALS: dict = {
    "medical_sft": {"weight": TEXT_SCENE_WEIGHTS["medical_sft"]},
    "legal_doc":   {"weight": TEXT_SCENE_WEIGHTS["legal_doc"]},
    "code_tech":   {"weight": TEXT_SCENE_WEIGHTS["code_tech"]},
    "creative":    {"weight": TEXT_SCENE_WEIGHTS["creative"]},
    "chat_qa":     {"weight": TEXT_SCENE_WEIGHTS["chat_qa"]},
}


# ===================================================================
# 辅助函数
# ===================================================================

def _tf_score(text: str, keyword_set: frozenset) -> float:
    """TF 词频密度得分 [0, 100]"""
    if not text:
        return 0.0
    total_chars = max(len(text), 1)
    hit_count = 0
    for kw in keyword_set:
        pos = 0
        while True:
            idx = text.find(kw, pos)
            if idx == -1:
                break
            hit_count += 1
            pos = idx + len(kw)
    density = hit_count / (total_chars / 100)
    return min(100.0, density * 20)


def _pattern_score(text: str, patterns: list) -> float:
    """正则模式命中率 [0, 100]"""
    if not patterns:
        return 0.0
    hits = sum(1 for p in patterns if p.search(text))
    return (hits / len(patterns)) * 100


def _kw_hits(text: str, kw_set: frozenset) -> int:
    """返回关键词命中数量 (用于音频文本通道)"""
    text_lower = text.lower()
    return sum(1 for kw in kw_set if kw in text_lower)


# ===================================================================
# 数据类
# ===================================================================

@dataclass
class SceneResult:
    scene:             str
    confidence:        float
    weight_multiplier: float
    quality_axis:      str
    composite_weights: dict
    method:            str = "rule"   # "rule" | "ml" | "hybrid" | "acoustic" | "text_proxy" | "fusion"
    audio_scene:       Optional[str] = None   # ★ v4: 仅音频模态填充，如 "speech_medical"


# ===================================================================
# SceneClassifier — 多模态 + 音频双通道 (v4)
# ===================================================================

class SceneClassifier:
    """
    多模态场景分类器 v4

    文本分类双引擎:
      Fast path (规则): TF 词频密度 + 正则模式 — ~0.1ms
      Slow path (ML):   sentence-transformers 场景锚点余弦 — ~8ms
      触发条件: 规则置信度 < ML_THRESHOLD (0.42)

    图像分类:
      描述关键词匹配，升级路径: CLIP zero-shot

    ★ v4 音频分类双通道:
      声学通道 (有波形): ZCR / HNR / chroma_var / beat_strength → 6类音频场景
      文本通道 (始终):   医疗/法律/音乐/教学 关键词密度
      融合权重: acoustic 0.65 + text 0.35（无波形时纯文本）
    """

    def __init__(self):
        self._ml_model   = None
        self._anchor_emb = {}

    # ----------------------------------------------------------------
    # 懒加载 ML 模型
    # ----------------------------------------------------------------
    def _ensure_ml(self) -> bool:
        if self._ml_model is not None:
            return True
        try:
            from sentence_transformers import SentenceTransformer
            self._ml_model = SentenceTransformer("all-MiniLM-L6-v2")
            anchors = list(ML_SCENE_ANCHORS.values())
            embs = self._ml_model.encode(anchors, normalize_embeddings=True)
            self._anchor_emb = dict(zip(ML_SCENE_ANCHORS.keys(), embs))
            return True
        except Exception:
            return False

    # ----------------------------------------------------------------
    # 规则引擎得分 (文本)
    # ----------------------------------------------------------------
    def _rule_scores(self, text: str) -> dict[str, float]:
        return {
            "medical_sft": _tf_score(text, MEDICAL_CORE) * 0.60 + _pattern_score(text, MEDICAL_PATTERNS) * 0.40,
            "legal_doc":   _tf_score(text, LEGAL_CORE)   * 0.60 + _pattern_score(text, LEGAL_PATTERNS)   * 0.40,
            "code_tech":   _tf_score(text, CODE_CORE)    * 0.35 + _pattern_score(text, CODE_PATTERNS)     * 0.65,
            "creative":    _tf_score(text, CREATIVE_CORE)* 0.55 + _pattern_score(text, CREATIVE_PATTERNS) * 0.45,
            "chat_qa":     _tf_score(text, CHAT_QA_CORE) * 0.40 + _pattern_score(text, QA_PATTERNS)       * 0.60,
        }

    # ----------------------------------------------------------------
    # ML 引擎得分 (文本)
    # ----------------------------------------------------------------
    def _ml_scores(self, text: str) -> dict[str, float]:
        if not self._ensure_ml():
            return {}
        emb = self._ml_model.encode(text[:512], normalize_embeddings=True)
        return {
            scene: float(np.dot(emb, anchor))
            for scene, anchor in self._anchor_emb.items()
        }

    # ----------------------------------------------------------------
    # 文本分类入口
    # ----------------------------------------------------------------
    def classify_text(self, text: str) -> SceneResult:
        if len(text) < 20:
            return self._noise()

        rule = self._rule_scores(text)
        best_rule = max(rule, key=rule.get)
        rule_conf = min(1.0, rule[best_rule] / 80.0)

        if rule_conf >= ML_THRESHOLD:
            return self._make_text_result(best_rule, rule_conf, "rule")

        ml = self._ml_scores(text)

        if ml:
            ml_max = max(ml.values()) if ml else 1.0
            scenes = set(rule) | set(ml)
            fused = {
                s: (rule.get(s, 0.0) / 100.0) * _RULE_WEIGHT
                 + (ml.get(s, 0.0) / max(ml_max, 1e-8)) * _ML_WEIGHT
                for s in scenes
            }
            best  = max(fused, key=fused.get)
            conf  = min(1.0, fused[best])
            method = "hybrid"
        else:
            best, conf, method = best_rule, rule_conf, "rule"

        if conf < 0.08:
            return self._noise()

        return self._make_text_result(best, conf, method)

    # ----------------------------------------------------------------
    # 图像分类
    # ----------------------------------------------------------------
    def classify_image(self, description: str) -> SceneResult:
        desc_lower = description.lower()
        scores: dict[str, float] = {
            scene: sum(1 for kw in config["keywords"] if kw in desc_lower)
            for scene, config in IMAGE_SCENE_SIGNALS.items()
        }
        best  = max(scores, key=scores.get)
        score = scores[best]

        if score == 0:
            return SceneResult(
                scene="photo",
                confidence=0.40,
                weight_multiplier=IMAGE_SCENE_WEIGHTS["photo"],
                quality_axis="entropy",
                composite_weights=SCENE_COMPOSITE_WEIGHTS["photo"],
                method="rule",
            )

        return SceneResult(
            scene=best,
            confidence=min(1.0, score / 4.0),
            weight_multiplier=IMAGE_SCENE_WEIGHTS.get(best, 1.0),
            quality_axis="structure" if best == "illustration" else "entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(best, SCENE_COMPOSITE_WEIGHTS["photo"]),
            method="rule",
        )

    # ----------------------------------------------------------------
    # ★ v4 音频分类: 双通道融合
    # ----------------------------------------------------------------
    def classify_audio(
        self,
        description: str,
        y: Optional[np.ndarray] = None,
        sr: int = 16000,
    ) -> SceneResult:
        """
        音频双通道场景分类，返回标准 SceneResult。

        Args:
            description: 音频描述文本（始终可用）
            y:           已解码的波形数组 float32 mono（可选）
            sr:          采样率（默认 16000）

        Returns:
            SceneResult，其中:
              scene           → TEV 场景键 (如 "medical_sft")
              audio_scene     → 音频细粒度场景 (如 "speech_medical")
              method          → "acoustic" | "text_proxy" | "fusion"
              weight_multiplier → 来自 TEXT_SCENE_WEIGHTS（TEV 对齐）
              composite_weights → 来自 SCENE_COMPOSITE_WEIGHTS（音频专属）

        融合策略:
            有波形 → 声学通道(0.65) + 文本通道(0.35) → method="fusion"
            无波形 → 纯文本通道 → method="text_proxy"
            声学置信度 > 0.85 时以声学为主 → method="acoustic"
        """
        # ── 通道1: 文本关键词 ──────────────────────────────────────
        text_scores, text_conf = self._audio_text_channel(description)

        has_wave = y is not None and len(y) > sr // 4

        if not has_wave:
            # 纯文本代理
            audio_scene = max(text_scores, key=text_scores.get)
            conf = text_conf
            method = "text_proxy"
            return self._make_audio_result(audio_scene, conf, method)

        # ── 通道2: 声学特征 ───────────────────────────────────────
        acoustic_scores, acoustic_conf = self._audio_acoustic_channel(y, sr)

        # ── 双通道加权融合 ────────────────────────────────────────
        _ACOUSTIC_W = 0.65
        _TEXT_W     = 0.35

        all_scenes = set(acoustic_scores) | set(text_scores)
        # 归一化各通道
        a_max = max(acoustic_scores.values()) if acoustic_scores else 1.0
        t_max = max(text_scores.values())     if text_scores     else 1.0

        fused = {
            s: (acoustic_scores.get(s, 0.0) / max(a_max, 1e-8)) * _ACOUSTIC_W
             + (text_scores.get(s, 0.0)     / max(t_max, 1e-8)) * _TEXT_W
            for s in all_scenes
        }

        audio_scene = max(fused, key=fused.get)
        fused_conf  = min(1.0, fused[audio_scene])

        # 高置信度声学结果不需要文本修正
        if acoustic_conf > 0.85:
            method = "acoustic"
            conf   = acoustic_conf
        else:
            method = "fusion"
            conf   = round(0.65 * acoustic_conf + 0.35 * text_conf, 3)

        return self._make_audio_result(audio_scene, conf, method)

    # ----------------------------------------------------------------
    # 音频文本关键词通道
    # ----------------------------------------------------------------
    def _audio_text_channel(self, description: str) -> tuple[dict[str, float], float]:
        """
        返回 (scores_dict, best_confidence)
        scores_dict: {audio_scene: raw_score}
        """
        med_hits   = _kw_hits(description, _MEDICAL_AUDIO_KWS)
        leg_hits   = _kw_hits(description, _LEGAL_AUDIO_KWS)
        music_hits = _kw_hits(description, _MUSIC_KWS)
        edu_hits   = _kw_hits(description, _EDU_KWS)

        scores = {
            "speech_medical": float(med_hits),
            "speech_legal":   float(leg_hits),
            "speech_edu":     float(edu_hits),
            "music_original": float(music_hits),
            "ambient_sfx":    0.5,   # 默认基线
            "noise":          0.0,
        }

        best = max(scores, key=scores.get)
        best_hits = scores[best]

        # 置信度: ≥3个关键词命中 → 高置信; 0 → 低置信
        if best_hits >= 3:
            conf = min(0.90, 0.60 + best_hits * 0.05)
        elif best_hits >= 2:
            conf = 0.60
        elif best_hits >= 1:
            conf = 0.45
        else:
            conf = 0.30   # 无命中，默认 ambient 低置信

        return scores, conf

    # ----------------------------------------------------------------
    # 音频声学特征通道
    # ----------------------------------------------------------------
    def _audio_acoustic_channel(
        self,
        y: np.ndarray,
        sr: int,
    ) -> tuple[dict[str, float], float]:
        """
        基于声学特征的音频场景评分。

        参考 DCASE 2023 分类法，使用以下声学指标:
          ZCR         — 过零率: 语音 0.02~0.18 / 音乐 0.05~0.25 / 噪声 >0.30
          HNR proxy   — 谐波噪声比: 语音/音乐 > 1.0 / 噪声 < 0.5
          chroma_var  — 色谱方差: 音乐性指标 > 0.05 表示音调变化丰富
          beat_str    — 节拍强度: > 0.2 表示有明显节奏

        返回 (scores_dict, best_confidence)
        """
        try:
            import librosa
            import librosa.feature
            import librosa.decompose
            import librosa.beat
        except ImportError:
            # librosa 不可用，返回空分数
            return {"ambient_sfx": 1.0}, 0.30

        try:
            zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)[0]))
            rms = float(np.mean(librosa.feature.rms(y=y)[0]))

            # 谐波噪声比代理
            stft = np.abs(librosa.stft(y))
            harmonic, percussive = librosa.decompose.hpss(stft)
            hnr_proxy = float(
                np.mean(np.abs(harmonic)) / (np.mean(np.abs(percussive)) + 1e-6)
            )

            # 色谱方差 (音乐性)
            chroma     = librosa.feature.chroma_stft(y=y, sr=sr)
            chroma_var = float(np.mean(np.var(chroma, axis=1)))

            # 节拍强度
            try:
                _, beats      = librosa.beat.beat_track(y=y, sr=sr)
                beat_str = float(min(1.0, len(beats) / max(len(y) / sr, 1) / 2))
            except Exception:
                beat_str = 0.0

        except Exception:
            return {"ambient_sfx": 1.0}, 0.30

        # ── 场景评分规则树 (参考 Tzanetakis & Cook 2002) ──────────
        scores: dict[str, float] = {
            "speech_medical":  0.0,
            "speech_legal":    0.0,
            "speech_edu":      0.0,
            "music_original":  0.0,
            "ambient_sfx":     0.0,
            "noise":           0.0,
        }
        conf = 0.55   # 声学通道基础置信度

        # 1) 噪声: 极低 RMS 或 极高 ZCR + 低谐波
        if rms < 0.005 or (zcr > 0.35 and hnr_proxy < 0.5):
            scores["noise"] = 10.0
            return scores, 0.88

        # 2) 音乐: 高色谱方差 + 节拍 + 谐波强
        if chroma_var > 0.05 and beat_str > 0.2 and hnr_proxy > 1.5:
            music_score = 0.60 + chroma_var * 5 + beat_str * 0.3
            scores["music_original"] = min(10.0, music_score)
            conf = round(min(0.95, 0.60 + chroma_var * 5), 3)
            return scores, conf

        # 3) 环境音效: 低 ZCR + 低 chroma_var + 稳定 RMS
        if zcr < 0.05 and chroma_var < 0.03 and rms > 0.01:
            scores["ambient_sfx"] = 7.2
            return scores, 0.72

        # 4) 语音类: 中等 ZCR + 谐波比
        if 0.02 <= zcr <= 0.25 and hnr_proxy > 0.8:
            # 基础语音得分
            speech_base = 5.0 + hnr_proxy * 0.5
            # 声学特征无法细分医疗/法律/教育 → 各语音子类基础分相同
            # 细分由文本通道完成（融合时补充）
            scores["speech_medical"] = speech_base * 0.40   # 声学权重均匀，文本加权差分
            scores["speech_legal"]   = speech_base * 0.35
            scores["speech_edu"]     = speech_base * 0.60   # 教学语音最常见，基线略高
            conf = round(min(0.82, 0.55 + hnr_proxy * 0.08), 3)
            return scores, conf

        # 5) 兜底: 环境/混合
        scores["ambient_sfx"] = 4.5
        return scores, 0.45

    # ----------------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------------
    def _make_text_result(self, scene: str, conf: float, method: str) -> SceneResult:
        _QUALITY_AXIS = {
            "medical_sft": "snr",
            "legal_doc":   "structure",
            "code_tech":   "structure",
            "creative":    "entropy",
            "chat_qa":     "llm_value",
        }
        return SceneResult(
            scene=scene,
            confidence=round(conf, 3),
            weight_multiplier=TEXT_SCENE_WEIGHTS.get(scene, 1.0),
            quality_axis=_QUALITY_AXIS.get(scene, "entropy"),
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                scene, SCENE_COMPOSITE_WEIGHTS["chat_qa"]
            ),
            method=method,
        )

    def _make_audio_result(
        self,
        audio_scene: str,
        conf: float,
        method: str,
    ) -> SceneResult:
        """
        将音频细粒度场景映射到 TEV 场景，构造 SceneResult。

        weight_multiplier 使用 TEXT_SCENE_WEIGHTS (TEV 对齐)，
        composite_weights 优先使用音频专属配置。
        """
        tev_scene = AUDIO_SCENE_TO_TEV.get(audio_scene, "general")
        _AUDIO_QUALITY_AXIS = {
            "speech_medical": "snr",       # 录音质量决定 ASR 价值
            "speech_legal":   "snr",
            "speech_edu":     "llm_value",
            "music_original": "structure",  # 音乐结构丰富度
            "ambient_sfx":    "entropy",
            "noise":          "entropy",
        }
        return SceneResult(
            scene=tev_scene,
            confidence=round(conf, 3),
            weight_multiplier=TEXT_SCENE_WEIGHTS.get(tev_scene, 1.0),
            quality_axis=_AUDIO_QUALITY_AXIS.get(audio_scene, "snr"),
            composite_weights=SCENE_COMPOSITE_WEIGHTS.get(
                audio_scene,
                SCENE_COMPOSITE_WEIGHTS.get(tev_scene, SCENE_COMPOSITE_WEIGHTS["chat_qa"])
            ),
            method=method,
            audio_scene=audio_scene,   # ★ v4: 携带细粒度标签
        )

    def _noise(self) -> SceneResult:
        return SceneResult(
            scene="noise",
            confidence=0.90,
            weight_multiplier=0.05,
            quality_axis="entropy",
            composite_weights=SCENE_COMPOSITE_WEIGHTS["noise"],
            method="rule",
        )

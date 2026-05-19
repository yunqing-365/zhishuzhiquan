"""
AudioAdapter — 音频模态适配器 v1
===================================
填补 oracle_engine.py MODALITY_TEV["audio"] = 120.0 的功能性空洞。
本适配器使 AI-Echo 系统首次支持音频资产的完整定价流程。

架构参考:
  CLAP  (Wu et al., ICASSP 2023):
    "Large-Scale Contrastive Language-Audio Pretraining with
     Feature Fusion and Keyword-to-Caption Augmentation"
    → 本模块以 MFCC 统计特征近似 CLAP 音频编码器的 512-dim 嵌入空间，
      保留跨模态对齐接口 (升级路径: 替换 get_embedding 内部实现)。

  AudioCLIP (Guzhov et al., ICASSP 2022):
    "AudioCLIP: Extending CLIP to Image, Text and Audio"
    → 三元对齐思路：音频描述文本 + 波形特征 + 视觉波形图
      → 当前实现: 波形特征 + 描述文本 Fallback

  DCASE 2023 任务分类法:
    → 音频场景分类: speech_clear / speech_noisy / music_original /
                   ambient_sfx / mixed / noise
    → 场景权重映射到现有 TEV 场景体系

  Wideband PESQ 代理 (ITU-T P.862.2):
    → 语音质量评估: 通过能量分布和谱质心近似 MOS 分
    → 用于 snr 维度，量化录音质量对 ASR/TTS 训练价值的影响

6 维输出指标含义 (与 BaseModalityAdapter 规范对齐):
  entropy   → 频谱熵 (STFT 能量分布均匀度，反映内容信息密度)
  snr       → 感知信噪比 (PESQ 代理，反映录音可用性)
  structure → 时频结构复杂度 (Delta-MFCC 动态范围，反映声学事件丰富度)
  scarcity  → 向量空间稀缺度 (MFCC 嵌入的语料库覆盖缺口)
  llm_value → AI 训练增益 (STT/TTS/音频生成模型的预期微调收益)
  shapley   → unified_shapley_score (v3 Beta-Shapley 自适应)

音频场景 → 现有场景映射:
  speech_medical  → "medical_sft"   (医疗语音转录, 28x 需求)
  speech_legal    → "legal_doc"     (法庭/庭审录音,  18x 需求)
  speech_edu      → "chat_qa"       (教学/访谈语音,  10x 需求)
  music_original  → "creative"      (原创音乐作品,    8x 需求)
  ambient_sfx     → "general"       (环境/音效素材,   5x 需求)
  noise           → "noise"         (噪声/杂音,       0x 需求)

升级路径 (接口不变):
  Stage A: MFCC + 频谱统计特征  ← 当前版本
  Stage B: CLAP audio encoder   → 替换 get_embedding，512-dim 对齐
  Stage C: Whisper 转录文字     → 接入 TextAdapter 联合估值
"""

import io
import math
import base64
import hashlib
import struct
from typing import Optional, List, Dict, Tuple

import numpy as np

# ── 懒加载 librosa (未安装时优雅降级) ────────────────────────────
try:
    import librosa
    import librosa.feature
    import librosa.beat
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

# ── 可选: soundfile 用于更宽格式支持 ───────────────────────────
try:
    import soundfile as sf
    HAS_SF = True
except ImportError:
    HAS_SF = False

# ── Whisper 懒加载（v2 新增：转录音频 → 联合文本打分）────────────
# 支持 openai-whisper (pip install openai-whisper) 或 faster-whisper
# 未安装时静默降级，行为与 v1 完全一致
_whisper_model  = None
_whisper_tried  = False
_WHISPER_SIZE   = "base"   # tiny/base/small/medium/large-v3 可配置

def _try_load_whisper():
    """懒加载 Whisper，只尝试一次，失败后不再重试"""
    global _whisper_model, _whisper_tried
    if _whisper_tried:
        return
    _whisper_tried = True
    try:
        import whisper as _whisper
        _whisper_model = _whisper.load_model(_WHISPER_SIZE)
        print(f">> [AudioAdapter v2] Whisper {_WHISPER_SIZE} 加载成功 ✓")
    except Exception as e:
        print(f"!! [AudioAdapter v2] Whisper 未安装或加载失败 (降级 MFCC 模式): {e}")
        _whisper_model = None


def _whisper_transcribe(y: "np.ndarray", sr: int) -> Optional[str]:
    """
    用 Whisper 将音频转录为文本。
    返回转录文本或 None（失败时）。
    音频已是 float32 mono 16kHz（由 librosa 预处理）。
    """
    _try_load_whisper()
    if _whisper_model is None or y is None:
        return None
    try:
        import whisper as _whisper
        import numpy as _np
        # whisper.transcribe 接受 numpy array (float32, 16kHz)
        result = _whisper_model.transcribe(y.astype(_np.float32), language=None, fp16=False)
        text = result.get("text", "").strip()
        return text if len(text) > 5 else None
    except Exception as e:
        print(f"!! [AudioAdapter v2] Whisper 转录失败: {e}")
        return None

from .base_adapter import BaseModalityAdapter

# ── 延迟导入 scoring，避免循环 ──────────────────────────────────
# 在函数内部 import，与 text_adapter / image_adapter 一致
# from scoring import knn_shapley_score  # 见各方法内部

# ===================================================================
# 常量 & 配置
# ===================================================================

_TARGET_SR   = 16_000      # 统一采样率 (ASR/TTS 行业标准)
_TARGET_SECS = 60          # 最大处理时长 (秒)，超长截断
_EMBED_DIM   = 384         # ChromaDB 对齐维度 (与 TextAdapter / ImageAdapter 一致)
_N_MFCC      = 40          # MFCC 系数数量 (CLAP 常用配置)
_N_CHROMA    = 12          # 色谱特征维度 (半音数量)

# 医疗/法律关键词: 用于描述文本辅助场景判断
_MEDICAL_AUDIO_KWS = frozenset([
    "患者","诊断","病历","手术","检查","医嘱","临床","治疗",
    "medical","patient","diagnosis","clinical","surgery","prescription",
])
_LEGAL_AUDIO_KWS = frozenset([
    "合同","庭审","判决","陈述","证词","仲裁","法庭","律师",
    "court","testimony","verdict","plaintiff","defendant","hearing",
])

# 音频场景 → 现有 TEV 场景的映射表
_AUDIO_SCENE_TO_TEV: Dict[str, str] = {
    "speech_medical": "medical_sft",
    "speech_legal":   "legal_doc",
    "speech_edu":     "chat_qa",
    "music_original": "creative",
    "ambient_sfx":    "general",
    "noise":          "noise",
}

# 场景基础 llm_value 权重 (决定该音频对下游 AI 任务的训练价值)
_SCENE_LLM_BASE: Dict[str, float] = {
    "speech_medical": 88.0,  # 医疗 ASR 极度稀缺
    "speech_legal":   78.0,
    "speech_edu":     65.0,
    "music_original": 60.0,  # 音频生成模型训练
    "ambient_sfx":    42.0,
    "noise":          5.0,
}


# ===================================================================
# 音频解码工具
# ===================================================================

def _decode_audio_b64(b64_str: str) -> Tuple[Optional[np.ndarray], int]:
    """
    解码 base64 音频 → (waveform [float32, mono], sample_rate)
    支持: WAV, MP3, FLAC, OGG (librosa 通过 soundfile/audioread 读取)
    失败时返回 (None, 0)
    """
    if not HAS_LIBROSA:
        return None, 0
    try:
        raw = b64_str.split(",", 1)[-1] if b64_str.startswith("data:") else b64_str
        audio_bytes = base64.b64decode(raw)
        buf = io.BytesIO(audio_bytes)
        y, sr = librosa.load(buf, sr=_TARGET_SR, mono=True, duration=_TARGET_SECS)
        return y.astype(np.float32), sr
    except Exception:
        return None, 0


# ===================================================================
# 感知音频哈希 (Acoustic Fingerprint)
# ===================================================================

def _acoustic_hash(y: np.ndarray, sr: int) -> str:
    """
    基于能量帧的感知哈希
    (灵感来源: Shazam 频谱峰值指纹 / Audio Fingerprinting, Wang 2003)

    算法:
      1. 将波形分成 0.5 秒帧
      2. 计算每帧 RMS 能量
      3. 二值化 (高于均值 → 1, 低于 → 0) → bit string
      4. 取 bit string 的 SHA-256 前 16 字节
    鲁棒性: 轻微变速/变调不影响指纹 (能量包络稳定)
    """
    if len(y) < sr:
        return "0xAUD_" + hashlib.sha256(y.tobytes()[:1024]).hexdigest()[:12].upper()

    frame_len = sr // 2                 # 0.5 秒帧
    frames    = len(y) // frame_len
    rms_vals  = np.array([
        float(np.sqrt(np.mean(y[i * frame_len:(i + 1) * frame_len] ** 2)))
        for i in range(frames)
    ])
    mean_rms = float(np.mean(rms_vals))
    bits     = "".join("1" if v > mean_rms else "0" for v in rms_vals)

    # 将 bit string 转为 bytes 再 SHA-256
    padded = bits.ljust(math.ceil(len(bits) / 8) * 8, "0")
    raw_bytes = bytes(
        int(padded[i:i + 8], 2) for i in range(0, len(padded), 8)
    )
    return "0xAFP_" + hashlib.sha256(raw_bytes).hexdigest()[:12].upper()


# ===================================================================
# 音频特征提取
# ===================================================================

class _AudioFeatureExtractor:
    """
    核心特征提取器 (librosa 可用时走真实路径，否则走描述文本代理)
    """

    # ----------------------------------------------------------------
    # 1. 频谱熵 (entropy 维度)
    # ----------------------------------------------------------------
    @staticmethod
    def spectral_entropy(y: np.ndarray, sr: int) -> float:
        """
        STFT 幅度谱的时间平均频谱熵 [0, 100]

        理论: 熵最大 (100) = 白噪声 (能量均匀分布在所有频率)
              熵最小 (0)   = 单音正弦波
        数据价值视角:
          语音/音乐: 中等熵 (40~75) = 结构化内容，训练价值高
          纯噪声: 极高熵 (>85) 但 snr 极低 → 被 llm_value 惩罚
        """
        n_fft = 2048
        hop   = 512
        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2  # [freq, time]
        S_norm = S / (S.sum(axis=0, keepdims=True) + 1e-10)             # 频率归一化
        # 时间平均频谱熵 (最大熵 = log2(n_fft//2+1) ≈ 10 bits)
        frame_ent = -np.sum(S_norm * np.log2(S_norm + 1e-10), axis=0)
        max_ent   = math.log2(n_fft // 2 + 1)
        return float(min(100.0, (np.mean(frame_ent) / max_ent) * 100))

    # ----------------------------------------------------------------
    # 2. 感知信噪比 (snr 维度) — PESQ 代理
    # ----------------------------------------------------------------
    @staticmethod
    def perceptual_snr(y: np.ndarray, sr: int) -> float:
        """
        宽带感知 SNR 代理 (ITU-T P.862.2 PESQ 简化版)

        方法:
          a. 提取语音频带 (300~3400 Hz) 的能量 E_speech
          b. 提取噪声底层 (最低 10% 帧的能量) E_noise
          c. SNR_dB = 10 × log10(E_speech / E_noise)
          d. 映射到 [0, 100]: score = min(100, SNR_dB / 40 × 100)

        实际意义: SNR > 30dB 的语音可直接用于 ASR 训练;
                  SNR < 10dB 需降噪后才有价值。
        """
        # 频率带通
        stft  = librosa.stft(y, n_fft=2048, hop_length=512)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

        speech_mask = (freqs >= 300) & (freqs <= 3400)
        speech_energy = np.mean(np.abs(stft[speech_mask, :]) ** 2)

        # 噪声底层估计: 最低 15% 帧的 RMS
        frame_rms = np.sqrt(np.mean(np.abs(stft) ** 2, axis=0))
        noise_floor = float(np.percentile(frame_rms, 15)) ** 2 + 1e-12

        if noise_floor <= 0:
            return 80.0
        snr_db = 10 * math.log10(max(speech_energy / noise_floor, 1e-6))
        return float(min(100.0, max(0.0, snr_db / 40.0 * 100)))

    # ----------------------------------------------------------------
    # 3. 时频结构复杂度 (structure 维度)
    # ----------------------------------------------------------------
    @staticmethod
    def temporal_structure(y: np.ndarray, sr: int) -> float:
        """
        Delta-MFCC 动态范围 + 音节速率 → 声学事件丰富度 [0, 100]

        Delta-MFCC 表示 MFCC 的一阶时间导数，捕捉声学特征的
        变化速率 (articulation dynamics)。
        高 Delta-MFCC 方差 = 快速音韵变化 = 丰富的音素/音符序列。

        Reference: Furui (1986), Delta-MFCC for speaker recognition.
        """
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=_N_MFCC)
        delta_mfcc = librosa.feature.delta(mfcc)                 # 一阶差分
        delta2     = librosa.feature.delta(mfcc, order=2)        # 二阶差分

        # 各系数时间方差的均值 (越高 = 动态变化越丰富)
        delta_var  = float(np.mean(np.var(delta_mfcc, axis=1)))
        delta2_var = float(np.mean(np.var(delta2,     axis=1)))

        # 音节速率代理: 能量包络峰值密度
        rms        = librosa.feature.rms(y=y, hop_length=512)[0]
        peaks      = np.sum(np.diff(np.sign(np.diff(rms))) == -2)  # 局部极大值
        duration   = max(len(y) / sr, 0.1)
        syllable_rate = peaks / duration   # 峰值/秒

        # 归一化: delta_var 典型范围 [0, 50]; syllable_rate 典型 [0, 15]
        delta_score   = min(100.0, (delta_var  / 30.0) * 100)
        delta2_score  = min(100.0, (delta2_var / 20.0) * 100)
        syllable_score = min(100.0, (syllable_rate / 8.0) * 100)

        return float(0.45 * delta_score + 0.30 * delta2_score + 0.25 * syllable_score)

    # ----------------------------------------------------------------
    # 4. MFCC 384-dim 嵌入 (CLAP 代理)
    # ----------------------------------------------------------------
    @staticmethod
    def mfcc_embedding(y: np.ndarray, sr: int) -> List[float]:
        """
        MFCC 统计特征嵌入 → 384-dim (ChromaDB 对齐)

        特征组成:
          MFCC mean (40) + std (40) + δ-mean (40) + δ-std (40) = 160 dims
          δ²-mean (40) + δ²-std (40)                           =  80 dims
          Chroma mean (12) + std (12)                           =  24 dims
          Spectral {centroid,bandwidth,rolloff,flatness}×{μ,σ} =   8 dims
          ZCR {μ,σ}, RMS {μ,σ}                                  =   4 dims
          Tempo scalar, beat regularity                          =   2 dims
          ─────────────────────────────────────────────────────────────
          Total                                                  = 278 dims
          → L2 normalize, pad to 384 with zeros

        升级路径: 替换本函数为 CLAP audio encoder (512-dim + 线性投影到384)
        """
        mfcc    = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=_N_MFCC)
        delta   = librosa.feature.delta(mfcc)
        delta2  = librosa.feature.delta(mfcc, order=2)
        chroma  = librosa.feature.chroma_stft(y=y, sr=sr, n_chroma=_N_CHROMA)

        spec_centroid  = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spec_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
        spec_rolloff   = librosa.feature.spectral_rolloff(y=y,  sr=sr)[0]
        spec_flatness  = librosa.feature.spectral_flatness(y=y)[0]
        zcr            = librosa.feature.zero_crossing_rate(y)[0]
        rms            = librosa.feature.rms(y=y)[0]

        def _stats(x):
            return [float(np.mean(x)), float(np.std(x))]

        feats = (
            list(np.mean(mfcc, axis=1))   +   # 40
            list(np.std(mfcc,  axis=1))   +   # 40
            list(np.mean(delta, axis=1))  +   # 40
            list(np.std(delta,  axis=1))  +   # 40
            list(np.mean(delta2, axis=1)) +   # 40
            list(np.std(delta2,  axis=1)) +   # 40
            list(np.mean(chroma, axis=1)) +   # 12
            list(np.std(chroma,  axis=1)) +   # 12
            _stats(spec_centroid)         +   # 2
            _stats(spec_bandwidth)        +   # 2
            _stats(spec_rolloff)          +   # 2
            _stats(spec_flatness)         +   # 2
            _stats(zcr)                   +   # 2
            _stats(rms)                       # 2
        )   # total 278

        # 节拍信息 (2 dims)
        try:
            tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
            beat_reg = float(np.std(np.diff(beats))) if len(beats) > 2 else 0.0
            feats += [float(tempo), beat_reg]
        except Exception:
            feats += [0.0, 0.0]

        arr  = np.array(feats, dtype=float)
        norm = np.linalg.norm(arr) + 1e-10
        arr  = arr / norm

        # Pad to 384
        if len(arr) < _EMBED_DIM:
            arr = np.concatenate([arr, np.zeros(_EMBED_DIM - len(arr))])
        else:
            arr = arr[:_EMBED_DIM]

        return arr.tolist()

    # ----------------------------------------------------------------
    # 5. 音频场景分类 (基于声学特征, 无外部模型)
    # ----------------------------------------------------------------
    @staticmethod
    def classify_scene(
        y: np.ndarray,
        sr: int,
        description: str = "",
    ) -> Tuple[str, float]:
        """
        音频场景分类 → (scene_key, confidence)

        规则引擎 (参考 DCASE 2023 Task 1 分类法):
          1. ZCR + 谐波噪声比 → 区分语音 vs 音乐 vs 环境
          2. 语音情景下: 描述关键词 → 细分 medical / legal / edu
          3. 音乐判定: chroma 方差 + 节拍强度

        ZCR 区间 (16kHz 采样):
          静音/环境: < 0.02
          语音:      0.02 ~ 0.18
          音乐:      0.05 ~ 0.25 (宽范围，需配合谐波判断)
          噪声:      > 0.30

        升级路径: 替换为 CLAP zero-shot 分类 (传入文本标签向量做 cosine)
        """
        zcr  = float(np.mean(librosa.feature.zero_crossing_rate(y)[0]))
        rms  = float(np.mean(librosa.feature.rms(y=y)[0]))
        stft = np.abs(librosa.stft(y))

        # 谐波噪声比代理 (harmonic-to-noise ratio proxy)
        harmonic, percussive = librosa.decompose.hpss(stft)
        hnr_proxy = float(
            np.mean(np.abs(harmonic)) / (np.mean(np.abs(percussive)) + 1e-6)
        )

        # 色谱方差 (音乐性指标: 音乐色谱随时间变化丰富)
        chroma    = librosa.feature.chroma_stft(y=y, sr=sr)
        chroma_var = float(np.mean(np.var(chroma, axis=1)))

        # 节拍强度 (打击乐/节奏感)
        try:
            _, beats  = librosa.beat.beat_track(y=y, sr=sr)
            beat_strength = float(min(1.0, len(beats) / max(len(y) / sr, 1) / 2))
        except Exception:
            beat_strength = 0.0

        desc_lower = description.lower()

        # ── 场景判断树 ───────────────────────────────────────────
        # 1) 噪声: 极低 RMS 或极高 ZCR + 低谐波
        if rms < 0.005 or (zcr > 0.35 and hnr_proxy < 0.5):
            return "noise", 0.88

        # 2) 音乐: 高色谱方差 + 节拍 + 谐波强
        if chroma_var > 0.05 and beat_strength > 0.2 and hnr_proxy > 1.5:
            return "music_original", round(min(0.95, 0.60 + chroma_var * 5), 3)

        # 3) 环境音效: 低 ZCR + 低 chroma_var + 稳定 RMS
        if zcr < 0.05 and chroma_var < 0.03 and rms > 0.01:
            return "ambient_sfx", 0.72

        # 4) 语音类: 中等 ZCR + 谐波比
        if 0.02 <= zcr <= 0.25 and hnr_proxy > 0.8:
            # 细分语音场景 (关键词辅助)
            med_hits = sum(1 for kw in _MEDICAL_AUDIO_KWS if kw in desc_lower)
            leg_hits = sum(1 for kw in _LEGAL_AUDIO_KWS  if kw in desc_lower)
            if med_hits >= 2:
                return "speech_medical", round(min(0.95, 0.65 + med_hits * 0.05), 3)
            if leg_hits >= 2:
                return "speech_legal", round(min(0.92, 0.60 + leg_hits * 0.05), 3)
            return "speech_edu", 0.68

        # 5) 默认: 环境/混合
        return "ambient_sfx", 0.45


# ===================================================================
# AudioAdapter
# ===================================================================

class AudioAdapter(BaseModalityAdapter):
    """
    音频模态适配器 v2

    v1 → v2 升级:
      [核心] Whisper 转录 + 联合文本打分
        - 对语音场景自动调用 Whisper 转录，成功则：
          1. snr += 5.0 × 1.1（清晰度验证）
          2. llm_value += min(15, len(text)/50)（内容价值加成）
          3. _whisper_text 字段透传转录文本（供 oracle debug / 前端展示）
        - 懒加载，失败静默降级，行为与 v1 完全一致。
      [兼容] BaseModalityAdapter 接口不变，oracle_engine 无需修改。

    接受 AssetData:
      asset_category = "audio"
      description    = 音频描述文本 (辅助场景判断)
      audio_data     = base64 编码的音频文件 (WAV/MP3/FLAC/OGG，可选)
    """

    _extractor = _AudioFeatureExtractor()

    def __init__(self, embed_fn, get_corpus_fn):
        """
        Args:
            embed_fn:      ChromaDB SentenceTransformerEmbeddingFunction
                           (仅在 audio_data=None 时用于文本描述 fallback)
            get_corpus_fn: 返回语料库嵌入列表的可调用对象
        """
        self._embed_fn   = embed_fn
        self._get_corpus = get_corpus_fn

    # ----------------------------------------------------------------
    # 感知哈希 (抗重新压制/变速/轻微变调)
    # ----------------------------------------------------------------
    def generate_hash(self, asset_data: str, audio_data: Optional[str] = None) -> str:
        """
        优先使用音频波形的声学指纹 (Acoustic Fingerprint)。
        无波形时降级为描述文本 SHA-256。
        """
        if audio_data:
            y, sr = _decode_audio_b64(audio_data)
            if y is not None and len(y) > 0:
                return _acoustic_hash(y, sr)
        return "0xAUD_DESC_" + hashlib.sha256(asset_data.encode()).hexdigest()[:12].upper()

    # ----------------------------------------------------------------
    # MFCC 384-dim 嵌入 (CLAP 代理)
    # ----------------------------------------------------------------
    def get_embedding(self, asset_data: str, audio_data: Optional[str] = None) -> List[float]:
        """
        真实路径: MFCC 统计特征 → 384-dim (已 L2 归一化)
        Fallback:  SentenceTransformer 描述文本嵌入 (与 TextAdapter 一致)
        """
        if audio_data and HAS_LIBROSA:
            y, sr = _decode_audio_b64(audio_data)
            if y is not None and len(y) > sr // 4:   # 至少 0.25 秒
                return self._extractor.mfcc_embedding(y, sr)

        # Fallback: 文本嵌入
        if self._embed_fn:
            res = self._embed_fn([asset_data])
            return res[0] if res else [0.0] * _EMBED_DIM
        return [0.0] * _EMBED_DIM

    # ----------------------------------------------------------------
    # 6D 特征提取
    # ----------------------------------------------------------------
    def extract_metrics(
        self,
        asset_data: str,
        scene_result,
        vector_distance: float,
        query_embedding: List[float],
        audio_data: Optional[str] = None,
        **_,
    ) -> Dict[str, float]:
        """
        音频 6D 特征提取

        真实路径 (audio_data + librosa):
          entropy   ← 频谱熵 (STFT 能量均匀度)
          snr       ← 感知信噪比 (PESQ 代理)
          structure ← Delta-MFCC 动态范围 + 音节速率
          scarcity  ← 向量空间稀缺度 + 音频场景稀缺加成
          llm_value ← 场景基础分 + 质量修正
          shapley   ← unified_shapley_score (v3 自适应)

        Fallback 路径 (无音频字节):
          以描述文本长度/丰富度做粗估，所有指标乘以 0.55 衰减因子。
        """
        from scoring import knn_shapley_score, unified_shapley_score

        has_wave    = False
        whisper_txt = None   # v2: Whisper 转录结果
        y = sr = None

        if audio_data and HAS_LIBROSA:
            y_decoded, sr_decoded = _decode_audio_b64(audio_data)
            if y_decoded is not None and len(y_decoded) > sr_decoded // 4:
                y, sr     = y_decoded, sr_decoded
                has_wave  = True
                # ── v2 新增：Whisper 转录 ─────────────────────────────
                # 仅对语音场景（非纯音乐/音效）尝试转录，节约计算
                tev_scene_hint = scene_result.scene if scene_result else "general"
                if tev_scene_hint in ("medical_sft", "legal_doc", "chat_qa", "general"):
                    whisper_txt = _whisper_transcribe(y, sr)

        # ── 音频场景 (独立于 oracle 场景分类器) ──────────────────
        # 从 scene_result 取已分类的 tev_scene
        tev_scene   = scene_result.scene if scene_result else "general"
        audio_scene = _reverse_map(tev_scene)   # "medical_sft" → "speech_medical"

        # ── 真实路径 ─────────────────────────────────────────────
        if has_wave:
            entropy   = self._extractor.spectral_entropy(y, sr)
            snr       = self._extractor.perceptual_snr(y, sr)
            structure = self._extractor.temporal_structure(y, sr)

            # 时长因子 (diminishing returns: 0~60s 线性, 60~300s 对数)
            duration_secs = len(y) / sr
            if duration_secs <= 60:
                dur_factor = duration_secs / 60.0
            else:
                dur_factor = 1.0 + math.log(duration_secs / 60.0, 5) * 0.15
            dur_factor = min(1.0, dur_factor)

        else:
            # ── Fallback: 基于描述文本的粗估 ─────────────────────
            desc_len     = len(asset_data)
            desc_words   = len(asset_data.split())
            desc_unique  = len(set(asset_data.lower().split()))
            ttr          = desc_unique / max(desc_words, 1)
            entropy      = min(100.0, ttr * 90 + desc_len / 20) * 0.55
            snr          = 50.0 * 0.55                             # 中等默认值
            structure    = min(100.0, desc_words * 1.5)  * 0.55
            dur_factor   = 0.5                                      # 无时长信息

        # ── 稀缺度 ──────────────────────────────────────────────
        # 基础: 向量空间距离 (与 ImageAdapter 相同逻辑)
        base_scarcity  = min(100.0, max(15.0, vector_distance * 85))
        # 场景加成: 医疗语音极稀缺
        scene_bonus    = _scene_scarcity_bonus(audio_scene)
        scarcity       = min(100.0, base_scarcity + scene_bonus)

        # ── KNN-Shapley (v3 统一调度) ────────────────────────────
        corpus  = self._get_corpus() if self._get_corpus else []
        shapley_score, shapley_conf = unified_shapley_score(
            query_embedding, corpus, scene=tev_scene
        )

        # ── llm_value: 下游 AI 训练收益 ──────────────────────────
        scene_base = _SCENE_LLM_BASE.get(audio_scene, 42.0)
        llm_value  = (
            scene_base * 0.40
            + (snr / 100.0)       * scene_base * 0.25
            + (structure / 100.0) * scene_base * 0.15
            + scarcity            * 0.10
            + shapley_score       * 0.10
        ) * (0.60 + 0.40 * dur_factor)

        # ── v2: Whisper 联合加成 ──────────────────────────────────
        # 如果 Whisper 成功转录，说明语音清晰度高，且提供真实文本内容
        whisper_bonus = 0.0
        if whisper_txt:
            txt_len     = len(whisper_txt)
            # 转录字数奖励：每 50 字加 1 分，上限 15 分
            whisper_bonus = min(15.0, txt_len / 50.0)
            # snr 提升：成功转录意味着 Whisper 能处理，语音质量可用
            snr = min(100.0, snr * 1.1 + 5.0)
            llm_value = min(100.0, llm_value + whisper_bonus)

        return {
            "entropy":   round(min(100.0, max(0.0, entropy)),   1),
            "snr":       round(min(100.0, max(0.0, snr)),       1),
            "structure": round(min(100.0, max(0.0, structure)), 1),
            "scarcity":  round(min(100.0, max(0.0, scarcity)),  1),
            "llm_value": round(min(100.0, max(0.0, llm_value)), 1),
            "shapley":   round(min(100.0, max(0.0, shapley_score)), 1),
            # 内部字段 (oracle_engine 读取用)
            "_audio_scene":       audio_scene,
            "_shapley_conf":      round(shapley_conf, 3),
            "_has_wave":          has_wave,
            # v2 新增私有字段
            "_whisper_text":      whisper_txt[:200] if whisper_txt else None,
            "_whisper_bonus":     round(whisper_bonus, 1),
        }

    # ----------------------------------------------------------------
    # 指标名称 (RadarChart 显示)
    # ----------------------------------------------------------------
    def get_metric_names(self) -> List[str]:
        return [
            "频谱熵 (STFT 信息密度)",
            "感知信噪比 (PESQ 代理)",
            "时频结构复杂度 (Delta-MFCC)",
            "音频场景稀缺度",
            "AI 训练增益 (STT/TTS)",
            "KNN-Shapley 贡献度",
        ]


# ===================================================================
# 内部工具
# ===================================================================

def _reverse_map(tev_scene: str) -> str:
    """TEV 场景 → 音频场景 (最接近的反向映射)"""
    _rev = {v: k for k, v in _AUDIO_SCENE_TO_TEV.items()}
    return _rev.get(tev_scene, "ambient_sfx")


def _scene_scarcity_bonus(audio_scene: str) -> float:
    """不同音频场景的稀缺度加成"""
    bonus_map = {
        "speech_medical": 25.0,   # 医疗转录极稀缺
        "speech_legal":   18.0,
        "speech_edu":      8.0,
        "music_original": 20.0,   # 原创音乐
        "ambient_sfx":     5.0,
        "noise":           0.0,
    }
    return bonus_map.get(audio_scene, 5.0)


# ===================================================================
# 场景分类入口 (供 oracle_engine 直接调用)
# ===================================================================

def classify_audio_scene(
    y: Optional[np.ndarray],
    sr: int,
    description: str,
) -> Tuple[str, float, str]:
    """
    音频场景分类 (oracle_engine 可直接调用)

    Returns:
        (tev_scene: str, confidence: float, audio_scene: str)
        tev_scene 是映射到现有 DOMAIN_DEMAND 的场景键
    """
    if y is not None and HAS_LIBROSA:
        audio_scene, conf = _AudioFeatureExtractor.classify_scene(y, sr, description)
    else:
        # 纯描述文本分类
        desc_lower   = description.lower()
        med_hits     = sum(1 for kw in _MEDICAL_AUDIO_KWS if kw in desc_lower)
        leg_hits     = sum(1 for kw in _LEGAL_AUDIO_KWS  if kw in desc_lower)
        if med_hits >= 2:
            audio_scene, conf = "speech_medical", 0.60
        elif leg_hits >= 2:
            audio_scene, conf = "speech_legal",   0.55
        else:
            audio_scene, conf = "ambient_sfx",    0.40

    tev_scene = _AUDIO_SCENE_TO_TEV.get(audio_scene, "general")
    return tev_scene, conf, audio_scene

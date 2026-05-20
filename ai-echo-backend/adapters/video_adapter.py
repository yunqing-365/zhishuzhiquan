"""
VideoAdapter — 视频模态适配器 v2 (Stage C)
==========================================
v1 (Stage B) → v2 (Stage C) 升级:

  [核心] 双流推理 (Dual-Stream Inference)
    视觉流 (继承 Stage B):
      - OpenCV 均匀采样最多 16 帧
      - CLIP ViT-B/32 per-frame 编码 → 512-dim
      - 时序聚合: mean + std；镜头切换密度；PCA 投影 512→384

    音频流 (Stage C 新增):
      - subprocess ffmpeg 提取视频音轨 → 临时 WAV (16kHz mono)
      - 调用 AudioAdapter 核心方法提取 6D 音频特征
      - 无 ffmpeg / 无音轨时静默降级，视觉流结果不受影响

  [融合策略] StreamFusion
    - 视觉流权重 α = 0.60，音频流权重 β = 0.40
    - 维度级融合: merged[dim] = α × video[dim] + β × audio[dim]
    - entropy:   视觉时域多样性 × α + 音频频谱熵 × β
    - snr:       CLIP 美学分    × α + 音频感知信噪比 × β
    - structure: 镜头切换密度   × α + Delta-MFCC 结构 × β
    - scarcity / shapley:  视觉流主导（音频辅助）
    - llm_value: 融合后加权均值

  [诊断字段] (带 _ 前缀，不计入估值)
    _has_audio_stream: bool   — 音频流是否成功提取
    _audio_scene:      str    — 音频场景分类结果
    _fusion_alpha:     float  — 实际视觉流权重
    _whisper_text:     str    — Whisper 转录片段（如可用）

  [降级] 无 video_data 或 OpenCV 未安装 → Stage A 描述代理
         音频流失败 → 纯视觉流 (α=1.0)，与 Stage B 一致

升级路径:
  Stage A (v0): 描述文字代理
  Stage B (v1): OpenCV 帧采样 + CLIP per-frame
  Stage C (v2): 音轨提取 → AudioAdapter 双流融合 ← 当前版本
  Stage D (v3): VideoMAE / TimeSformer 时序理解
"""

import io
import math
import base64
import hashlib
import tempfile
import os
import subprocess
import shutil
from typing import List, Dict, Optional, Tuple

import numpy as np

from .base_adapter import BaseModalityAdapter

# ── OpenCV 懒加载 ─────────────────────────────────────────────────
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ── ffmpeg 可用性检测（音频流提取）────────────────────────────────
_FFMPEG_BIN: Optional[str] = shutil.which("ffmpeg")
HAS_FFMPEG = _FFMPEG_BIN is not None

try:
    from scoring import knn_shapley_score
except ImportError:
    def knn_shapley_score(emb, corpus):
        return 50.0

try:
    import imagehash
    from PIL import Image as _PIL_Image
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

# ── 配置常量 ──────────────────────────────────────────────────────
_MAX_FRAMES        = 16
_MIN_FRAME_GAP_S   = 1.0
_SCENE_CUT_THRESH  = 0.35
_EMBED_DIM         = 384

# ── 双流融合权重 (Stage C) ──────────────────────────────────────────
# 视觉流主导(α)，音频流辅助(β)；有音频时使用，无音频时 α=1.0
_FUSION_ALPHA_DEFAULT = 0.60   # 视觉流权重
_FUSION_BETA_DEFAULT  = 0.40   # 音频流权重

# 各维度融合系数（视觉流贡献度，音频流 = 1 - 对应值）
# snr: 视觉质量（CLIP 美学）与音频质量各占比
# structure: 镜头切换密度 vs Delta-MFCC 结构
_DIM_ALPHA: Dict[str, float] = {
    "entropy":   0.55,   # 时域多样性 vs 频谱熵
    "snr":       0.55,   # CLIP 美学  vs 感知 SNR
    "structure": 0.50,   # 镜头切换  vs 时频结构（平权）
    "scarcity":  0.70,   # 视觉空间稀缺主导
    "llm_value": 0.60,
    "shapley":   0.65,
}

_HIGH_KWS = frozenset([
    "4k", "8k", "uhd", "高清", "专业", "纪录片", "电影级",
    "professional", "cinematic", "4K", "HDR", "无损",
])
_LOW_KWS = frozenset([
    "模糊", "压缩", "低帧率", "480p", "blur", "compressed",
    "shaky", "竖屏", "手机拍",
])
_SCENE_KWS = frozenset([
    "教学", "演讲", "访谈", "手术", "庭审", "实验室",
    "tutorial", "lecture", "medical", "court", "lab",
])


def _decode_video_b64(b64_str: str) -> Optional[str]:
    if not HAS_CV2:
        return None
    try:
        raw = b64_str.split(",", 1)[-1] if b64_str.startswith("data:") else b64_str
        video_bytes = base64.b64decode(raw)
        suffix = ".mp4"
        if video_bytes[:4] == b"RIFF":
            suffix = ".avi"
        elif video_bytes[:4] == b"\x1a\x45\xdf\xa3":
            suffix = ".webm"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(video_bytes)
        tmp.flush()
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"!! [VideoAdapter v1] 视频解码失败: {e}")
        return None


def _sample_frames(video_path: str) -> Tuple[List[np.ndarray], float, int]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return [], 0.0, 0
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s   = total_frames / fps
    n_samples = min(_MAX_FRAMES, max(1, int(duration_s / _MIN_FRAME_GAP_S)))
    if n_samples <= 1:
        sample_indices = [total_frames // 2]
    else:
        sample_indices = [
            int(i * (total_frames - 1) / (n_samples - 1))
            for i in range(n_samples)
        ]
    frames = []
    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append(cv2.resize(frame, (224, 224)))
    cap.release()
    return frames, fps, total_frames


def _clip_frame_features(frames_bgr: List[np.ndarray]) -> Optional[np.ndarray]:
    """对每帧调用 CLIP ViT-B/32，返回 (N, 512) 特征矩阵"""
    from . import image_adapter as _ia
    _ia._try_load_clip()
    model     = _ia._clip_model
    processor = _ia._clip_processor
    if model is None or processor is None or not frames_bgr:
        return None
    try:
        import torch
        from PIL import Image as PILImage
        all_feats = []
        for bgr in frames_bgr:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(rgb)
            inputs = processor(images=pil_img, return_tensors="pt")
            with torch.no_grad():
                feat = model.get_image_features(**inputs)
            all_feats.append(feat.cpu().numpy()[0])
        return np.array(all_feats, dtype=np.float32)
    except Exception as e:
        print(f"!! [VideoAdapter v1] CLIP 帧编码失败: {e}")
        return None


def _clip_aesthetic_batch(frames_bgr: List[np.ndarray]) -> float:
    from . import image_adapter as _ia
    model     = _ia._clip_model
    processor = _ia._clip_processor
    if model is None or not frames_bgr:
        return -1.0
    try:
        import torch
        from PIL import Image as PILImage
        _POS = "high quality, professional video, cinematic, sharp, detailed"
        _NEG = "low quality, blurry, shaky, compressed, noise, amateur"
        scores = []
        for bgr in frames_bgr:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(rgb)
            inputs = processor(
                text=[_POS, _NEG], images=pil_img,
                return_tensors="pt", padding=True,
            )
            with torch.no_grad():
                out = model(**inputs)
            probs = torch.softmax(out.logits_per_image[0], dim=0).cpu().numpy()
            scores.append(float(probs[0]) * 100.0)
        return float(np.mean(scores)) if scores else -1.0
    except Exception as e:
        print(f"!! [VideoAdapter v1] CLIP aesthetic 批量失败: {e}")
        return -1.0


def _perceptual_hash_video(frames_bgr: List[np.ndarray]) -> str:
    if not frames_bgr or not HAS_IMAGEHASH:
        return ""
    try:
        from PIL import Image as PILImage
        hashes = []
        for bgr in frames_bgr:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            pil = PILImage.fromarray(rgb)
            hashes.append(str(imagehash.phash(pil)))
        combined = "".join(hashes).encode()
        return hashlib.sha256(combined).hexdigest()[:16].upper()
    except Exception:
        return ""


def _scene_cut_density(frame_feats: np.ndarray) -> float:
    if len(frame_feats) < 2:
        return 0.0
    cuts = 0
    for i in range(len(frame_feats) - 1):
        a = frame_feats[i] / (np.linalg.norm(frame_feats[i]) + 1e-10)
        b = frame_feats[i + 1] / (np.linalg.norm(frame_feats[i + 1]) + 1e-10)
        if 1.0 - float(np.dot(a, b)) > _SCENE_CUT_THRESH:
            cuts += 1
    return round(min(100.0, cuts / (len(frame_feats) - 1) * 100), 1)


def _temporal_diversity(frame_feats: np.ndarray) -> float:
    if len(frame_feats) < 2:
        return 20.0
    mean_var = float(np.mean(np.var(frame_feats, axis=0)))
    return round(min(100.0, mean_var / 0.05 * 100), 1)


# ===================================================================
# Stage C: 音频流提取 & 双流融合
# ===================================================================

def _extract_audio_from_video(video_path: str) -> Optional[str]:
    """
    用 ffmpeg 从视频文件提取音轨，输出 16kHz mono WAV。
    返回临时 WAV 文件路径，调用方负责 unlink；失败时返回 None。

    命令: ffmpeg -i <video> -vn -ar 16000 -ac 1 -f wav <out.wav> -y -loglevel error
    """
    if not HAS_FFMPEG:
        return None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        cmd = [
            _FFMPEG_BIN,
            "-i", video_path,
            "-vn",               # 丢弃视频流
            "-ar", "16000",      # 16 kHz（ASR 标准）
            "-ac", "1",          # mono
            "-f", "wav",
            tmp.name,
            "-y",
            "-loglevel", "error",
        ]
        result = subprocess.run(cmd, timeout=60, capture_output=True)
        if result.returncode != 0 or not os.path.exists(tmp.name):
            os.unlink(tmp.name)
            return None
        # 空音轨检测（< 1KB）
        if os.path.getsize(tmp.name) < 1024:
            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception as e:
        print(f"!! [VideoAdapter v2] ffmpeg 音频提取失败: {e}")
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return None


def _wav_path_to_b64(wav_path: str) -> Optional[str]:
    """将 WAV 文件读取为 base64 字符串（供 AudioAdapter 接口使用）"""
    try:
        with open(wav_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None


def _extract_audio_metrics(
    video_path: str,
    description: str,
    scene_result,
    vector_distance: float,
    query_embedding: List[float],
    embed_fn,
    get_corpus_fn,
) -> Optional[Dict]:
    """
    Stage C 音频流: 提取视频音轨 → AudioAdapter.extract_metrics()
    返回 6D 指标 dict，或 None（无音轨 / 依赖未安装）。
    """
    try:
        from .audio_adapter import AudioAdapter, HAS_LIBROSA
        if not HAS_LIBROSA:
            return None

        wav_path = _extract_audio_from_video(video_path)
        if wav_path is None:
            return None

        try:
            audio_b64  = _wav_path_to_b64(wav_path)
            if audio_b64 is None:
                return None

            audio_adapter = AudioAdapter(embed_fn, get_corpus_fn)
            metrics = audio_adapter.extract_metrics(
                asset_data=description,
                scene_result=scene_result,
                vector_distance=vector_distance,
                query_embedding=query_embedding,
                audio_data=audio_b64,
            )
            return metrics
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    except Exception as e:
        print(f"!! [VideoAdapter v2] 音频流推理失败: {e}")
        return None


def _fuse_streams(
    video_metrics: Dict,
    audio_metrics: Optional[Dict],
    alpha: float = _FUSION_ALPHA_DEFAULT,
) -> Dict:
    """
    双流融合: video × α + audio × (1-α)，维度级可配置权重。
    audio_metrics=None 时退化为纯视觉流 (α=1.0)。
    """
    if audio_metrics is None:
        return video_metrics

    fused = {}
    dims = ["entropy", "snr", "structure", "scarcity", "llm_value", "shapley"]
    for dim in dims:
        v_val = float(video_metrics.get(dim, 0.0))
        a_val = float(audio_metrics.get(dim, 0.0))
        w     = _DIM_ALPHA.get(dim, alpha)
        fused[dim] = round(min(100.0, w * v_val + (1.0 - w) * a_val), 1)

    # 保留所有视觉流诊断字段（带 _ 前缀）
    for k, v in video_metrics.items():
        if k.startswith("_"):
            fused[k] = v

    # 注入音频流诊断字段
    fused["_has_audio_stream"]  = True
    fused["_audio_snr"]         = audio_metrics.get("snr")
    fused["_audio_entropy"]     = audio_metrics.get("entropy")
    fused["_audio_scene"]       = audio_metrics.get("_audio_scene")
    fused["_whisper_text"]      = audio_metrics.get("_whisper_text")
    fused["_fusion_alpha"]      = round(alpha, 2)
    fused["_shapley_confidence"] = round(
        max(
            float(video_metrics.get("_shapley_confidence", 0.5)),
            0.85,    # 双流成功 → 置信度提升到 0.85
        ),
        3,
    )
    return fused


class VideoAdapter(BaseModalityAdapter):
    """
    视频模态适配器 v2 — Stage C: 双流推理 (视觉 + 音频)

    IS_STUB = True 仅当 OpenCV 未安装（帧采样不可用）。
    CLIP / ffmpeg / librosa 未安装时各自静默降级，IS_STUB 不变。
    """

    ADAPTER_VERSION = "v2-stage-c"
    IS_STUB = not HAS_CV2

    def __init__(self, embed_fn, get_corpus_fn):
        self._embed_fn   = embed_fn
        self._get_corpus = get_corpus_fn

    def generate_hash(self, asset_data: str, video_data: Optional[str] = None, **_) -> str:
        if video_data and HAS_CV2:
            tmp_path = _decode_video_b64(video_data)
            if tmp_path:
                try:
                    frames, _, _ = _sample_frames(tmp_path)
                    vid_hash = _perceptual_hash_video(frames)
                    if vid_hash:
                        return f"0xVID_{vid_hash}"
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        digest = hashlib.sha256(asset_data.encode("utf-8")).hexdigest()[:16].upper()
        return f"0xVID_desc_{digest}"

    def get_embedding(self, asset_data: str, video_data: Optional[str] = None, **_) -> List[float]:
        if video_data and HAS_CV2:
            tmp_path = _decode_video_b64(video_data)
            if tmp_path:
                try:
                    frames, _, _ = _sample_frames(tmp_path)
                    if frames:
                        frame_feats = _clip_frame_features(frames)
                        if frame_feats is not None and len(frame_feats) > 0:
                            from . import image_adapter as _ia
                            pca_mat   = _ia._clip_pca
                            mean_feat = np.mean(frame_feats, axis=0)
                            if pca_mat is not None:
                                vec384 = (mean_feat @ pca_mat).astype(np.float32)
                            else:
                                vec384 = mean_feat[:384].astype(np.float32)
                            norm = np.linalg.norm(vec384)
                            return (vec384 / norm if norm > 0 else vec384).tolist()
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        if self._embed_fn:
            try:
                res = self._embed_fn([asset_data])
                return res[0] if res else [0.0] * _EMBED_DIM
            except Exception:
                pass
        return [0.0] * _EMBED_DIM

    def extract_metrics(
        self,
        asset_data: str,
        scene_result,
        vector_distance: float,
        query_embedding: List[float],
        video_data: Optional[str] = None,
        **_,
    ) -> Dict:
        has_video   = False
        frame_feats = None
        clip_ok     = False
        aesthetic   = -1.0
        n_frames    = 0
        duration_s  = 0.0
        tmp_path    = None   # Stage C: 复用给音频流

        if video_data and HAS_CV2:
            tmp_path = _decode_video_b64(video_data)
            if tmp_path:
                try:
                    frames, fps, total_frames = _sample_frames(tmp_path)
                    if frames:
                        has_video   = True
                        n_frames    = len(frames)
                        duration_s  = total_frames / max(fps, 1.0)
                        frame_feats = _clip_frame_features(frames)
                        clip_ok     = frame_feats is not None
                        aesthetic   = _clip_aesthetic_batch(frames) if clip_ok else -1.0
                except Exception as e:
                    print(f"!! [VideoAdapter v2] 视觉流提取异常: {e}")

        if has_video and clip_ok and frame_feats is not None:
            entropy   = _temporal_diversity(frame_feats)
            snr       = aesthetic if aesthetic >= 0 else 55.0
            structure = _scene_cut_density(frame_feats)
            if duration_s <= 60:
                dur_factor = duration_s / 60.0
            elif duration_s <= 600:
                dur_factor = 1.0 + math.log(duration_s / 60.0, 10) * 0.2
            else:
                dur_factor = 1.35
            dur_factor = min(1.5, dur_factor)
            shapley_confidence = 0.80
        else:
            # Stage A 降级
            desc_lower   = asset_data.lower()
            words        = asset_data.split()
            n_words      = max(len(words), 1)
            unique_ratio = len(set(words)) / n_words
            entropy      = min(100.0, unique_ratio * 130 + len(asset_data) * 0.04) * 0.55
            high_bonus   = sum(6.0 for kw in _HIGH_KWS if kw in desc_lower)
            low_penalty  = sum(8.0 for kw in _LOW_KWS  if kw in desc_lower)
            snr          = min(100.0, max(20.0, 55.0 + high_bonus - low_penalty)) * 0.55
            scene_bonus  = sum(5.0 for kw in _SCENE_KWS if kw in desc_lower)
            structure    = min(100.0, 45.0 + scene_bonus + unique_ratio * 30) * 0.55
            dur_factor   = 0.5
            shapley_confidence = 0.35

        scarcity = min(100.0, max(15.0, vector_distance * 90))
        corpus   = self._get_corpus() if self._get_corpus else []
        shapley  = knn_shapley_score(query_embedding, corpus)
        llm_value = (
            entropy   * 0.20
            + snr     * 0.20
            + structure * 0.20
            + scarcity  * 0.20
            + shapley   * 0.20
        ) * (0.50 + 0.50 * dur_factor)

        video_metrics = {
            "entropy":   round(min(100.0, entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(min(100.0, scarcity), 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
            "_clip_available":     clip_ok,
            "_clip_aesthetic":     round(aesthetic, 1) if aesthetic >= 0 else None,
            "_has_video":          has_video,
            "_n_frames":           n_frames,
            "_duration_s":         round(duration_s, 1),
            "_shapley_confidence": round(shapley_confidence, 3),
            "_has_audio_stream":   False,   # 默认无音频流，下方可能覆盖
        }

        # ── Stage C: 音频流双流推理 ─────────────────────────────────
        # 仅在视觉流成功（has_video）且 ffmpeg 可用时尝试
        audio_metrics = None
        if has_video and tmp_path and HAS_FFMPEG:
            audio_metrics = _extract_audio_metrics(
                video_path=tmp_path,
                description=asset_data,
                scene_result=scene_result,
                vector_distance=vector_distance,
                query_embedding=query_embedding,
                embed_fn=self._embed_fn,
                get_corpus_fn=self._get_corpus,
            )

        # 清理临时视频文件
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # 融合两路特征（audio_metrics=None 时退化为纯视觉流）
        return _fuse_streams(video_metrics, audio_metrics)

    def get_metric_names(self) -> List[str]:
        if HAS_CV2:
            audio_suffix = " + 音频流" if HAS_FFMPEG else ""
            return [
                f"帧时域多样性 (CLIP temporal diversity{audio_suffix})",
                f"CLIP 帧美学分 / 音频感知 SNR (双流融合)" if HAS_FFMPEG else "CLIP 帧美学分 (cinematic quality)",
                f"镜头切换密度 / 时频结构 (双流融合)" if HAS_FFMPEG else "镜头切换密度 (scene complexity)",
                "视频库稀缺度 (CLIP frame space)",
                f"多帧+音轨训练增益 (VideoLLM × AudioLM)" if HAS_FFMPEG else "多帧训练增益 (VideoLLM alignment)",
                "KNN-Shapley 贡献度",
            ]
        return [
            "帧多样性 (描述代理 · Stage A)",
            "视频码率质量 (描述代理 · Stage A)",
            "镜头结构复杂度 (描述代理 · Stage A)",
            "视频库稀缺度 (向量空间)",
            "视频训练增益 (描述代理 · Stage A)",
            "KNN-Shapley 贡献度",
        ]

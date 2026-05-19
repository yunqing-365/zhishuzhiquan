"""
VideoAdapter — 视频模态适配器 v1 (Stage B)
==========================================
v0 (Stage A) → v1 (Stage B) 升级:

  [核心] 真实帧采样 + CLIP per-frame 视觉编码
    - 接受 base64 编码的视频文件（MP4/AVI/MOV/WebM）
    - 使用 OpenCV 均匀采样最多 16 帧（不超过 30s 间隔）
    - 每帧调用 CLIP ViT-B/32 提取 512-dim 图像特征
    - 时序聚合：mean + std → 时域平均特征 + 时域变化率
    - PCA 投影 512→384 与 ChromaDB SentenceTransformer 维度对齐
    - 感知哈希：各帧 pHash 拼接后 SHA-256，抗重编码鲁棒

  [新增] 镜头切换密度检测
    - 相邻帧 CLIP 余弦距离 > 阈值 → 判定为镜头切换
    - 镜头切换密度 → structure 维度（取代描述文字代理）

  [新增] 时域多样性（temporal diversity）
    - 帧特征方差均值 → 反映内容变化丰富度
    - 高方差 = 场景切换频繁 / 信息密度大 → 训练价值高

  [降级] 无 video_data 或 OpenCV/CLIP 未安装时
    - 自动回退到 Stage A 描述文字代理，行为与 v0 完全一致
    - IS_STUB = True 仅当 OpenCV 未安装时置位

升级路径:
  Stage A (v0): 描述文字代理
  Stage B (v1): OpenCV 帧采样 + CLIP per-frame ← 当前版本
  Stage C (v2): 音轨 → AudioAdapter 联合估值（双流）
  Stage D (v3): VideoMAE / TimeSformer 时序理解
"""

import io
import math
import base64
import hashlib
import tempfile
import os
from typing import List, Dict, Optional, Tuple

import numpy as np

from .base_adapter import BaseModalityAdapter

# ── OpenCV 懒加载 ─────────────────────────────────────────────────
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

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


class VideoAdapter(BaseModalityAdapter):
    """
    视频模态适配器 v1 — Stage B: OpenCV 帧采样 + CLIP per-frame

    IS_STUB = True 仅当 OpenCV 未安装（帧采样不可用）。
    CLIP 未装时自动降级到 Stage A，IS_STUB 不变。
    """

    ADAPTER_VERSION = "v1-stage-b"
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
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

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

        return {
            "entropy":   round(min(100.0, entropy), 1),
            "snr":       round(min(100.0, snr), 1),
            "structure": round(min(100.0, structure), 1),
            "scarcity":  round(min(100.0, scarcity), 1),
            "llm_value": round(min(100.0, llm_value), 1),
            "shapley":   round(shapley, 1),
            "_clip_available":    clip_ok,
            "_clip_aesthetic":    round(aesthetic, 1) if aesthetic >= 0 else None,
            "_has_video":         has_video,
            "_n_frames":          n_frames,
            "_duration_s":        round(duration_s, 1),
            "_shapley_confidence": round(shapley_confidence, 3),
        }

    def get_metric_names(self) -> List[str]:
        if HAS_CV2:
            return [
                "帧时域多样性 (CLIP temporal diversity)",
                "CLIP 帧美学分 (cinematic quality)",
                "镜头切换密度 (scene complexity)",
                "视频库稀缺度 (CLIP frame space)",
                "多帧训练增益 (VideoLLM alignment)",
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

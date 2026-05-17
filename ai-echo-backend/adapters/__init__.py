"""
adapters — 多模态适配器包
每个模态实现 BaseModalityAdapter 接口，统一输出 6D 特征向量。

当前支持:
  TextAdapter   — 文本语料 (6 种场景路径)
  ImageAdapter  — 图像/画作 (5 种场景路径)
  AudioAdapter  — 音频 (语音/音乐，声学指纹 + MFCC 嵌入 + 6D 特征)
  VideoAdapter  — 视频 (Stage A 降级：描述文字代理；Stage B 接帧采样 + CLIP-video)
"""
from .base_adapter import BaseModalityAdapter
from .text_adapter import TextAdapter
from .image_adapter import ImageAdapter
from .audio_adapter import AudioAdapter, classify_audio_scene
from .video_adapter import VideoAdapter

__all__ = [
    "BaseModalityAdapter",
    "TextAdapter",
    "ImageAdapter",
    "AudioAdapter",
    "classify_audio_scene",
    "VideoAdapter",
]

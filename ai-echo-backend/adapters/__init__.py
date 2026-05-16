"""
adapters — 多模态适配器包
每个模态实现 BaseModalityAdapter 接口，统一输出 6D 特征向量。

当前支持:
  TextAdapter  — 文本语料 (6 种场景路径)
  ImageAdapter — 图像/画作 (5 种场景路径)

升级路径:
  AudioAdapter — 音频 (语音/音乐，接入 Whisper + CLAP)
  VideoAdapter — 视频 (接入帧采样 + CLIP-video)
"""
from .base_adapter import BaseModalityAdapter
from .text_adapter import TextAdapter
from .image_adapter import ImageAdapter

__all__ = ["BaseModalityAdapter", "TextAdapter", "ImageAdapter"]

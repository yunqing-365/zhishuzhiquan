"""
adapters/base_adapter.py — 多模态适配器抽象基类 v2
=====================================================

v1 → v2 升级:
  - 方法签名与三个子类（TextAdapter / ImageAdapter / AudioAdapter）真实实现对齐
    v1 的 extract_metrics(asset_data, vector_distance, query_embedding) 签名
    与子类实际参数不符，导致 ABC 约束形同虚设。
  - 新增 get_metric_names() 抽象方法（三个子类均已实现，v1 漏掉了）
  - 新增 modality 类属性，供 oracle_engine 注册表路由使用
  - 保留 **kwargs 作为模态专属参数通道：
      TextAdapter  : 无额外参数
      ImageAdapter : image_data: Optional[str] = None
      AudioAdapter : audio_data: Optional[str] = None
    调用方通过 _modal_extra(asset) 统一组装 kwargs，适配器按需取用。

架构约束（所有子类必须遵守）：
  - generate_hash   : 返回 str，格式由子类定义
  - get_embedding   : 返回 384-dim List[float]，L2 归一化，ChromaDB 对齐
  - extract_metrics : 返回 Dict[str, float]，必须含 6 个标准键 + 可选 _ 前缀私有键
  - get_metric_names: 返回长度为 6 的 List[str]，与 6 个标准键一一对应

6 维标准键（0–100分，所有模态统一）：
  entropy   — 信息熵 / 语义丰富度 / 频谱熵
  snr       — 信噪比 / 水印鲁棒性 / 感知信噪比
  structure — 结构性 / 构图复杂度 / 指令连贯性
  scarcity  — 跨空间稀缺度（向量库 KNN 距离 + 场景加成）
  llm_value — 预期大模型训练增益（下游任务适配性）
  shapley   — KNN-Shapley / Beta-Shapley 边际贡献度
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseModalityAdapter(ABC):
    """
    多模态适配器抽象基类 v2

    所有接入智数知权系统的模态必须继承此类并实现全部抽象方法。
    这是系统的"数据度量衡"——无论内部实现如何，对外输出必须一致。

    子类注册：
        在 adapters/__init__.py 的 ADAPTER_REGISTRY 中以 modality 字符串为键注册实例。
        oracle_engine 通过注册表路由，无需硬编码 if-else。

    模态标识（子类应覆盖此类属性）：
        modality: str = "base"
    """

    modality: str = "base"

    @abstractmethod
    def generate_hash(self, asset_data: str, **kwargs) -> str:
        """
        生成资产的抗破坏底层哈希，用于上链确权和防洗稿检测。

        Args:
            asset_data : 主要内容描述文本（所有模态均有）
            **kwargs   : 模态专属二进制数据
                         image_data (str): base64 图像
                         audio_data (str): base64 音频

        Returns:
            str: 哈希字符串，前缀由子类定义
        """

    @abstractmethod
    def get_embedding(self, asset_data: str, **kwargs) -> List[float]:
        """
        提取 384 维语义特征向量，L2 归一化，与 ChromaDB 对齐。

        Args:
            asset_data : 主要内容描述文本
            **kwargs   : 模态专属数据

        Returns:
            List[float]: 长度为 384 的归一化向量
        """

    @abstractmethod
    def extract_metrics(
        self,
        asset_data: str,
        scene_result,
        vector_distance: float,
        query_embedding: List[float],
        **kwargs,
    ) -> Dict[str, float]:
        """
        核心算子：提取 6D 标准化评价指标（0–100 分）。

        必须返回的 6 个标准键：
            entropy, snr, structure, scarcity, llm_value, shapley

        可选 _ 前缀私有键（oracle_engine 读取，不传前端）：
            _audio_scene, _has_wave, 等
        """

    @abstractmethod
    def get_metric_names(self) -> List[str]:
        """
        返回 6 个标准键的人类可读名称列表（供前端雷达图 subject 字段使用）。
        顺序固定：[entropy名, snr名, structure名, scarcity名, llm_value名, shapley名]
        """

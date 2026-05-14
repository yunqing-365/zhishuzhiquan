# adapters/base_adapter.py
from abc import ABC, abstractmethod
from typing import Dict, List

class BaseModalityAdapter(ABC):
    """
    多模态基类适配器：
    所有接入智数知权系统的模态（文本、图像、音频等）都必须实现这些接口。
    这就是我们平台的“数据度量衡”。
    """

    @abstractmethod
    def generate_hash(self, asset_data) -> str:
        """生成抗破坏的底层哈希（用于上链和防洗稿判断）"""
        pass

    @abstractmethod
    def get_embedding(self, asset_data) -> List[float]:
        """提取高维语义特征向量（用于 KNN-Shapley 定价和相似度检索）"""
        pass

    @abstractmethod
    def extract_metrics(self, asset_data, vector_distance: float, query_embedding: List[float]) -> Dict[str, float]:
        """
        核心算子：无论内部怎么算，必须输出统一的 6 维标准化评价体系 (0-100分)
        必须返回包含以下 key 的字典:
        - entropy (信息熵/语义丰富度)
        - snr (信噪比/水印鲁棒性)
        - structure (结构/构图/指令连贯性)
        - scarcity (跨网拓扑稀缺度)
        - llm_value (预期大模型微调增益)
        - shapley (KNN-Shapley 边际贡献度)
        """
        pass
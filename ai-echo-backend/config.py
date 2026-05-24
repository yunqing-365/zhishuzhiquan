# config.py — AI-Echo Backend 配置中心
"""
统一管理所有可配置项，从环境变量 / .env 文件读取。
其他模块只需：from config import get_settings

环境变量优先级：系统环境变量 > .env 文件 > 此处默认值
"""
from __future__ import annotations

import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
    # 依次尝试加载 .env（项目根目录）和 ai-echo-backend/.env
    _root = os.path.dirname(os.path.dirname(__file__))
    load_dotenv(os.path.join(_root, ".env"), override=False)
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)
except ImportError:
    pass


class Settings:
    # ── LLM（标注引擎）────────────────────────────────────────────────
    openai_api_key:   str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url:  str = os.environ.get(
        "OPENAI_BASE_URL", "https://api.openai.com/v1"
    )
    openai_model:     str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # ── 数据集生产目录 ────────────────────────────────────────────────
    _data_dir = os.path.join(os.path.dirname(__file__), "data")
    dataset_output_dir: str = os.environ.get(
        "DATASET_OUTPUT_DIR", os.path.join(_data_dir, "datasets")
    )
    pipeline_state_dir: str = os.environ.get(
        "PIPELINE_STATE_DIR", os.path.join(_data_dir, "pipeline_state")
    )
    creator_ledger_path: str = os.environ.get(
        "CREATOR_LEDGER_PATH", os.path.join(_data_dir, "creator_ledger.json")
    )

    # ── 业务参数 ──────────────────────────────────────────────────────
    platform_revenue_ratio: float = float(
        os.environ.get("PLATFORM_REVENUE_RATIO", "0.30")   # 平台留成 30%
    )
    creator_revenue_ratio: float = float(
        os.environ.get("CREATOR_REVENUE_RATIO", "0.70")    # 创作者池 70%
    )

    # ── 后端网络 ──────────────────────────────────────────────────────
    backend_host: str = os.environ.get("BACKEND_HOST", "0.0.0.0")
    backend_port: int = int(os.environ.get("BACKEND_PORT", "8000"))

    def __init__(self):
        # 确保目录存在
        os.makedirs(self.dataset_output_dir, exist_ok=True)
        os.makedirs(self.pipeline_state_dir, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

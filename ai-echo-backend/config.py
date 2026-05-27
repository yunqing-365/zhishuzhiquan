# config.py — AI-Echo Backend 配置中心
"""
统一管理所有可配置项，从环境变量 / .env 文件读取。
其他模块只需：from config import get_settings

环境变量优先级：系统环境变量 > .env 文件 > 此处默认值
"""
from __future__ import annotations

import os
import warnings
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
    # 多模型投票副模型（留空则与主模型相同，走不同 temperature）
    openai_model_b:    str = os.environ.get("OPENAI_MODEL_B", "")
    # 副模型的 API Base URL（副模型来自不同厂商时设置，如 DeepSeek / Moonshot）
    # 留空则复用 openai_base_url（主模型地址）
    openai_base_url_b: str = os.environ.get("OPENAI_BASE_URL_B", "")

    # ── JWT 认证 ──────────────────────────────────────────────────────
    jwt_secret_key:  str = os.environ.get(
        "JWT_SECRET_KEY",
        "zhishuzhiquan-dev-secret-please-change-in-production-2024"
    )
    jwt_algorithm:   str = os.environ.get("JWT_ALGORITHM", "HS256")
    jwt_expire_days: int = int(os.environ.get("JWT_EXPIRE_DAYS", "30"))

    # ── 数据存储路径 ──────────────────────────────────────────────────
    _data_dir = os.path.join(os.path.dirname(__file__), "data")

    # 主 SQLite 数据库（样本、包元数据、版本快照）
    sqlite_db_path: str = os.environ.get(
        "SQLITE_DB_PATH", os.path.join(_data_dir, "zszq.db")
    )
    # 账本 SQLite（CreatorLedger，独立文件避免事务争用）
    creator_ledger_db_path: str = os.environ.get(
        "CREATOR_LEDGER_DB_PATH", os.path.join(_data_dir, "creator_ledger.db")
    )
    # 旧版 JSON 账本路径（仅供首次启动迁移，迁移完成后可忽略）
    creator_ledger_json_path: str = os.environ.get(
        "CREATOR_LEDGER_JSON_PATH", os.path.join(_data_dir, "creator_ledger.json")
    )
    # 数据集生产输出目录
    dataset_output_dir: str = os.environ.get(
        "DATASET_OUTPUT_DIR", os.path.join(_data_dir, "datasets")
    )
    # 流水线断点状态目录
    pipeline_state_dir: str = os.environ.get(
        "PIPELINE_STATE_DIR", os.path.join(_data_dir, "pipeline_state")
    )
    # ChromaDB 向量库目录
    chroma_db_dir: str = os.environ.get(
        "CHROMA_DB_DIR", os.path.join(_data_dir, "chroma_db")
    )

    # ── 业务参数 ──────────────────────────────────────────────────────
    platform_revenue_ratio: float = float(
        os.environ.get("PLATFORM_REVENUE_RATIO", "0.30")   # 平台留成 30%
    )
    creator_revenue_ratio: float = float(
        os.environ.get("CREATOR_REVENUE_RATIO", "0.70")    # 创作者池 70%
    )
    # 内容安全：是否启用 LLM 三层审核
    enable_llm_safety: bool = os.environ.get(
        "ENABLE_LLM_SAFETY", "false"
    ).lower() == "true"

    # ── 后端网络 ──────────────────────────────────────────────────────
    backend_host: str = os.environ.get("BACKEND_HOST", "0.0.0.0")
    backend_port: int = int(os.environ.get("BACKEND_PORT", "8000"))
    # 跨域白名单（逗号分隔）
    allowed_origins: str = os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://localhost"
    )

    def __init__(self):
        # 确保必要目录存在
        os.makedirs(self.dataset_output_dir, exist_ok=True)
        os.makedirs(self.pipeline_state_dir, exist_ok=True)
        os.makedirs(self.chroma_db_dir, exist_ok=True)

        # 生产环境密钥检查
        if self.jwt_secret_key.startswith("zhishuzhiquan-dev-secret"):
            warnings.warn(
                "[config] 使用默认 JWT_SECRET_KEY！生产部署前请在 .env 中设置强随机密钥。",
                UserWarning,
                stacklevel=2,
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

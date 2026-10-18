# backend/config.py
# pydantic-settings：从 .env.local 读取配置

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/config.py → 上两级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_BGE_M3 = str(PROJECT_ROOT / "models" / "embedding" / "bge-m3")
_DEFAULT_RERANKER = str(PROJECT_ROOT / "models" / "reranker" / "bge-reranker-large")
_DEFAULT_MINILM = str(PROJECT_ROOT / "models" / "classifier" / "all-MiniLM-L6-v2")


class Settings(BaseSettings):
    """应用配置（环境变量 / .env.local）。"""

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──
    app_name: str = "规划助手"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    default_tenant_id: str = "tenant_default"

    # ── DeepSeek ──
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"  # DeepSeek-V3 via OpenAI-compatible API

    # ── PostgreSQL（默认对齐现网 edu 栈账号；可被 .env.local 覆盖）──
    db_host: str = "127.0.0.1"
    db_port: int = 5433
    db_user: str = "eduagent_user"
    db_password: str = "123456"
    db_name: str = "eduagent"

    # ── Milvus ──
    milvus_host: str = "127.0.0.1"
    milvus_port: int = 19531
    milvus_collection: str = "landscape_kb"
    milvus_dense_dim: int = 1024  # BGE-M3 dense

    # ── Local models (process-local, under models/) ──
    bge_m3_model: str = _DEFAULT_BGE_M3
    bge_reranker_model: str = _DEFAULT_RERANKER
    minilm_model: str = _DEFAULT_MINILM
    model_device: str = "cpu"

    # ── RAG params ──
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 80
    rag_top_k: int = 8
    rag_rerank_top_n: int = 5
    # CPU 上 BGE-reranker-large 单次约数秒，默认关闭以保首 token P95；需要时可 .env 打开
    rag_enable_rerank: bool = False
    intent_retrieve_threshold: float = 0.45

    # ── Eval dimension weights (sum ≈ 1.0) ──
    eval_weight_concept: float = 0.2
    eval_weight_compliance: float = 0.2
    eval_weight_ecology: float = 0.2
    eval_weight_layout: float = 0.2
    eval_weight_economy: float = 0.2

    # ── Uploads ──
    upload_dir: str = "uploads"

    # ── Auth（本地最小 JWT；生产必须更换密钥）──
    jwt_secret: str = "landscape-dev-change-me"
    jwt_expire_minutes: int = 720

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

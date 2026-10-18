# backend/rag/model_paths.py
# 本地模型路径校验：存在才加载，避免静默回落 HuggingFace Hub

from __future__ import annotations

from pathlib import Path

from backend.config import PROJECT_ROOT


def resolve_model_path(model_ref: str) -> Path:
    """将配置中的模型路径解析为绝对 Path。

    - 绝对路径：原样使用
    - 相对路径：相对项目根目录
    """
    p = Path(model_ref)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def require_local_model(model_ref: str, label: str) -> str:
    """校验本地模型目录存在，返回可供 FlagEmbedding / SentenceTransformer 加载的路径字符串。"""
    path = resolve_model_path(model_ref)
    path_str = str(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{label} 本地模型不存在: {path}\n"
            f"请将权重放到 models/ 对应目录，或检查 .env.local 中的路径配置。"
        )
    if not path.is_dir():
        raise FileNotFoundError(
            f"{label} 路径不是目录: {path}\n"
            f"期望为包含 config.json / 权重文件的模型目录。"
        )
    return path_str

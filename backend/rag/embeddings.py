# backend/rag/embeddings.py
# BGE-M3 进程内单例：dense + sparse 双输出

from __future__ import annotations

import asyncio
from typing import Any

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _patch_transformers_dtype_kwarg() -> None:
    """兼容 transformers>=4.50 向旧版 *Model.__init__ 传入 dtype 导致的 TypeError。"""
    try:
        from transformers.models.xlm_roberta.modeling_xlm_roberta import XLMRobertaModel
    except Exception:
        return
    if getattr(XLMRobertaModel, "_landscape_dtype_patched", False):
        return
    _orig = XLMRobertaModel.__init__

    def _init(self, config, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.pop("dtype", None)
        return _orig(self, config, *args, **kwargs)

    XLMRobertaModel.__init__ = _init  # type: ignore[method-assign]
    XLMRobertaModel._landscape_dtype_patched = True  # type: ignore[attr-defined]

    # 部分版本在 from_pretrained → cls(config, **model_kwargs) 路径传入 dtype
    try:
        from transformers import modeling_utils as _mu

        if not getattr(_mu, "_landscape_dtype_patched", False):
            _orig_load = getattr(_mu.PreTrainedModel, "_load_pretrained_model", None)
            _ = _orig_load  # keep attr referenced for future hooks
            # Also wrap AutoModel.from_pretrained call site via XLMRoberta only — sufficient for BGE-M3
            _mu._landscape_dtype_patched = True
    except Exception:
        pass


class BGEM3Embedder:
    """BGE-M3 embedding（进程内单例）。"""

    _instance: BGEM3Embedder | None = None
    _model: Any = None

    def __init__(self) -> None:
        self._model = None
        self._loaded = False

    @classmethod
    def get_instance(cls) -> BGEM3Embedder:
        if cls._instance is None:
            cls._instance = BGEM3Embedder()
        return cls._instance

    def load(self) -> None:
        if self._loaded:
            return
        settings = get_settings()
        _patch_transformers_dtype_kwarg()
        from FlagEmbedding import BGEM3FlagModel
        from backend.rag.model_paths import require_local_model

        model_path = require_local_model(settings.bge_m3_model, "BGE-M3")
        logger.info("embeddings.bge_m3_loading", model=model_path)
        self._model = BGEM3FlagModel(
            model_path,
            use_fp16=False,
            device=settings.model_device,
        )
        self._loaded = True
        logger.info("embeddings.bge_m3_loaded", model=model_path)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def encode_sync(self, texts: list[str]) -> dict[str, Any]:
        """同步编码，返回 dense (list[list[float]]) 与 sparse (list[dict])."""
        if not self._loaded:
            self.load()
        assert self._model is not None
        out = self._model.encode(
            texts,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = out["dense_vecs"]
        # FlagEmbedding sparse: list of dict {token_id: weight}
        sparse_list = out["lexical_weights"]
        dense_list = [d.tolist() if hasattr(d, "tolist") else list(d) for d in dense]
        sparse_dicts: list[dict[int, float]] = []
        for sw in sparse_list:
            if isinstance(sw, dict):
                sparse_dicts.append({int(k): float(v) for k, v in sw.items()})
            else:
                # fallback: convert mapping-like
                sparse_dicts.append({int(k): float(v) for k, v in dict(sw).items()})
        return {"dense": dense_list, "sparse": sparse_dicts}

    async def encode(self, texts: list[str]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.encode_sync, texts)

    def encode_query_sync(self, query: str) -> dict[str, Any]:
        result = self.encode_sync([query])
        return {"dense": result["dense"][0], "sparse": result["sparse"][0]}

    async def encode_query(self, query: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.encode_query_sync, query)


def get_embedder() -> BGEM3Embedder:
    return BGEM3Embedder.get_instance()

# backend/rag/reranker.py
# BGE-Reranker 进程内单例

from __future__ import annotations

import asyncio
from typing import Any

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)


class BGEReranker:
    """BGE Reranker（进程内单例）。"""

    _instance: BGEReranker | None = None

    def __init__(self) -> None:
        self._model: Any = None
        self._loaded = False

    @classmethod
    def get_instance(cls) -> BGEReranker:
        if cls._instance is None:
            cls._instance = BGEReranker()
        return cls._instance

    def load(self) -> None:
        if self._loaded:
            return
        settings = get_settings()
        from FlagEmbedding import FlagReranker
        from backend.rag.model_paths import require_local_model

        model_path = require_local_model(settings.bge_reranker_model, "BGE-Reranker")
        logger.info("reranker.loading", model=model_path)
        self._model = FlagReranker(
            model_path,
            use_fp16=False,
            device=settings.model_device,
        )
        self._loaded = True
        logger.info("reranker.loaded", model=model_path)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def rerank_sync(
        self,
        query: str,
        passages: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        """返回 [(index, score), ...] 按分数降序，截断 top_n。"""
        if not passages:
            return []
        if not self._loaded:
            self.load()
        assert self._model is not None
        settings = get_settings()
        n = top_n if top_n is not None else settings.rag_rerank_top_n
        pairs = [[query, p] for p in passages]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float):
            scores = [scores]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return ranked[:n]

    async def rerank(
        self,
        query: str,
        passages: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.rerank_sync, query, passages, top_n)


def get_reranker() -> BGEReranker:
    return BGEReranker.get_instance()

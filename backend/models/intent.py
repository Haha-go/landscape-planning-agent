# backend/models/intent.py
# MiniLM-L6-v2 零样本意图：direct vs retrieve

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)

IntentLabel = Literal["direct", "retrieve"]

# 模板句：与用户问题做余弦相似度，判 direct / retrieve
_RETRIEVE_TEMPLATES = [
    "规范标准国标条例条文要求绿化率绿地率指标是多少",
    "城市规划设计规范中关于公园绿地的规定",
    "景观规划相关国家标准如何执行",
    "根据条例计算容积率绿地率退线",
    "设计规范中的强制性条文是什么",
]
_DIRECT_TEMPLATES = [
    "你好请问你是谁",
    "简单解释一下什么是景观规划",
    "帮我聊聊今天的天气或者闲聊",
    "请用通俗语言介绍园林设计概念",
    "谢谢你帮我总结一下",
]


class IntentClassifier:
    """MiniLM 零样本意图分类（进程内单例）。"""

    _instance: IntentClassifier | None = None

    def __init__(self) -> None:
        self._model: Any = None
        self._loaded = False
        self._retrieve_emb = None
        self._direct_emb = None

    @classmethod
    def get_instance(cls) -> IntentClassifier:
        if cls._instance is None:
            cls._instance = IntentClassifier()
        return cls._instance

    def load(self) -> None:
        if self._loaded:
            return
        settings = get_settings()
        from sentence_transformers import SentenceTransformer
        import numpy as np
        from backend.rag.model_paths import require_local_model

        model_path = require_local_model(settings.minilm_model, "MiniLM")
        logger.info("intent.minilm_loading", model=model_path)
        # Incomplete Hub exports omit 1_Pooling/; ST then treats the Windows path as a
        # Hub repo_id and raises HFValidationError. Materialize the standard config.
        pooling_dir = os.path.join(model_path, "1_Pooling")
        pooling_cfg = os.path.join(pooling_dir, "config.json")
        if not os.path.isfile(pooling_cfg):
            os.makedirs(pooling_dir, exist_ok=True)
            with open(pooling_cfg, "w", encoding="utf-8") as _pf:
                json.dump(
                    {
                        "word_embedding_dimension": 384,
                        "pooling_mode_cls_token": False,
                        "pooling_mode_mean_tokens": True,
                        "pooling_mode_max_tokens": False,
                        "pooling_mode_mean_sqrt_len_tokens": False,
                        "pooling_mode_weightedmean_tokens": False,
                        "pooling_mode_lasttoken": False,
                    },
                    _pf,
                    indent=2,
                )
            logger.info("intent.minilm_pooling_config_written", path=pooling_cfg)
        self._model = SentenceTransformer(
            model_path,
            device=settings.model_device,
        )
        self._retrieve_emb = self._model.encode(_RETRIEVE_TEMPLATES, normalize_embeddings=True)
        self._direct_emb = self._model.encode(_DIRECT_TEMPLATES, normalize_embeddings=True)
        self._np = np
        self._loaded = True
        logger.info("intent.minilm_loaded", model=model_path)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def classify_sync(self, text: str) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        assert self._model is not None
        settings = get_settings()
        np = self._np
        q = self._model.encode([text], normalize_embeddings=True)[0]
        r_scores = (self._retrieve_emb @ q).tolist()
        d_scores = (self._direct_emb @ q).tolist()
        r_max = float(max(r_scores))
        d_max = float(max(d_scores))

        # 库外虚构 / 闲聊优先 direct，避免被「国标/CJJ」诱饵词强行 retrieve
        ood_cues = (
            "火星", "月球", "虚构", "航空母舰", "中世纪", "城堡", "海底隧道",
            "简单话", "通俗", "你是谁", "谢谢", "地球", "天气", "闲聊",
            "早上好", "在吗", "你好", "您好",
        )
        if any(c in text for c in ood_cues):
            return {"label": "direct", "confidence": round(float(max(d_max, 0.5)), 4)}

        # 领域词强制走 retrieve（阈值可能被闲聊模板压过）
        keywords = (
            "规范", "国标", "条例", "绿化率", "绿地率", "条文", "标准",
            "退线", "容积率", "强制", "GB ", "CJJ",
            "服务半径", "无障碍", "坡道", "雨水花园", "植草沟", "园路",
            "纵坡", "海绵", "透水", "下凹", "日照", "铺装", "游园",
            "主园路", "次园路", "溢流", "截污", "乡土", "初期雨水",
            "附属绿地", "集中绿地", "弹性",
        )
        if any(k in text for k in keywords):
            r_max = max(r_max, d_max + 0.05, settings.intent_retrieve_threshold)

        if r_max >= settings.intent_retrieve_threshold and r_max >= d_max:
            label: IntentLabel = "retrieve"
            confidence = r_max
        else:
            label = "direct"
            confidence = d_max if d_max > 0 else 0.5

        return {"label": label, "confidence": round(float(confidence), 4)}

    async def classify(self, text: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.classify_sync, text)


def get_intent_classifier() -> IntentClassifier:
    return IntentClassifier.get_instance()

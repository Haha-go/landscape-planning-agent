# backend/rag/milvus_client.py
# Milvus 连接与 hybrid 检索封装

from __future__ import annotations

from typing import Any

from pymilvus import (
    connections,
    Collection,
    utility,
    AnnSearchRequest,
    RRFRanker,
)

from backend.config import get_settings
from backend.core.exceptions import MilvusConnectionError
from backend.core.logger import get_logger

logger = get_logger(__name__)

_CONNECTED = False


def connect_milvus() -> None:
    global _CONNECTED
    settings = get_settings()
    try:
        if not connections.has_connection("default"):
            connections.connect(
                alias="default",
                host=settings.milvus_host,
                port=str(settings.milvus_port),
            )
        _CONNECTED = True
        logger.info(
            "milvus.connected",
            host=settings.milvus_host,
            port=settings.milvus_port,
        )
    except Exception as e:
        _CONNECTED = False
        raise MilvusConnectionError(str(e), agent_type="qa") from e


def milvus_healthy() -> bool:
    try:
        connect_milvus()
        settings = get_settings()
        return utility.has_collection(settings.milvus_collection)
    except Exception:
        return False


def get_collection() -> Collection:
    settings = get_settings()
    connect_milvus()
    if not utility.has_collection(settings.milvus_collection):
        raise MilvusConnectionError(
            f"Collection '{settings.milvus_collection}' 不存在，请先运行 scripts/init_milvus.py",
            agent_type="qa",
        )
    col = Collection(settings.milvus_collection)
    col.load()
    return col


def sparse_dict_to_milvus(sparse: dict[int, float]) -> dict[int, float]:
    """Ensure keys are int for Milvus sparse vector."""
    return {int(k): float(v) for k, v in sparse.items()}


def hybrid_search(
    dense_vec: list[float],
    sparse_vec: dict[int, float],
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Dense + sparse hybrid search with RRF fusion."""
    settings = get_settings()
    k = top_k or settings.rag_top_k
    col = get_collection()

    dense_req = AnnSearchRequest(
        data=[dense_vec],
        anns_field="dense",
        param={"metric_type": "IP", "params": {"ef": 64}},
        limit=k,
    )
    sparse_req = AnnSearchRequest(
        data=[sparse_dict_to_milvus(sparse_vec)],
        anns_field="sparse",
        param={"metric_type": "IP", "params": {}},
        limit=k,
    )
    results = col.hybrid_search(
        reqs=[dense_req, sparse_req],
        rerank=RRFRanker(),
        limit=k,
        output_fields=["text", "doc_id", "title", "section", "page", "standard_code"],
    )
    hits: list[dict[str, Any]] = []
    if not results:
        return hits
    for hit in results[0]:
        entity = hit.entity
        hits.append(
            {
                "id": hit.id,
                "score": float(hit.score),
                "text": entity.get("text"),
                "doc_id": entity.get("doc_id"),
                "title": entity.get("title"),
                "section": entity.get("section"),
                "page": entity.get("page"),
                "standard_code": entity.get("standard_code"),
            }
        )
    return hits


def delete_chunks_by_doc_id(doc_id: str) -> int:
    """Delete all chunks for a document. Returns approximate deleted count (expr match)."""
    if not doc_id:
        return 0
    col = get_collection()
    # Escape quotes in doc_id for Milvus boolean expr
    safe = doc_id.replace("\\", "\\\\").replace('"', '\\"')
    expr = f'doc_id == "{safe}"'
    try:
        # Count before delete for logging (optional; may be expensive on huge collections)
        res = col.query(expr=expr, output_fields=["id"], limit=16384)
        n = len(res) if res else 0
        col.delete(expr)
        col.flush()
        logger.info("milvus.deleted_by_doc_id", doc_id=doc_id, count=n)
        return n
    except Exception as e:
        logger.warning("milvus.delete_by_doc_id_failed", doc_id=doc_id, error=str(e))
        raise


def upsert_chunks(rows: list[dict[str, Any]], *, replace_doc: bool = True) -> int:
    """Insert chunk rows into Milvus. Each row needs id/text/dense/sparse/...

    When replace_doc is True (default), deletes existing vectors for the same
    doc_id first so re-ingest does not duplicate chunks (insert is not a true upsert).
    """
    if not rows:
        return 0
    col = get_collection()
    if replace_doc:
        doc_ids = {r.get("doc_id") for r in rows if r.get("doc_id")}
        for did in doc_ids:
            delete_chunks_by_doc_id(str(did))
    data = [
        [r["id"] for r in rows],
        [r["text"] for r in rows],
        [r["dense"] for r in rows],
        [sparse_dict_to_milvus(r["sparse"]) for r in rows],
        [r["doc_id"] for r in rows],
        [r["title"] for r in rows],
        [r.get("section") or "" for r in rows],
        [int(r.get("page") or 0) for r in rows],
        [r.get("standard_code") or "" for r in rows],
    ]
    col.insert(data)
    col.flush()
    return len(rows)

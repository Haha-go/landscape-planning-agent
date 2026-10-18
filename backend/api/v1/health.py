# backend/api/v1/health.py

from fastapi import APIRouter
from sqlalchemy import text

from backend.dependencies import AsyncSessionLocal
from backend.rag.embeddings import get_embedder
from backend.rag.reranker import get_reranker
from backend.models.intent import get_intent_classifier
from backend.rag.milvus_client import milvus_healthy

router = APIRouter()


@router.get("/health")
async def health():
    db_ok = False
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        db_err = str(e)
    else:
        db_err = None

    milvus_ok = milvus_healthy()
    models = {
        "bge_m3": get_embedder().is_loaded,
        "reranker": get_reranker().is_loaded,
        "minilm": get_intent_classifier().is_loaded,
    }
    status = "ok" if db_ok and milvus_ok else "degraded"
    return {
        "status": status,
        "postgres": {"ok": db_ok, "error": db_err},
        "milvus": {"ok": milvus_ok},
        "models": models,
    }

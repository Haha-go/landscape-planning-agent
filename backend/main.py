# backend/main.py
# Landscape Planning Agent 入口

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.core.exceptions import LandscapeAgentError
from backend.core.logger import configure_logging, get_logger
from backend.db.migrations import run_migrations
from backend.api.v1 import api_router

logger = get_logger(__name__)


def _load_models_sync() -> None:
    """预加载 MiniLM；BGE-M3/Reranker 懒加载（首次检索时）。"""
    settings = get_settings()
    try:
        from backend.models.intent import get_intent_classifier
        from backend.rag.model_paths import resolve_model_path

        logger.info(
            "startup.model_paths",
            bge_m3=str(resolve_model_path(settings.bge_m3_model)),
            reranker=str(resolve_model_path(settings.bge_reranker_model)),
            minilm=str(resolve_model_path(settings.minilm_model)),
        )
        get_intent_classifier().load()
        logger.info("startup.minilm_ready")
        logger.info("startup.bge_lazy", note="BGE-M3/Reranker 将在首次检索时从本地 models/ 加载")
    except Exception as e:
        logger.warning("startup.model_preload_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

    try:
        await run_migrations()
    except Exception as e:
        logger.warning("startup.migrations_failed", error=str(e))

    try:
        from backend.rag.milvus_client import connect_milvus
        connect_milvus()
    except Exception as e:
        logger.warning("startup.milvus_connect_failed", error=str(e))

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _load_models_sync)

    logger.info("app.started", name=settings.app_name)
    yield
    logger.info("app.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.exception_handler(LandscapeAgentError)
    async def landscape_error_handler(request: Request, exc: LandscapeAgentError):
        return JSONResponse(
            status_code=400,
            content={
                "error": type(exc).__name__,
                "message": str(exc),
                "agent_type": exc.agent_type,
                "details": exc.details,
            },
        )

    # 前端静态页（挂在 API 之后，避免吞掉 /api）
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        logger.info("app.frontend_mounted", path=str(frontend_dir))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

# backend/api/v1/__init__.py
from fastapi import APIRouter

from backend.api.v1 import health, chat, eval as eval_api, assistant, auth, reviews, metrics

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(eval_api.router, prefix="/plans", tags=["plans"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(assistant.router, prefix="/assistant", tags=["assistant"])

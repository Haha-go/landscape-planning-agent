# backend/core/retry.py
# 三层兜底：自动重试 → Agent 级降级 → 系统级兜底

import asyncio
from functools import wraps
from typing import Callable, Any, Optional

from backend.core.exceptions import (
    LLMAPIError,
    MilvusConnectionError,
    InvalidInputError,
    AuthenticationError,
    FileParseError,
)
from backend.core.logger import get_logger
from backend.core.metrics import record_metric

logger = get_logger(__name__)

RETRYABLE_ERRORS = (
    LLMAPIError,
    MilvusConnectionError,
    TimeoutError,
    ConnectionError,
)
NON_RETRYABLE_ERRORS = (
    InvalidInputError,
    AuthenticationError,
    FileParseError,
)


def is_non_retryable(exc: BaseException) -> bool:
    """扫描件、非法输入、认证失败不得重试。"""
    return isinstance(exc, NON_RETRYABLE_ERRORS)

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]
TIMEOUT_PER_ATTEMPT = 120.0


async def retry_call(
    factory: Callable[[], Any],
    *,
    agent_type: str = "",
    tool: str = "llm",
    step: str | None = None,
    timeout: float | None = None,
) -> Any:
    """重试单次调用。不可重试错误立即抛出；耗尽后抛出最后一次异常，不整段降级。"""
    import time

    last_error: Optional[Exception] = None
    started = time.perf_counter()
    limit = TIMEOUT_PER_ATTEMPT if timeout is None else timeout
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(factory(), timeout=limit)
            if attempt > 0:
                logger.info("retry.succeeded", agent_type=agent_type, attempt=attempt + 1, step=step)
            record_metric(
                tool=tool,
                step=step,
                success=True,
                latency_ms=(time.perf_counter() - started) * 1000,
                retried=attempt > 0,
                recovered=attempt > 0,
                detail={"agent_type": agent_type, "attempts": attempt + 1},
            )
            return result
        except NON_RETRYABLE_ERRORS as e:
            logger.warning("retry.non_retryable_error", agent_type=agent_type, error=str(e), step=step)
            record_metric(
                tool=tool,
                step=step,
                success=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                retried=False,
                recovered=False,
                detail={"agent_type": agent_type, "error": type(e).__name__},
            )
            raise
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(
                    "retry.attempt_failed",
                    agent_type=agent_type,
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay=delay,
                    error=str(e),
                    step=step,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("retry.all_attempts_failed", agent_type=agent_type, error=str(e), step=step)
                record_metric(
                    tool=tool,
                    step=step,
                    success=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    retried=True,
                    recovered=False,
                    detail={"agent_type": agent_type, "error": type(e).__name__, "attempts": attempt + 1},
                )
    assert last_error is not None
    raise last_error


def with_retry(agent_type: str = ""):
    """三层兜底装饰器工厂。不可重试错误直接抛出。"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_error: Optional[Exception] = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=TIMEOUT_PER_ATTEMPT,
                    )
                    if attempt > 0:
                        logger.info("retry.succeeded", agent_type=agent_type, attempt=attempt + 1)
                    return result
                except NON_RETRYABLE_ERRORS as e:
                    logger.warning("retry.non_retryable_error", agent_type=agent_type, error=str(e))
                    raise
                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning(
                            "retry.attempt_failed",
                            agent_type=agent_type,
                            attempt=attempt + 1,
                            max_retries=MAX_RETRIES,
                            delay=delay,
                            error=str(e),
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error("retry.all_attempts_failed", agent_type=agent_type, error=str(e))

            try:
                fallback_result = await AgentFallbackHandler.handle(
                    agent_type=agent_type, original_error=last_error,
                )
                logger.info("retry.fallback_succeeded", agent_type=agent_type)
                return fallback_result
            except Exception as fallback_error:
                logger.error("retry.fallback_failed", agent_type=agent_type, error=str(fallback_error))

            logger.error("retry.system_fallback", agent_type=agent_type, original_error=str(last_error))
            return _system_fallback_response(agent_type)

        return wrapper

    return decorator


class AgentFallbackHandler:
    """第二层降级：各 Agent 专项策略。"""

    @classmethod
    async def handle(cls, agent_type: str, original_error: Exception) -> Any:
        fallback_map = {
            "qa": cls._qa_fallback,
            "eval": cls._eval_fallback,
            "eval_extract": cls._eval_fallback,
        }
        handler = fallback_map.get(agent_type)
        if handler:
            return await handler()
        raise original_error

    @classmethod
    async def _qa_fallback(cls) -> dict:
        logger.info("fallback.qa_service_unavailable")
        return {
            "fallback_used": True,
            "content": "⚠️ 知识库检索暂时不可用，请稍后重试。",
            "structured_output": None,
        }

    @classmethod
    async def _eval_fallback(cls) -> dict:
        logger.info("fallback.eval_service_unavailable")
        return {
            "fallback_used": True,
            "content": "方案评估服务暂时不可用，请稍后重试。如持续失败，请检查上传的 PDF 是否完整可读。",
            "structured_output": None,
            "status": "failed",
        }


def _system_fallback_response(agent_type: str) -> dict:
    messages = {
        "qa": "非常抱歉，智能问答服务暂时不可用，请稍后再试。",
        "eval": "非常抱歉，方案评估服务暂时不可用，请稍后重新上传。",
        "eval_extract": "非常抱歉，方案评估服务暂时不可用，请稍后重新上传。",
    }
    content = messages.get(agent_type, "服务暂时不可用，请稍后再试。")
    return {
        "messages": [],
        "content": content,
        "fallback_used": True,
        "system_fallback": True,
        "structured_output": None,
    }

# backend/core/metrics.py
# 路由、工具、评估步骤与重试的成功/耗时记录（日志 + 内存环）

from __future__ import annotations

import math
import threading
from contextvars import ContextVar
from typing import Any

from backend.core.logger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_BUFFER: list[dict[str, Any]] = []
_RUNS: dict[str, list[dict[str, Any]]] = {}
_MAX = 5000
_run_id: ContextVar[str | None] = ContextVar("metric_run_id", default=None)


def begin_run(run_id: str) -> None:
    _run_id.set(run_id)
    with _LOCK:
        _RUNS[run_id] = []


def end_run(run_id: str) -> list[dict[str, Any]]:
    _run_id.set(None)
    with _LOCK:
        return list(_RUNS.pop(run_id, []))


def record_metric(
    *,
    success: bool,
    latency_ms: float,
    route: str | None = None,
    expected_route: str | None = None,
    tool: str | None = None,
    retried: bool = False,
    recovered: bool = False,
    step: str | None = None,
    tokens: int | None = None,
    detail: dict | None = None,
) -> dict[str, Any]:
    """写入一条可汇总的指标。route_hit 仅在提供期望路由时计算。"""
    route_hit = None
    if expected_route is not None and route is not None:
        route_hit = route == expected_route
    event: dict[str, Any] = {
        "route": route,
        "expected_route": expected_route,
        "route_hit": route_hit,
        "tool": tool,
        "success": success,
        "latency_ms": round(float(latency_ms), 3),
        "retried": retried,
        "recovered": recovered,
        "step": step,
        "tokens": tokens,
        "detail": detail or {},
    }
    rid = _run_id.get()
    if rid:
        event["run_id"] = rid
    logger.info(
        "metric",
        route=route,
        route_hit=route_hit,
        tool=tool,
        success=success,
        latency_ms=event["latency_ms"],
        retried=retried,
        recovered=recovered,
        step=step,
        tokens=tokens,
    )
    with _LOCK:
        _BUFFER.append(event)
        if len(_BUFFER) > _MAX:
            del _BUFFER[: len(_BUFFER) - _MAX]
        if rid and rid in _RUNS:
            _RUNS[rid].append(event)
    return event


def recent_metrics() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_BUFFER)


def reset_metrics() -> None:
    with _LOCK:
        _BUFFER.clear()
        _RUNS.clear()


def percentile(values: list[float], p: float) -> float:
    """最近秩百分位。p=95 表示 P95。"""
    if not values:
        raise ValueError("percentile requires at least one sample")
    ordered = sorted(float(v) for v in values)
    idx = max(0, min(len(ordered) - 1, math.ceil(p / 100.0 * len(ordered)) - 1))
    return ordered[idx]

"""Authenticated, bounded host API for the local MLX reranker."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response

from .本地精排 import (
    内存指导上限字节, 完整性, 内存状态, 卸载, 请求超限, 默认精排器, 状态,
)


_最大文本 = 16_384
_最大请求文档 = 16
_最大待排队 = 3
_请求等待秒 = 60.0
_空闲卸载秒 = 180.0
_最大告警数 = 8
_最大告警请求字节 = 64 * 1024
_告警等待秒 = 1.8
_重启锁 = threading.Lock()
_已安排重启 = False


@dataclass
class _任务:
    query: str
    documents: list[str]
    future: asyncio.Future[list[float]]


def _令牌有效(authorization: str | None) -> bool:
    expected = os.environ.get("XJ_RERANK_TOKEN", "")
    supplied = str(authorization or "")
    if not expected or len(expected) < 32 or not supplied.startswith("Bearer "):
        return False
    value = supplied[7:]
    return len(value) == len(expected) and hmac.compare_digest(value, expected)


def _文本(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _最大文本:
        raise HTTPException(400, f"{name} 无效")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise HTTPException(400, f"{name} 含控制字符")
    return value


def _告警模块():
    """只在宿主服务收到告警请求时加载告警表达代码；容器不会导入它。"""
    os.environ["XJ_ALERT_RENDER_LOCAL"] = "1"
    source = Path(os.environ.get("XJ_RELEASE_SOURCE", "")).expanduser().resolve()
    tools_root = source / "工具"
    if tools_root.is_dir() and str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    import 告警表达

    return 告警表达


def _清理告警(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(400, "alerts 中每一项必须是对象")
    try:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "告警内容不是有效 JSON") from exc
    if len(raw) > 8 * 1024:
        raise HTTPException(413, "单张告警内容过大")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if len(name) > 64 or any(ord(char) < 32 for char in name):
            raise HTTPException(400, "告警字段名无效")
        if isinstance(item, str):
            if len(item) > 2048 or any(ord(char) < 32 and char not in "\n\t" for char in item):
                raise HTTPException(400, "告警字段内容无效")
            clean[name] = item
        elif isinstance(item, (int, float, bool)) or item is None:
            clean[name] = item
        else:
            raise HTTPException(400, "告警字段类型无效")
    return clean


def _请求进程重启() -> None:
    """只终止卡死的独立精排角色；LaunchAgent KeepAlive 会拉起干净进程。"""
    global _已安排重启
    with _重启锁:
        if _已安排重启:
            return
        _已安排重启 = True

    def exit_after_response() -> None:
        time.sleep(0.25)
        os._exit(70)

    threading.Thread(
        target=exit_after_response,
        name="local-rerank-forced-restart",
        daemon=True,
    ).start()


async def _worker(app: FastAPI) -> None:
    while True:
        job = await app.state.queue.get()
        if job is None:
            app.state.queue.task_done()
            return
        try:
            if job.future.cancelled():
                continue
            app.state.processing = True
            app.state.last_activity = time.monotonic()
            try:
                scores = await app.state.reranker.rerank(job.query, job.documents)
            except Exception as exc:  # noqa: BLE001
                if not job.future.done():
                    job.future.set_exception(exc)
            else:
                if not job.future.done():
                    job.future.set_result(scores)
            finally:
                app.state.processing = False
                app.state.last_activity = time.monotonic()
        finally:
            app.state.queue.task_done()


async def _检查空闲(app: FastAPI, *, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    runtime = 状态()
    if (
        runtime.get("可用")
        and not app.state.processing
        and app.state.queue.empty()
        and now - app.state.last_activity >= _空闲卸载秒
    ):
        return await asyncio.to_thread(卸载)
    return False


async def _空闲巡检(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(30)
        await _检查空闲(app)


async def _停止(app: FastAPI) -> None:
    app.state.closing = True
    app.state.idle_task.cancel()
    await asyncio.gather(app.state.idle_task, return_exceptions=True)
    while True:
        try:
            queued = app.state.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if queued is not None and not queued.future.done():
            queued.future.cancel()
        app.state.queue.task_done()
    app.state.queue.put_nowait(None)
    try:
        await asyncio.wait_for(asyncio.shield(app.state.worker_task), timeout=_请求等待秒 + 5)
    except asyncio.TimeoutError:
        app.state.worker_task.cancel()
        await asyncio.gather(app.state.worker_task, return_exceptions=True)
    await asyncio.to_thread(卸载)


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("XJ_RERANK_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("本地精排服务内部令牌无效")
    app.state.reranker = 默认精排器()
    app.state.queue = asyncio.Queue(maxsize=_最大待排队)
    app.state.processing = False
    app.state.closing = False
    app.state.last_activity = time.monotonic()
    app.state.alert_slots = asyncio.Semaphore(_最大告警数)
    app.state.worker_task = asyncio.create_task(_worker(app), name="local-rerank-worker")
    app.state.idle_task = asyncio.create_task(_空闲巡检(app), name="local-rerank-idle-release")
    try:
        yield
    finally:
        await _停止(app)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz(
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地精排服务未授权")
    complete, detail = await asyncio.to_thread(完整性)
    memory = 内存状态()
    resource_ok = int(memory.get("active_bytes") or 0) <= 内存指导上限字节
    if not complete or not resource_ok:
        response.status_code = 503
    return {
        "ok": complete and resource_ok,
        "ready": complete and resource_ok and not app.state.closing,
        "detail": detail if resource_ok else "本地精排活动内存超过 8GB，已暂停接收新请求",
        "model": app.state.reranker.model_id,
        "files": {"complete": complete, "detail": detail},
        "state": 状态(),
        "queue": {
            "waiting": app.state.queue.qsize(),
            "capacity": _最大待排队,
            "processing": bool(app.state.processing),
        },
        "memory": memory,
    }


@app.get("/alert-healthz")
async def alert_healthz(
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地告警服务未授权")
    try:
        module = _告警模块()
        complete, detail = await asyncio.to_thread(module.完整性)
        state = await asyncio.to_thread(module.状态)
    except Exception as exc:  # noqa: BLE001
        response.status_code = 503
        return {
            "ok": False,
            "ready": False,
            "state": {"状态": "不可用", "错误": f"{type(exc).__name__}: {str(exc)[:180]}"},
        }
    memory = state.get("内存") if isinstance(state, dict) else None
    active_bytes = int(memory.get("活动字节") or 0) if isinstance(memory, dict) else 0
    resource_ok = active_bytes <= 内存指导上限字节
    ready = complete and resource_ok and not app.state.closing
    if not ready:
        response.status_code = 503
    return {
        "ok": ready,
        "ready": ready,
        "detail": detail if resource_ok else "本地告警与精排共用的活动内存超过 8GB，已改用规则版",
        "state": state,
        "queue": {"capacity": _最大告警数, "available": app.state.alert_slots._value},
    }


@app.post("/alert-render")
async def alert_render(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地告警服务未授权")
    if set(payload) != {"alerts"}:
        raise HTTPException(400, "本地告警请求只允许 alerts 字段")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not 1 <= len(alerts) <= _最大告警数:
        raise HTTPException(400, f"alerts 必须是 1 到 {_最大告警数} 张告警")
    try:
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _最大告警请求字节:
            raise HTTPException(413, "本地告警请求过大")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "本地告警请求不是有效 JSON") from exc
    clean = [_清理告警(item) for item in alerts]
    try:
        await asyncio.wait_for(app.state.alert_slots.acquire(), timeout=0.05)
    except asyncio.TimeoutError as exc:
        raise HTTPException(429, "本地告警表达服务正在忙，请保留规则版告警") from exc
    try:
        module = _告警模块()
        rendered = await asyncio.to_thread(
            module.呈现, clean, 首项等待秒=_告警等待秒,
        )
    except Exception:
        # 告警表达不是业务决策，任何模型/桥接异常都由调用方继续显示规则版。
        rendered = clean
    finally:
        app.state.alert_slots.release()
    return {"alerts": rendered}


async def _提交(query: str, documents: list[str]) -> list[float]:
    if app.state.closing:
        raise HTTPException(503, "本地精排服务正在关闭")
    loop = asyncio.get_running_loop()
    future: asyncio.Future[list[float]] = loop.create_future()
    try:
        app.state.queue.put_nowait(_任务(query, documents, future))
    except asyncio.QueueFull as exc:
        raise HTTPException(429, "本地精排正在忙，等待队列已满") from exc
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=_请求等待秒)
    except asyncio.TimeoutError as exc:
        future.cancel()
        if bool(getattr(app.state, "processing", False)):
            _请求进程重启()
        raise HTTPException(504, "本地精排等待超过 60 秒") from exc
    except asyncio.CancelledError:
        future.cancel()
        raise


@app.post("/rerank")
async def rerank(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地精排服务未授权")
    if int(内存状态().get("active_bytes") or 0) > 内存指导上限字节:
        raise HTTPException(503, "本地精排活动内存超过 8GB，本次改用基础排序")
    if set(payload) - {"query", "documents"}:
        raise HTTPException(400, "本地精排请求含不允许的参数")
    query = _文本(payload.get("query"), "query")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not 1 <= len(documents) <= _最大请求文档:
        raise HTTPException(400, "documents 必须是 1 到 16 段文本")
    clean = [_文本(item, "document") for item in documents]
    try:
        scores = await _提交(query, clean)
    except HTTPException:
        raise
    except 请求超限 as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"本地精排模型不可用：{type(exc).__name__}: {str(exc)[:180]}") from exc
    return {"model": app.state.reranker.model_id, "scores": scores}

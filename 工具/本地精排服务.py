#!/usr/bin/env python3
"""开发办公室专用的独立本机精排服务。"""
from __future__ import annotations

import asyncio
import hmac
import os
import signal
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Response

from 本地精排 import (
    默认内存上限字节, 最大文档数, 完整性, 状态, 同步精排, 释放模型, 请求超限,
)


最大排队数 = 3
默认等待秒 = 60.0
默认空闲释放秒 = 180.0
_最大查询字符 = 2048
_最大文档字符 = 8192


class 精排繁忙(RuntimeError):
    pass


class 精排等待超时(RuntimeError):
    pass


@dataclass
class _作业:
    query: str
    documents: list[str]
    future: asyncio.Future[list[float]]


class 有界精排队列:
    """一个 MLX 工作者，外加固定长度的先进先出等待队列。"""

    def __init__(
        self,
        *,
        scorer: Callable[[str, list[str]], list[float]] = 同步精排,
        releaser: Callable[[], bool] = 释放模型,
        queue_limit: int = 最大排队数,
        wait_timeout: float = 默认等待秒,
        idle_timeout: float = 默认空闲释放秒,
    ) -> None:
        self._scorer = scorer
        self._releaser = releaser
        self._queue: asyncio.Queue[_作业 | None] = asyncio.Queue(maxsize=max(1, queue_limit))
        self._wait_timeout = max(0.05, float(wait_timeout))
        self._idle_timeout = max(0.05, float(idle_timeout))
        self._worker: asyncio.Task[None] | None = None
        self._closing = False
        self._active = False
        self._accepted = 0
        self._completed = 0
        self._failed = 0
        self._rejected = 0
        self._timed_out = 0
        self._idle_releases = 0

    async def start(self) -> None:
        if self._closing:
            raise 精排繁忙("本地精排服务正在关闭")
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="local-rerank-worker")

    async def close(self) -> None:
        self._closing = True
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if job is not None and not job.future.done():
                job.future.set_exception(RuntimeError("本地精排服务正在关闭"))
            self._queue.task_done()
        safe_to_release = True
        if self._worker is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._worker), timeout=self._wait_timeout + 5,
                )
            except asyncio.TimeoutError:
                safe_to_release = False
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if safe_to_release:
            await asyncio.to_thread(self._releaser)

    def snapshot(self) -> dict[str, Any]:
        return {
            "处理中": self._active,
            "排队数": self._queue.qsize(),
            "排队上限": self._queue.maxsize,
            "已接收": self._accepted,
            "已完成": self._completed,
            "失败": self._failed,
            "已拒绝": self._rejected,
            "等待超时": self._timed_out,
            "空闲释放次数": self._idle_releases,
            "等待上限秒": self._wait_timeout,
            "空闲释放秒": self._idle_timeout,
        }

    async def submit(self, query: str, documents: list[str]) -> list[float]:
        if self._closing:
            raise 精排繁忙("本地精排服务正在关闭")
        await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[float]] = loop.create_future()
        try:
            self._queue.put_nowait(_作业(query, documents, future))
        except asyncio.QueueFull as exc:
            self._rejected += 1
            raise 精排繁忙("本地精排正在处理其他搜索，本次改用基础排序") from exc
        self._accepted += 1
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=self._wait_timeout)
        except asyncio.TimeoutError as exc:
            self._timed_out += 1
            if not future.done():
                future.cancel()
            raise 精排等待超时("本地精排等待超时，本次改用基础排序") from exc

    async def _run(self) -> None:
        while True:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=self._idle_timeout)
            except asyncio.TimeoutError:
                if self._closing:
                    break
                released = await asyncio.to_thread(self._releaser)
                if released:
                    self._idle_releases += 1
                continue
            except asyncio.CancelledError:
                break
            if job is None:
                self._queue.task_done()
                break
            if job.future.cancelled():
                self._queue.task_done()
                continue
            self._active = True
            try:
                result = await asyncio.to_thread(self._scorer, job.query, job.documents)
                self._completed += 1
                if not job.future.done():
                    job.future.set_result(result)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:  # noqa: BLE001
                self._failed += 1
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._active = False
                self._queue.task_done()


def _环境浮点(name: str, default: float, lower: float, upper: float) -> float:
    try:
        return max(lower, min(float(os.environ.get(name, str(default)).strip()), upper))
    except (TypeError, ValueError):
        return default


def _令牌有效(authorization: str | None) -> bool:
    expected = os.environ.get("XJ_RERANK_TOKEN", "")
    supplied = str(authorization or "")
    if len(expected) < 32 or not supplied.startswith("Bearer "):
        return False
    value = supplied[7:]
    return len(value) == len(expected) and hmac.compare_digest(value, expected)


def _文本(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise HTTPException(400, f"{name} 无效或过长")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise HTTPException(400, f"{name} 含控制字符")
    return value


async def _监护父进程() -> None:
    try:
        parent_pid = int(os.environ.get("XJ_RERANK_PARENT_PID", "0"))
    except ValueError:
        parent_pid = 0
    if parent_pid <= 1:
        return
    while True:
        await asyncio.sleep(2)
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        except PermissionError:
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("XJ_RERANK_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("本地精排服务内部令牌无效")
    app.state.queue = 有界精排队列(
        queue_limit=max(1, min(int(os.environ.get("XJ_RERANK_QUEUE_LIMIT", 最大排队数)), 8)),
        wait_timeout=_环境浮点("XJ_RERANK_TIMEOUT_SECONDS", 默认等待秒, 1, 180),
        idle_timeout=_环境浮点("XJ_RERANK_IDLE_SECONDS", 默认空闲释放秒, 5, 3600),
    )
    await app.state.queue.start()
    guardian = asyncio.create_task(_监护父进程(), name="local-rerank-parent-guardian")
    try:
        yield
    finally:
        guardian.cancel()
        await asyncio.gather(guardian, return_exceptions=True)
        await app.state.queue.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz(
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地精排服务未授权")
    # 存活检查只查文件和内存账，绝不加载模型或做推理。
    files_ok, files_detail = 完整性()
    runtime = 状态()
    active = int((runtime.get("内存") or {}).get("活动字节") or 0)
    resource_ok = active <= 默认内存上限字节
    if not files_ok or not resource_ok:
        response.status_code = 503
    return {
        "ok": files_ok and resource_ok,
        "ready": files_ok and resource_ok and not app.state.queue._closing,
        "detail": files_detail if resource_ok else "本地精排活动内存超过 8GB，已暂停接收新请求",
        "state": {**runtime, "队列": app.state.queue.snapshot()},
    }


@app.post("/rerank")
async def rerank(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not _令牌有效(authorization):
        raise HTTPException(403, "本地精排服务未授权")
    active = int((状态().get("内存") or {}).get("活动字节") or 0)
    if active > 默认内存上限字节:
        raise HTTPException(503, "本地精排活动内存超过 8GB，本次改用基础排序")
    if set(payload) - {"query", "documents"}:
        raise HTTPException(400, "本地精排请求含不允许的参数")
    query = _文本(payload.get("query"), "query", _最大查询字符)
    documents = payload.get("documents")
    if not isinstance(documents, list) or not 1 <= len(documents) <= 最大文档数:
        raise HTTPException(400, f"documents 必须是 1 到 {最大文档数} 段文本")
    clean = [_文本(item, "document", _最大文档字符) for item in documents]
    try:
        scores = await app.state.queue.submit(query, clean)
    except 精排繁忙 as exc:
        raise HTTPException(429, str(exc)) from exc
    except 精排等待超时 as exc:
        raise HTTPException(504, str(exc)) from exc
    except 请求超限 as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"本地精排暂不可用：{type(exc).__name__}: {str(exc)[:160]}") from exc
    return {
        "model": f"mlx-community/Qwen3-Reranker-4B-mxfp8@25f203a237b8",
        "scores": scores,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("XJ_RERANK_HOST", "127.0.0.1"),
        port=int(os.environ.get("XJ_RERANK_PORT", "37659")),
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

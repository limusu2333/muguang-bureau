#!/usr/bin/env python3
"""办公室主进程使用的本地精排客户端与按需进程管理。"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx

from 本地精排 import 最大文档数, 完整性, 模型仓, 模型版本


COMPANY = Path(__file__).resolve().parents[1]
服务脚本 = Path(__file__).resolve().with_name("本地精排服务.py")
默认端口 = 37659
启动等待秒 = 12.0
请求等待秒 = 65.0
熔断失败数 = 3
熔断秒 = 60.0

_服务锁 = threading.RLock()
_服务进程: subprocess.Popen[bytes] | None = None
_服务令牌 = secrets.token_urlsafe(36)
_故障锁 = threading.Lock()
_连续故障 = 0
_熔断到 = 0.0
_最后错误 = ""


class 本地精排不可用(RuntimeError):
    pass


class 本地精排繁忙(本地精排不可用):
    pass


class 本地精排请求被拒绝(本地精排不可用):
    """输入越界只影响本次搜索，不代表精排服务发生故障。"""


def _端口() -> int:
    try:
        return max(1024, min(int(os.environ.get("XJ_DEV_RERANK_PORT", 默认端口)), 65535))
    except (TypeError, ValueError):
        return 默认端口


def _网址() -> str:
    return f"http://127.0.0.1:{_端口()}"


def _请求健康(timeout: float = 1.0) -> dict[str, Any]:
    request = urllib.request.Request(
        _网址() + "/healthz",
        headers={"authorization": "Bearer " + _服务令牌},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise 本地精排不可用("本地精排服务没有通过存活检查")
    return payload


def _进程还在() -> bool:
    return _服务进程 is not None and _服务进程.poll() is None


def _确保服务同步() -> dict[str, Any]:
    global _服务进程
    with _服务锁:
        if _进程还在():
            deadline = time.monotonic() + 3.0
            while _进程还在() and time.monotonic() < deadline:
                try:
                    return _请求健康()
                except urllib.error.HTTPError as exc:
                    if exc.code == 503:
                        raise 本地精排不可用("本地精排服务已因资源异常暂停，本次改用基础排序") from exc
                except 本地精排不可用:
                    raise
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            if _进程还在():
                停止服务()
        if _服务进程 is not None and _服务进程.poll() is not None:
            _服务进程 = None

        ok, detail = 完整性()
        if not ok:
            raise 本地精排不可用(f"本地精排模型文件不完整：{detail}")

        # 防止上一任办公室的子进程正在退出时，直接撞到同一个端口。
        deadline = time.monotonic() + 3.0
        occupied = False
        while time.monotonic() < deadline:
            try:
                return _请求健康(timeout=0.3)
            except urllib.error.HTTPError as exc:
                occupied = True
                if exc.code == 403:
                    time.sleep(0.1)
                    continue
                break
            except (OSError, ValueError, urllib.error.URLError, 本地精排不可用):
                break
        if occupied:
            raise 本地精排不可用("本地精排端口正被另一间办公室占用，本次改用基础排序")

        log_dir = COMPANY / "运行状态" / "as_log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "local-rerank.log"
        env = os.environ.copy()
        env.update({
            "XJ_RERANK_TOKEN": _服务令牌,
            "XJ_RERANK_HOST": "127.0.0.1",
            "XJ_RERANK_PORT": str(_端口()),
            "XJ_RERANK_PARENT_PID": str(os.getpid()),
        })
        with log_path.open("ab", buffering=0) as log:
            _服务进程 = subprocess.Popen(
                [sys.executable, str(服务脚本)],
                cwd=str(COMPANY),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        deadline = time.monotonic() + 启动等待秒
        last_error = ""
        while time.monotonic() < deadline:
            if _服务进程.poll() is not None:
                last_error = f"服务进程提前退出（状态 {_服务进程.returncode}）"
                break
            try:
                return _请求健康(timeout=0.5)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.1)
        停止服务()
        raise 本地精排不可用(f"本地精排独立服务没有启动成功：{last_error or '等待超时'}")


def 停止服务() -> None:
    global _服务进程
    with _服务锁:
        process = _服务进程
        _服务进程 = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


atexit.register(停止服务)


def _熔断状态() -> tuple[bool, float]:
    with _故障锁:
        remaining = max(0.0, _熔断到 - time.monotonic())
        return remaining > 0, remaining


def _记录成功() -> None:
    global _连续故障, _熔断到, _最后错误
    with _故障锁:
        _连续故障 = 0
        _熔断到 = 0.0
        _最后错误 = ""


def _记录故障(exc: BaseException) -> None:
    global _连续故障, _熔断到, _最后错误
    with _故障锁:
        _连续故障 += 1
        _最后错误 = f"{type(exc).__name__}: {str(exc)[:180]}"
        if _连续故障 >= 熔断失败数:
            _熔断到 = time.monotonic() + 熔断秒


def 状态() -> dict[str, Any]:
    running = _进程还在()
    payload: dict[str, Any] = {}
    if running:
        try:
            payload = _请求健康(timeout=0.8)
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    files_ok, files_detail = 完整性()
    open_now, remaining = _熔断状态()
    with _故障锁:
        failure = {"连续故障": _连续故障, "最后错误": _最后错误}
    return {
        "服务状态": "运行中" if running else "未启动",
        "服务进程": int(_服务进程.pid) if running and _服务进程 is not None else None,
        "模型文件完整": files_ok,
        "模型文件说明": files_detail,
        "熔断中": open_now,
        "熔断剩余秒": round(remaining, 1),
        **failure,
        "服务": payload,
    }


class LocalQwen3Reranker:
    """统一搜索默认精排器；主进程自身不加载 MLX 模型。"""

    model_id = f"{模型仓}@{模型版本[:12]}:isolated"
    max_documents = 最大文档数

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if len(documents) > self.max_documents:
            raise ValueError(f"本地精排一次最多接收 {self.max_documents} 篇候选，实际 {len(documents)} 篇")
        if not documents:
            return []
        opened, remaining = _熔断状态()
        if opened:
            raise 本地精排不可用(f"本地精排刚才连续失败，{remaining:.0f} 秒内改用基础排序")
        try:
            await asyncio.to_thread(_确保服务同步)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(请求等待秒, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    _网址() + "/rerank",
                    json={"query": str(query), "documents": [str(item) for item in documents]},
                    headers={"authorization": "Bearer " + _服务令牌},
                )
            if response.status_code == 429:
                raise 本地精排繁忙("本地精排正在排队，本次改用基础排序")
            if response.status_code == 400:
                raise 本地精排请求被拒绝("本次搜索内容超过本地精排限制，已改用基础排序")
            if response.status_code == 504:
                await asyncio.to_thread(停止服务)
                raise 本地精排不可用("本地精排等待超时，本次改用基础排序")
            if response.status_code != 200:
                raise 本地精排不可用(f"本地精排服务返回 HTTP {response.status_code}")
            payload = response.json()
            scores = payload.get("scores") if isinstance(payload, dict) else None
            if not isinstance(scores, list) or len(scores) != len(documents):
                raise 本地精排不可用("本地精排返回的分数数量不正确")
            _记录成功()
            return [float(score) for score in scores]
        except (本地精排繁忙, 本地精排请求被拒绝):
            raise
        except httpx.TimeoutException as exc:
            await asyncio.to_thread(停止服务)
            _记录故障(exc)
            raise 本地精排不可用("本地精排响应超时，本次改用基础排序") from exc
        except Exception as exc:  # noqa: BLE001
            _记录故障(exc)
            raise


_默认精排器 = LocalQwen3Reranker()


def 默认精排器() -> LocalQwen3Reranker:
    return _默认精排器


def 同步精排(query: str, documents: list[str]) -> list[float]:
    return asyncio.run(_默认精排器.rerank(query, documents))

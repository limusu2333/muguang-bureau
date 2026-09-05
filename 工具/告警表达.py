#!/usr/bin/env python3
"""把机器故障编译成船主看得懂、能直接拍板的问题卡。"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any

from 下载本地告警模型 import 完整性, 模型目录

_任务队列: queue.Queue[tuple[str, dict[str, Any], cf.Future[dict[str, Any]]] | None] = queue.Queue(maxsize=8)
_线程: threading.Thread | None = None
_线程锁 = threading.Lock()
_状态锁 = threading.Lock()
_状态: dict[str, Any] = {"状态": "未启动", "模型": "Qwen3.5-4B-4bit", "错误": "", "加载秒": None}
_缓存上限 = 256
_缓存: OrderedDict[str, dict[str, Any]] = OrderedDict()
_处理中: dict[str, cf.Future[dict[str, Any]]] = {}
_空闲释放秒 = 180.0
_内存指导上限 = 8 * 1024**3
_远端状态锁 = threading.Lock()
_远端状态缓存: tuple[float, dict[str, Any]] | None = None


def _远端配置() -> tuple[str, str, bool]:
    """返回宿主告警服务地址、内部令牌和是否强制走宿主。

    办公室/正式容器的运行环境会设置 ``XJ_ALERT_RENDER_REQUIRED``，
    防止宿主服务暂时不可用时悄悄在每个用户容器里各自加载 MLX。
    """
    if os.environ.get("XJ_ALERT_RENDER_LOCAL") == "1":
        return "", "", False
    required = os.environ.get("XJ_ALERT_RENDER_REQUIRED") == "1"
    raw_url = os.environ.get("XJ_ALERT_RENDER_URL", "").strip().rstrip("/")
    token = os.environ.get("XJ_ALERT_RENDER_TOKEN", "").strip()
    if not raw_url:
        return "", token, required
    parsed = urllib.parse.urlsplit(raw_url)
    allowed_hosts = {"127.0.0.1", "localhost", "alert-render", "local-rerank-bridge"}
    try:
        port = parsed.port or 80
    except ValueError:
        return "", token, True
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.hostname not in allowed_hosts
        or not 1024 <= port <= 65_535
    ):
        return "", token, True
    return raw_url, token, True


def _远端端点(path: str) -> tuple[str, str] | None:
    base, token, required = _远端配置()
    if not base:
        return None
    if len(token) < 32:
        return None
    suffix = base if base.endswith(path) else base + path
    return suffix, token


def _远端健康() -> dict[str, Any]:
    endpoint = _远端端点("/alert-healthz")
    if endpoint is None:
        return {"状态": "宿主服务未配置", "可用": False, "后端": "宿主告警服务"}
    url, token = endpoint
    request = urllib.request.Request(url, headers={"authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=0.8) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("宿主告警服务返回格式无效")
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        return {
            "状态": "宿主服务" if payload.get("ok") else "宿主服务异常",
            "可用": bool(payload.get("ok")),
            "后端": "宿主告警服务",
            "模型": state.get("模型", "Qwen3.5-4B-4bit"),
            "宿主": state,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "状态": "宿主服务不可达",
            "可用": False,
            "后端": "宿主告警服务",
            "错误": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def _改状态(**fields: Any) -> None:
    with _状态锁:
        _状态.update(fields)


def _mlx内存() -> dict[str, int]:
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return {"活动字节": 0, "缓存字节": 0, "峰值字节": 0}
    try:
        return {
            "活动字节": int(mx.get_active_memory()),
            "缓存字节": int(mx.get_cache_memory()),
            "峰值字节": int(mx.get_peak_memory()),
        }
    except Exception:  # noqa: BLE001
        return {"活动字节": 0, "缓存字节": 0, "峰值字节": 0}


def 状态() -> dict[str, Any]:
    remote_url, _, required = _远端配置()
    if required:
        global _远端状态缓存
        now = time.monotonic()
        with _远端状态锁:
            if _远端状态缓存 is None or now - _远端状态缓存[0] >= 2.0:
                _远端状态缓存 = (now, _远端健康())
            return dict(_远端状态缓存[1])
    with _状态锁:
        out = dict(_状态)
        out["已编译告警数"] = len(_缓存)
    out["目录"] = str(模型目录())
    out["可用"] = out.get("状态") == "可用"
    out["内存"] = _mlx内存()
    return out


def _清理文本(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _提取对象(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("本地模型没有返回 JSON 对象")
    data = json.loads(text[start:end])
    if not isinstance(data, dict):
        raise ValueError("本地模型返回的不是对象")
    return data


def _校验结果(alert: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    detail = _清理文本(data.get("详情"), 360)
    if not detail:
        raise ValueError("本地模型漏掉了人话详情")
    return {"详情": detail}


def _写缓存_已锁(fingerprint: str, rendered: dict[str, Any]) -> None:
    _缓存[fingerprint] = rendered
    _缓存.move_to_end(fingerprint)
    while len(_缓存) > _缓存上限:
        _缓存.popitem(last=False)


def _提示(alert: dict[str, Any]) -> str:
    source = {
        "谁": alert.get("负责人称谓") or alert.get("负责人") or "公司员工",
        "原任务": alert.get("目标") or "未命名任务",
        "任务状态": alert.get("任务状态") or alert.get("级别") or "异常",
        "发生位置": alert.get("房间") or "公司内部",
        "已整理事实": alert.get("详情") or "没有留下详情",
        "故障类型": alert.get("故障类型") or "",
        "原始技术信息": alert.get("原始详情") or alert.get("详情") or "没有留下详情",
    }
    return (
        "你只负责把系统已经整理好的故障事实润色成普通人看得懂的中文，不负责重新判断和解决问题。\n"
        "‘已整理事实’是系统已经核对出的事实，必须保留谁、做了什么、哪里出问题、造成什么结果。\n"
        "不要把具体事实改回‘上游不可用’‘系统异常’这类空话，也不要凭空加入权限、费用或解决办法。\n"
        "原任务只帮助说明故障发生时在做什么，不能用它推测原因、权限、路径、解决办法或新增限制。\n"
        "删除 RuntimeError、ValueError 等包装和英文堆栈；原始技术信息只用于保留明确证据，不覆盖已整理事实。\n"
        "不要给建议，不要列选项，不用 Markdown。只输出一个 JSON 对象："
        '{"详情":""}\n'
        "原始资料：" + json.dumps(source, ensure_ascii=False)
    )


def _工作循环() -> None:
    started = time.monotonic()
    try:
        ok, message = 完整性()
        if not ok:
            raise FileNotFoundError(f"本地告警模型不可用：{message}")
        _改状态(状态="加载中", 错误="")
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_sampler

        mx.set_cache_limit(256 * 1024**2)
        mx.set_memory_limit(8 * 1024**3)
        mx.reset_peak_memory()
        model, tokenizer = load(str(模型目录()), lazy=False)
        _改状态(状态="预热中")
        warm_messages = [
            {"role": "system", "content": "只输出简短 JSON。"},
            {"role": "user", "content": '输出 {"状态":"好"}'},
        ]
        warm_prompt = tokenizer.apply_chat_template(
            warm_messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        generate(model, tokenizer, prompt=warm_prompt, max_tokens=24, sampler=make_sampler(temp=0.0))
        _改状态(状态="可用", 加载秒=round(time.monotonic() - started, 2), 错误="")
    except Exception as exc:  # noqa: BLE001
        _改状态(状态="不可用", 错误=f"{type(exc).__name__}: {exc}", 加载秒=round(time.monotonic() - started, 2))
        while True:
            try:
                item = _任务队列.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                fingerprint, _, future = item
                with _状态锁:
                    _处理中.pop(fingerprint, None)
                future.set_exception(exc)
        return

    while True:
        try:
            item = _任务队列.get(timeout=_空闲释放秒)
        except queue.Empty:
            with _线程锁:
                try:
                    item = _任务队列.get_nowait()
                except queue.Empty:
                    _改状态(状态="卸载中")
                    item = None
                    should_exit = True
                else:
                    should_exit = False
            if not should_exit:
                pass
            else:
                del model, tokenizer
                import gc

                gc.collect()
                mx.clear_cache()
                _改状态(状态="未启动")
                return
        if item is None:
            del model, tokenizer
            import gc

            gc.collect()
            mx.clear_cache()
            _改状态(状态="未启动")
            return
        fingerprint, alert, future = item
        try:
            messages = [
                {"role": "system", "content": "你只负责把公司事故翻译成准确、简洁、可执行的中文 JSON。"},
                {"role": "user", "content": _提示(alert)},
            ]
            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            raw = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=180,
                sampler=make_sampler(temp=0.0),
            )
            rendered = _校验结果(alert, _提取对象(raw))
            with _状态锁:
                _写缓存_已锁(fingerprint, rendered)
                _处理中.pop(fingerprint, None)
            future.set_result(rendered)
        except Exception as exc:  # noqa: BLE001
            with _状态锁:
                _处理中.pop(fingerprint, None)
                _状态["最近生成错误"] = f"{type(exc).__name__}: {exc}"
            future.set_exception(exc)
        finally:
            mx.clear_cache()


def 启动并预热(*, 等待秒: float = 45.0) -> dict[str, Any]:
    global _线程
    if _远端配置()[2]:
        return 状态()
    if os.environ.get("XJ_MOCK") == "1" or os.environ.get("XJ_ALERT_MODEL_DISABLED") == "1":
        _改状态(状态="已停用", 错误="演示/测试模式不加载本地模型")
        return 状态()
    with _线程锁:
        if _线程 is None or not _线程.is_alive():
            _线程 = threading.Thread(target=_工作循环, name="本地告警表达器", daemon=True)
            _线程.start()
    deadline = time.monotonic() + max(0.0, 等待秒)
    while time.monotonic() < deadline:
        info = 状态()
        if info["状态"] in ("可用", "不可用"):
            return info
        time.sleep(0.05)
    return 状态()


def _提交(alert: dict[str, Any]) -> tuple[cf.Future[dict[str, Any]] | None, str]:
    fingerprint = str(alert.get("指纹") or "")
    if not fingerprint:
        return None, fingerprint
    with _线程锁, _状态锁:
        memory = _mlx内存()
        if _状态.get("状态") != "可用" or int(memory.get("活动字节") or 0) > _内存指导上限:
            return None, fingerprint
        cached = _缓存.get(fingerprint)
        if cached is not None:
            _缓存.move_to_end(fingerprint)
            done: cf.Future[dict[str, Any]] = cf.Future()
            done.set_result(copy.deepcopy(cached))
            return done, fingerprint
        future = _处理中.get(fingerprint)
        if future is None:
            future = cf.Future()
            _处理中[fingerprint] = future
            try:
                _任务队列.put_nowait((fingerprint, copy.deepcopy(alert), future))
            except queue.Full:
                _处理中.pop(fingerprint, None)
                return None, fingerprint
        return future, fingerprint


def 呈现(alerts: list[dict[str, Any]], *, 首项等待秒: float | None = None) -> list[dict[str, Any]]:
    """首张卡最多短等一次，其余卡后台生成；任何异常都原样降级。"""
    out = [copy.deepcopy(item) for item in alerts]
    if not out:
        return out
    wait = 首项等待秒
    if wait is None:
        wait = float(os.environ.get("XJ_ALERT_RENDER_TIMEOUT", "1.8"))
    if _远端配置()[2]:
        return _远端呈现(out, wait=max(0.0, min(wait, 30.0)))
    if 状态().get("状态") == "未启动":
        启动并预热(等待秒=0)
    futures: list[cf.Future[dict[str, Any]] | None] = []
    for item in out:
        future, _ = _提交(item)
        futures.append(future)
    if futures[0] is not None and not futures[0].done() and wait > 0:
        try:
            futures[0].result(timeout=wait)
        except Exception:
            pass
    for item, future in zip(out, futures):
        item["表达来源"] = "规则"
        if future is None or not future.done():
            continue
        try:
            item.update(future.result())
            item["表达来源"] = "本地模型"
        except Exception:
            continue
    return out


def _远端呈现(out: list[dict[str, Any]], *, wait: float) -> list[dict[str, Any]]:
    """一次批量请求宿主；网络、格式或超时均只回到原始规则卡。"""
    for item in out:
        item.setdefault("表达来源", "规则")
    endpoint = _远端端点("/alert-render")
    if endpoint is None:
        return out
    url, token = endpoint
    payload = json.dumps({"alerts": out}, ensure_ascii=False).encode("utf-8")
    if len(payload) > 64 * 1024:
        return out
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "authorization": "Bearer " + token,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, min(wait + 0.8, 8.0))) as response:
            if response.status != 200:
                return out
            raw = json.loads(response.read(128 * 1024).decode("utf-8"))
        rendered = raw.get("alerts") if isinstance(raw, dict) else None
        if not isinstance(rendered, list) or len(rendered) != len(out):
            return out
        result = [copy.deepcopy(item) for item in out]
        for target, source in zip(result, rendered):
            if not isinstance(source, dict):
                continue
            detail = _清理文本(source.get("详情"), 360)
            source_origin = str(source.get("表达来源") or "")
            if detail and source_origin in {"本地模型", "宿主本地模型"}:
                target["详情"] = detail
                target["表达来源"] = "宿主本地模型"
            else:
                target["表达来源"] = "规则"
        return result
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        return out

"""本地精排资源边界的隔离回归；不加载真实模型，不启动真实服务。"""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import os
import queue
import sys
import threading
import time
import urllib.error
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import mlx.core as mx
from fastapi import Response


import 工具间  # noqa: E402
import 告警表达  # noqa: E402
import 本地精排  # noqa: E402
import 本地精排客户端  # noqa: E402
import 本地精排服务  # noqa: E402
import 统一搜索  # noqa: E402


class _假词嵌入:
    def __init__(self) -> None:
        self.weight = mx.array([
            [4.0, 0.0],
            [0.0, 4.0],
            [9.0, 9.0],
            [8.0, 8.0],
        ])


class _假主干:
    def __init__(self, hidden) -> None:
        self.hidden = hidden
        self.embed_tokens = _假词嵌入()

    def __call__(self, _tokens):
        return self.hidden


class _假模型:
    def __init__(self, hidden, *, tied: bool) -> None:
        self.model = _假主干(hidden)
        self.args = SimpleNamespace(tie_word_embeddings=tied)
        self.lm_head = _假词嵌入()

    def __call__(self, _tokens):
        raise AssertionError("不允许调用会生成完整词表结果的模型入口")


def test_本地精排_共享与独立输出层都只计算最后位置两个词():
    hidden = mx.array([
        [[0.0, 0.0], [0.0, 2.0], [7.0, 7.0]],
        [[0.0, 0.0], [0.0, 0.0], [2.0, 0.0]],
    ])
    tokens = mx.array([[1, 2, 0], [1, 2, 3]])
    for tied in (True, False):
        scores = 本地精排._最后位置概率(_假模型(hidden, tied=tied), tokens, [2, 3], 0, 1, mx)
        assert scores.shape == (2,)
        assert float(scores[0].item()) > 0.99
        assert float(scores[1].item()) < 0.01


def test_本地精排_量化输出层只把两个词交给矩阵乘():
    class _量化头:
        weight = mx.zeros((12, 4), dtype=mx.uint32)
        scales = mx.ones((12, 1))
        biases = mx.zeros((12, 1))
        group_size = 64
        bits = 4
        mode = "affine"

    seen: dict[str, tuple[int, ...]] = {}

    def fake_matmul(hidden, weight, **kwargs):
        seen["weight"] = tuple(weight.shape)
        seen["scales"] = tuple(kwargs["scales"].shape)
        return mx.zeros((hidden.shape[0], 2))

    fake_mx = SimpleNamespace(array=mx.array, quantized_matmul=fake_matmul)
    result = 本地精排._两词投影(_量化头(), mx.ones((3, 4)), 2, 7, fake_mx)
    assert tuple(result.shape) == (3, 2)
    assert seen == {"weight": (2, 4), "scales": (2, 1)}


def test_本地精排_异常和成功都清理mlx缓存():
    tokenizer = SimpleNamespace(
        pad_token_id=0,
        eos_token_id=0,
        encode=lambda text, add_special_tokens=False: [1, 2],
    )
    fake_mx = SimpleNamespace(
        array=lambda value: value,
        clear_cache=Mock(),
    )
    with patch.object(本地精排, "_加载分词器", return_value=(tokenizer, 1, 0)), \
         patch.object(本地精排, "_加载", return_value=(object(), tokenizer, 1, 0)), \
         patch.object(
             本地精排, "_最后位置概率",
             side_effect=[mx.array([0.75]), RuntimeError("模拟失败")],
         ), \
         patch.dict(sys.modules, {"mlx.core": fake_mx}):
        assert 本地精排.同步精排("查询", ["证据"]) == [0.75]
        try:
            本地精排.同步精排("查询", ["证据"])
        except RuntimeError as exc:
            assert "模拟失败" in str(exc)
        else:
            raise AssertionError("模拟失败没有向上传递")
    assert fake_mx.clear_cache.call_count == 2


def test_本地精排_总令牌超限会在推理前拒绝():
    tokenizer = SimpleNamespace(
        pad_token_id=0,
        eos_token_id=0,
        encode=lambda text, add_special_tokens=False: [1] * (3000 if "超长" in text else 2),
    )
    infer = Mock(side_effect=AssertionError("超限后不应进入推理"))
    model_load = Mock(side_effect=AssertionError("超限后不应加载4B模型"))
    with patch.object(本地精排, "_加载分词器", return_value=(tokenizer, 1, 0)), \
         patch.object(本地精排, "_加载", model_load), \
         patch.object(本地精排, "_最后位置概率", infer):
        try:
            本地精排.同步精排("查询", ["超长"] * 13)
        except ValueError as exc:
            assert "超过上限" in str(exc)
        else:
            raise AssertionError("超限请求没有被拒绝")
    infer.assert_not_called()
    model_load.assert_not_called()


def test_本地精排服务_排队满立即拒绝且空闲会卸载():
    async def run() -> None:
        release = Mock(return_value=True)
        gate = asyncio.Event()

        def scorer(_query, _documents):
            while not gate.is_set():
                time.sleep(0.005)
            return [0.5]

        queue = 本地精排服务.有界精排队列(
            scorer=scorer, releaser=release, queue_limit=1,
            wait_timeout=1.0, idle_timeout=0.05,
        )
        await queue.start()
        first = asyncio.create_task(queue.submit("一", ["甲"]))
        for _ in range(100):
            if queue.snapshot()["处理中"]:
                break
            await asyncio.sleep(0.005)
        second = asyncio.create_task(queue.submit("二", ["乙"]))
        await asyncio.sleep(0)
        try:
            await queue.submit("三", ["丙"])
        except 本地精排服务.精排繁忙:
            pass
        else:
            raise AssertionError("队列已满时没有拒绝")
        gate.set()
        assert await first == [0.5]
        assert await second == [0.5]
        for _ in range(100):
            if queue.snapshot()["空闲释放次数"]:
                break
            await asyncio.sleep(0.01)
        assert queue.snapshot()["空闲释放次数"] >= 1
        await queue.close()

    asyncio.run(run())


def test_本地精排服务_已超时的排队请求不会继续耗模型():
    async def run() -> None:
        gate = threading.Event()
        calls: list[str] = []

        def scorer(query, _documents):
            calls.append(query)
            if query == "占用工作者":
                gate.wait(timeout=1)
            return [0.5]

        queue = 本地精排服务.有界精排队列(
            scorer=scorer, releaser=Mock(return_value=False), queue_limit=2,
            wait_timeout=0.05, idle_timeout=1,
        )
        await queue.start()
        first = asyncio.create_task(queue.submit("占用工作者", ["甲"]))
        for _ in range(100):
            if queue.snapshot()["处理中"]:
                break
            await asyncio.sleep(0.005)
        try:
            await queue.submit("会超时", ["乙"])
        except 本地精排服务.精排等待超时:
            pass
        else:
            raise AssertionError("排队请求没有按时超时")
        gate.set()
        try:
            await first
        except 本地精排服务.精排等待超时:
            pass
        for _ in range(100):
            if not queue.snapshot()["处理中"]:
                break
            await asyncio.sleep(0.005)
        await queue.close()
        assert calls == ["占用工作者"]

    asyncio.run(run())


def test_本地精排客户端_只读状态不会启动服务或加载模型():
    with patch.object(本地精排客户端, "_服务进程", None), \
         patch.object(本地精排客户端, "完整性", return_value=(True, "完整")), \
         patch.object(本地精排客户端, "_确保服务同步") as start:
        result = 本地精排客户端.状态()
    assert result["服务状态"] == "未启动"
    start.assert_not_called()


def test_本地精排客户端_自有服务资源异常时不重复拉起撞端口():
    process = Mock()
    process.poll.return_value = None
    error = urllib.error.HTTPError("http://127.0.0.1", 503, "resource", {}, None)
    with patch.object(本地精排客户端, "_服务进程", process), \
         patch.object(本地精排客户端, "_请求健康", side_effect=error), \
         patch.object(本地精排客户端.subprocess, "Popen") as spawn:
        try:
            本地精排客户端._确保服务同步()
        except 本地精排客户端.本地精排不可用 as exc:
            assert "资源异常" in str(exc)
        else:
            raise AssertionError("资源异常没有触发基础排序")
    spawn.assert_not_called()


def _假HTTP客户端(status: int) -> MagicMock:
    response = httpx.Response(
        status,
        json={"detail": "测试响应"},
        request=httpx.Request("POST", "http://127.0.0.1/rerank"),
    )
    client = AsyncMock()
    client.post.return_value = response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_本地精排客户端_输入超限不触发全局熔断():
    with 本地精排客户端._故障锁:
        old = (
            本地精排客户端._连续故障,
            本地精排客户端._熔断到,
            本地精排客户端._最后错误,
        )
        本地精排客户端._连续故障 = 0
        本地精排客户端._熔断到 = 0.0
        本地精排客户端._最后错误 = ""
    process = Mock()
    process.poll.return_value = None
    try:
        with patch.object(本地精排客户端, "_服务进程", process), \
             patch.object(本地精排客户端, "_确保服务同步"), \
             patch.object(
                 本地精排客户端.httpx, "AsyncClient",
                 return_value=_假HTTP客户端(400),
             ):
            for _ in range(3):
                try:
                    asyncio.run(本地精排客户端.LocalQwen3Reranker().rerank("查询", ["超长证据"]))
                except 本地精排客户端.本地精排请求被拒绝:
                    pass
                else:
                    raise AssertionError("输入超限没有退回基础排序")
        assert 本地精排客户端._熔断状态()[0] is False
        assert 本地精排客户端._连续故障 == 0
        process.terminate.assert_not_called()
    finally:
        with 本地精排客户端._故障锁:
            (
                本地精排客户端._连续故障,
                本地精排客户端._熔断到,
                本地精排客户端._最后错误,
            ) = old


def test_本地精排客户端_推理超时只重启自有服务():
    assert 统一搜索._精排总等待秒 >= (
        本地精排客户端.启动等待秒 + 本地精排客户端.请求等待秒 + 2
    )
    process = Mock()
    process.poll.return_value = None
    caller_thread = threading.get_ident()
    stop_threads: list[int] = []
    real_stop = 本地精排客户端.停止服务

    def stop_in_worker():
        stop_threads.append(threading.get_ident())
        real_stop()

    with patch.object(本地精排客户端, "_服务进程", process), \
         patch.object(本地精排客户端, "_确保服务同步"), \
         patch.object(本地精排客户端, "停止服务", side_effect=stop_in_worker), \
         patch.object(
             本地精排客户端.httpx, "AsyncClient",
             return_value=_假HTTP客户端(504),
         ):
        try:
            asyncio.run(本地精排客户端.LocalQwen3Reranker().rerank("查询", ["证据"]))
        except 本地精排客户端.本地精排不可用:
            pass
        else:
            raise AssertionError("推理超时没有退回基础排序")
        process.terminate.assert_called_once()
        assert 本地精排客户端._服务进程 is None
        assert stop_threads and stop_threads[0] != caller_thread


def test_本地精排服务_内存越界会停止接新请求():
    async def run() -> None:
        token = "xjrt_" + "x" * 48
        本地精排服务.app.state.queue = SimpleNamespace(
            _closing=False,
            snapshot=lambda: {"处理中": False, "排队数": 0},
        )
        response = Response()
        overloaded = {
            "状态": "可用",
            "内存": {"活动字节": 本地精排.默认内存上限字节 + 1, "缓存字节": 0, "峰值字节": 0},
        }
        with patch.dict(os.environ, {"XJ_RERANK_TOKEN": token}), \
             patch.object(本地精排服务, "完整性", return_value=(True, "完整")), \
             patch.object(本地精排服务, "状态", return_value=overloaded):
            payload = await 本地精排服务.healthz(response, "Bearer " + token)
            assert response.status_code == 503 and payload["ok"] is False
            try:
                await 本地精排服务.rerank(
                    {"query": "查询", "documents": ["证据"]}, "Bearer " + token,
                )
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 503
            else:
                raise AssertionError("内存越界后仍接受了新请求")

    asyncio.run(run())


def test_告警表达_队列满时直接保留规则版而不无限排队():
    old_state = dict(告警表达._状态)
    old_processing = dict(告警表达._处理中)
    try:
        with 告警表达._状态锁:
            告警表达._状态["状态"] = "可用"
            告警表达._处理中.clear()
        for index in range(告警表达._任务队列.maxsize):
            future = cf.Future()
            告警表达._任务队列.put_nowait((f"占位-{index}", {}, future))
        future, fingerprint = 告警表达._提交({"指纹": "额外告警"})
        assert future is None and fingerprint == "额外告警"
        assert 告警表达._任务队列.qsize() == 告警表达._任务队列.maxsize
        assert "额外告警" not in 告警表达._处理中
    finally:
        while True:
            try:
                告警表达._任务队列.get_nowait()
            except queue.Empty:
                break
        with 告警表达._状态锁:
            告警表达._状态.clear()
            告警表达._状态.update(old_state)
            告警表达._处理中.clear()
            告警表达._处理中.update(old_processing)


def test_告警表达_缓存只保留最近256条():
    with 告警表达._状态锁:
        old_cache = 告警表达._缓存.copy()
        try:
            告警表达._缓存.clear()
            for index in range(告警表达._缓存上限 + 1):
                key = f"告警-{index}"
                告警表达._写缓存_已锁(key, {"详情": key})
            assert len(告警表达._缓存) == 告警表达._缓存上限
            assert "告警-0" not in 告警表达._缓存
            assert f"告警-{告警表达._缓存上限}" in 告警表达._缓存
        finally:
            告警表达._缓存.clear()
            告警表达._缓存.update(old_cache)


def test_每轮统一搜索最多两次且版本问题不调用搜索():
    async def run() -> None:
        fake_result = SimpleNamespace(status="ok", hits=[], source_status={})
        token = 工具间.开始统一搜索轮(2)
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = root / "active.json"
            store = root / "store"
            manifest = store / "published" / "twilight-v1.0-beta" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            lock.write_text('{"schema":3,"version":"twilight-v1.0-beta"}', encoding="utf-8")
            manifest.write_text(
                '{"version":"twilight-v1.0-beta","version_name":"暮光v1.0beta版"}', encoding="utf-8",
            )
            try:
                with patch.dict(os.environ, {"XJ_RELEASE_LOCK": str(lock), "XJ_RELEASE_STORE": str(store)}), \
                     patch("统一搜索.search", new=AsyncMock(return_value=fake_result)) as search, \
                     patch("统一搜索.render_result", return_value="没有结果"), \
                     patch.object(工具间, "_留痕"):
                    await 工具间.统一查("第一次")
                    await 工具间.统一查("第二次")
                    third = await 工具间.统一查("第三次")
                    os.environ.pop("XJ_COMPANY_VERSION", None)
                    os.environ.pop("XJ_ACTIVE_VERSION", None)
                    development = await 工具间.查运行版本()
                    os.environ["XJ_ACTIVE_VERSION"] = "twilight-v1.0-beta"
                    formal = await 工具间.查运行版本()
                assert search.await_count == 2
                assert "本轮已经完成 2 次统一搜索" in third.content[0].text
                assert "未命名开发版" in development.content[0].text
                assert "暮光v1.0beta版" in development.content[0].text
                assert "已经发布的正式版本" in formal.content[0].text
                assert "未命名开发版" not in formal.content[0].text
                assert "暮光v1.0beta版" in formal.content[0].text
            finally:
                os.environ.pop("XJ_COMPANY_VERSION", None)
                os.environ.pop("XJ_ACTIVE_VERSION", None)
                工具间.结束统一搜索轮(token)

    asyncio.run(run())

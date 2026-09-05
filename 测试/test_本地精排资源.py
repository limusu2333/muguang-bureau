from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import mlx.core as mx
from fastapi import HTTPException, Response

from 多用户.控制面 import 本地精排, 本地精排服务


class _禁止推理:
    model_id = "fake-local-reranker"

    async def rerank(self, _query, _documents):
        raise AssertionError("轻量健康检查不应调用推理")


class 本地精排资源测试(unittest.TestCase):
    def test_MLX首次并发导入时内存检查不报500(self):
        partial_mx = SimpleNamespace(get_cache_memory=lambda: 0)
        with patch.dict(sys.modules, {"mlx.core": partial_mx}):
            self.assertEqual(
                本地精排.内存状态(),
                {"active_bytes": 0, "cache_bytes": 0, "peak_bytes": 0},
            )

    def test_限制候选长度批大小和每批令牌(self):
        self.assertEqual(本地精排.最大文档数, 16)
        self.assertEqual(本地精排.最大长度, 2048)
        self.assertEqual(本地精排.最大请求令牌, 24_576)
        self.assertEqual(本地精排.缓存上限字节, 256 * 1024 * 1024)
        self.assertEqual(本地精排.内存指导上限字节, 8 * 1024 * 1024 * 1024)
        with patch.dict("os.environ", {"XJ_RERANK_BATCH_SIZE": "99"}):
            self.assertEqual(本地精排.批大小(), 2)
        encoded = [[1] * 2048, [2] * 100, [3] * 2000, [4] * 101]
        batches = 本地精排._分批(encoded)
        self.assertEqual([len(batch) for batch in batches], [2, 2])
        self.assertTrue(all(
            max(len(item) for _index, item in batch) * len(batch) <= 4096
            for batch in batches
        ))
        restored = [0] * len(encoded)
        for batch in batches:
            for index, item in batch:
                restored[index] = len(item)
        self.assertEqual(restored, list(map(len, encoded)))

    def test_加载前设置缓存和内存指导上限(self):
        class Tokenizer:
            @staticmethod
            def encode(text, add_special_tokens=False):
                return [1]

        old_model, old_tokenizer = 本地精排._模型, 本地精排._分词器
        本地精排._模型 = None
        本地精排._分词器 = None
        try:
            with (
                patch.object(本地精排, "完整性", return_value=(True, "完整")),
                patch("mlx_lm.load", return_value=(object(), Tokenizer())),
                patch.object(mx, "set_cache_limit") as cache_limit,
                patch.object(mx, "set_memory_limit") as memory_limit,
            ):
                本地精排._加载()
            cache_limit.assert_called_once_with(256 * 1024 * 1024)
            memory_limit.assert_called_once_with(8 * 1024 * 1024 * 1024)
        finally:
            本地精排._模型 = old_model
            本地精排._分词器 = old_tokenizer

    def test_只投影最后隐藏态而不调用完整模型输出(self):
        hidden = mx.array([
            [[1.0, 1.0], [0.0, 4.0], [9.0, 9.0]],
            [[1.0, 1.0], [1.0, 1.0], [4.0, 0.0]],
        ])

        class Backbone:
            def __call__(self, _inputs):
                return hidden

        class Head:
            weight = mx.array([[1.0, 0.0], [0.0, 1.0], [9.0, 9.0]])

        class Model:
            args = SimpleNamespace(tie_word_embeddings=False)
            model = Backbone()
            lm_head = Head()

            def __call__(self, _inputs):
                raise AssertionError("不得生成整段完整词表 logits")

        model = Model()
        probabilities = 本地精排._最后位置概率(
            model, mx.array([[1, 2, 0], [1, 2, 3]]), [2, 3], 0, 1, mx,
        )
        self.assertGreater(float(probabilities[0]), 0.9)
        self.assertLess(float(probabilities[1]), 0.1)

    def test_量化输出只把no_yes两行交给矩阵乘法(self):
        weight = mx.zeros((6, 32))
        quant_weight, quant_scales, *quant_biases = mx.quantize(
            weight, group_size=32, bits=8, mode="mxfp8",
        )

        class QuantizedLayer:
            group_size = 32
            bits = 8
            mode = "mxfp8"
            biases = quant_biases[0] if quant_biases else None
            weight = quant_weight
            scales = quant_scales

            def __getitem__(self, name):
                return getattr(self, name)

        class Backbone:
            embed_tokens = QuantizedLayer()

            def __call__(self, _inputs):
                return mx.ones((1, 1, 32))

        class Model:
            args = SimpleNamespace(tie_word_embeddings=True)
            model = Backbone()

        original = mx.quantized_matmul

        def checked(hidden, selected_weight, **kwargs):
            self.assertEqual(selected_weight.shape[0], 2)
            self.assertEqual(kwargs["scales"].shape[0], 2)
            return original(hidden, selected_weight, **kwargs)

        with patch.object(mx, "quantized_matmul", side_effect=checked) as operation:
            result = 本地精排._最后位置概率(
                Model(), mx.array([[1]]), [1], 2, 5, mx,
            )
        self.assertEqual(result.shape, (1,))
        operation.assert_called_once()

    def test_长度分桶后恢复原顺序并记录资源(self):
        class Tokenizer:
            pad_token_id = 0
            eos_token_id = 0

            @staticmethod
            def encode(text, add_special_tokens=False):
                return [1] if text == "yes" else [0]

        def encode(_tokenizer, _query, document):
            value = int(document)
            return [value] * value

        def score(_model, inputs, _lengths, _no, _yes, _mx):
            return mx.array([float(row[0]) for row in inputs.tolist()])

        before = 本地精排.状态()["调用次数"]
        with (
            patch.object(本地精排, "_加载分词器", return_value=Tokenizer()),
            patch.object(本地精排, "_加载", return_value=(object(), Tokenizer())),
            patch.object(本地精排, "_编码", side_effect=encode),
            patch.object(本地精排, "_最后位置概率", side_effect=score),
            patch.object(本地精排, "内存状态", return_value={"active_bytes": 11, "cache_bytes": 22, "peak_bytes": 33}),
            patch.object(mx, "clear_cache") as clear,
        ):
            scores = 本地精排._打分同步("q", ["1", "4", "2", "3"])
        self.assertEqual(scores, [1.0, 4.0, 2.0, 3.0])
        state = 本地精排.状态()
        self.assertEqual(state["最近总令牌"], 10)
        self.assertEqual((state["active_bytes"], state["cache_bytes"], state["peak_bytes"]), (11, 22, 33))
        self.assertEqual(state["调用次数"], before + 1)
        clear.assert_called_once_with()

    def test_单请求超过24576_token会拒绝并记录失败(self):
        class Tokenizer:
            pad_token_id = 0
            eos_token_id = 0

            @staticmethod
            def encode(text, add_special_tokens=False):
                return [1] if text == "yes" else [0]

        before = 本地精排.状态()["失败次数"]
        model_load = Mock(side_effect=AssertionError("超限后不应加载4B模型"))
        with (
            patch.object(本地精排, "_加载分词器", return_value=Tokenizer()),
            patch.object(本地精排, "_加载", model_load),
            patch.object(本地精排, "_编码", return_value=[1] * 2048),
        ):
            with self.assertRaises(本地精排.请求超限):
                本地精排._打分同步("q", ["d"] * 13)
        state = 本地精排.状态()
        self.assertEqual(state["最近总令牌"], 26_624)
        self.assertEqual(state["失败次数"], before + 1)
        self.assertIn("请求超限", state["最近失败"])
        model_load.assert_not_called()

    def test_服务把总token超限明确返回400(self):
        async def run():
            token = "xjrt_" + "x" * 48
            with (
                patch.dict("os.environ", {"XJ_RERANK_TOKEN": token}),
                patch.object(
                    本地精排服务,
                    "_提交",
                    AsyncMock(side_effect=本地精排.请求超限("超过 24576 token")),
                ),
            ):
                with self.assertRaises(HTTPException) as caught:
                    await 本地精排服务.rerank(
                        {"query": "q", "documents": ["d"]}, "Bearer " + token,
                    )
            self.assertEqual(caught.exception.status_code, 400)

        asyncio.run(run())

    def test_healthz只检查文件和状态不加载模型(self):
        async def run():
            token = "xjrt_" + "x" * 48
            本地精排服务.app.state.reranker = _禁止推理()
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.processing = False
            本地精排服务.app.state.closing = False
            response = Response()
            with (
                patch.dict("os.environ", {"XJ_RERANK_TOKEN": token}),
                patch.object(本地精排服务, "完整性", return_value=(True, "完整")),
                patch.object(本地精排服务, "状态", return_value={"状态": "未加载", "可用": False}),
                patch.object(本地精排服务, "内存状态", return_value={"active_bytes": 0, "cache_bytes": 0, "peak_bytes": 0}),
            ):
                payload = await 本地精排服务.healthz(response, "Bearer " + token)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["state"]["状态"], "未加载")

        asyncio.run(run())

    def test_活动内存超过8GB时健康失败并拒绝新请求(self):
        async def run():
            token = "xjrt_" + "x" * 48
            本地精排服务.app.state.reranker = _禁止推理()
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.processing = False
            本地精排服务.app.state.closing = False
            response = Response()
            memory = {
                "active_bytes": 本地精排.内存指导上限字节 + 1,
                "cache_bytes": 0,
                "peak_bytes": 本地精排.内存指导上限字节 + 1,
            }
            with (
                patch.dict("os.environ", {"XJ_RERANK_TOKEN": token}),
                patch.object(本地精排服务, "完整性", return_value=(True, "完整")),
                patch.object(本地精排服务, "内存状态", return_value=memory),
            ):
                payload = await 本地精排服务.healthz(response, "Bearer " + token)
                self.assertEqual(response.status_code, 503)
                self.assertFalse(payload["ok"])
                with self.assertRaises(HTTPException) as caught:
                    await 本地精排服务.rerank(
                        {"query": "q", "documents": ["d"]}, "Bearer " + token,
                    )
                self.assertEqual(caught.exception.status_code, 503)

        asyncio.run(run())

    def test_等待队列满返回429(self):
        async def run():
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.closing = False
            loop = asyncio.get_running_loop()
            futures = []
            for _ in range(3):
                future = loop.create_future()
                futures.append(future)
                本地精排服务.app.state.queue.put_nowait(本地精排服务._任务("q", ["d"], future))
            with self.assertRaises(HTTPException) as caught:
                await 本地精排服务._提交("q", ["d"])
            self.assertEqual(caught.exception.status_code, 429)
            for future in futures:
                future.cancel()

        asyncio.run(run())

    def test_等待超过限制返回504(self):
        async def run():
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.closing = False
            本地精排服务.app.state.processing = False
            with patch.object(本地精排服务, "_请求等待秒", 0.01):
                with self.assertRaises(HTTPException) as caught:
                    await 本地精排服务._提交("q", ["d"])
            self.assertEqual(caught.exception.status_code, 504)
            queued = 本地精排服务.app.state.queue.get_nowait()
            self.assertTrue(queued.future.cancelled())
            本地精排服务.app.state.queue.task_done()

        asyncio.run(run())

    def test_工作者卡死超时会要求独立精排进程自恢复(self):
        async def run():
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.closing = False
            本地精排服务.app.state.processing = True
            with (
                patch.object(本地精排服务, "_请求等待秒", 0.01),
                patch.object(本地精排服务, "_请求进程重启") as restart,
            ):
                with self.assertRaises(HTTPException) as caught:
                    await 本地精排服务._提交("q", ["d"])
            self.assertEqual(caught.exception.status_code, 504)
            restart.assert_called_once_with()
            queued = 本地精排服务.app.state.queue.get_nowait()
            self.assertTrue(queued.future.cancelled())
            本地精排服务.app.state.queue.task_done()

        asyncio.run(run())

    def test_调用方取消时同步取消排队任务(self):
        async def run():
            本地精排服务.app.state.queue = asyncio.Queue(maxsize=3)
            本地精排服务.app.state.closing = False
            request = asyncio.create_task(本地精排服务._提交("q", ["d"]))
            await asyncio.sleep(0)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            queued = 本地精排服务.app.state.queue.get_nowait()
            self.assertTrue(queued.future.cancelled())
            本地精排服务.app.state.queue.task_done()

        asyncio.run(run())

    def test_空闲180秒释放模型和缓存(self):
        async def run():
            app = SimpleNamespace(state=SimpleNamespace(
                queue=asyncio.Queue(maxsize=3), processing=False, last_activity=0.0,
            ))
            with (
                patch.object(本地精排服务, "状态", return_value={"可用": True}),
                patch.object(本地精排服务, "卸载", return_value=True) as release,
            ):
                changed = await 本地精排服务._检查空闲(app, now=180.0)
            self.assertTrue(changed)
            release.assert_called_once_with()

        asyncio.run(run())

    def test_lifespan关闭worker并卸载(self):
        async def run():
            token = "xjrt_" + "x" * 48
            with (
                patch.dict("os.environ", {"XJ_RERANK_TOKEN": token}),
                patch.object(本地精排服务, "默认精排器", return_value=_禁止推理()),
                patch.object(本地精排服务, "卸载", return_value=False) as release,
            ):
                async with 本地精排服务.lifespan(本地精排服务.app):
                    worker = 本地精排服务.app.state.worker_task
                    idle = 本地精排服务.app.state.idle_task
                    self.assertFalse(worker.done())
                self.assertTrue(worker.done())
                self.assertTrue(idle.done())
                release.assert_called_once_with()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)

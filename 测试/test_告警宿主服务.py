from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import Response


根 = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if 根 not in sys.path:
    sys.path.insert(0, 根)

from 多用户.控制面 import 本地精排服务  # noqa: E402


内存上限 = 本地精排服务.内存指导上限字节


class _表达器:
    @staticmethod
    def 完整性():
        return True, "完整"

    @staticmethod
    def 状态():
        return {"状态": "未启动", "模型": "Qwen3.5-4B-4bit", "可用": False}

    @staticmethod
    def 呈现(alerts, *, 首项等待秒):
        return [{**item, "详情": "宿主统一整理", "表达来源": "宿主本地模型"} for item in alerts]


class 告警宿主服务测试(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old = os.environ.get("XJ_RERANK_TOKEN")
        os.environ["XJ_RERANK_TOKEN"] = "r" * 40
        本地精排服务.app.state.alert_slots = asyncio.Semaphore(8)
        本地精排服务.app.state.closing = False

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("XJ_RERANK_TOKEN", None)
        else:
            os.environ["XJ_RERANK_TOKEN"] = self.old

    async def test_告警接口只在内部令牌正确时工作(self):
        with patch.object(本地精排服务, "_告警模块", return_value=_表达器):
            result = await 本地精排服务.alert_render(
                {"alerts": [{"指纹": "a", "详情": "规则"}]},
                "Bearer " + "r" * 40,
            )
        self.assertEqual(result["alerts"][0]["详情"], "宿主统一整理")
        with self.assertRaisesRegex(Exception, "未授权"):
            await 本地精排服务.alert_render(
                {"alerts": [{"指纹": "a"}]}, "Bearer bad",
            )

    async def test_告警请求过多时拒绝而不是无限排队(self):
        本地精排服务.app.state.alert_slots = asyncio.Semaphore(0)
        with self.assertRaisesRegex(Exception, "正在忙"):
            await 本地精排服务.alert_render(
                {"alerts": [{"指纹": "a"}]},
                "Bearer " + "r" * 40,
            )

    async def test_告警健康在共享精排内存超限时不报绿(self):
        overloaded = type(
            "过载表达器",
            (),
            {
                "完整性": staticmethod(lambda: (True, "完整")),
                "状态": staticmethod(lambda: {"状态": "可用", "内存": {"活动字节": 内存上限 + 1}}),
            },
        )
        response = Response()
        with patch.object(本地精排服务, "_告警模块", return_value=overloaded):
            result = await 本地精排服务.alert_healthz(response, "Bearer " + "r" * 40)
        self.assertFalse(result["ok"])
        self.assertEqual(response.status_code, 503)

if __name__ == "__main__":
    unittest.main()

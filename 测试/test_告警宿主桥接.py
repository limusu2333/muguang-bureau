from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


工具根 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "工具"))
if 工具根 not in sys.path:
    sys.path.insert(0, 工具根)

import 告警表达  # noqa: E402
import 机房  # noqa: E402


class _响应:
    status = 200

    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


class 告警宿主桥接测试(unittest.TestCase):
    def setUp(self) -> None:
        self.alert = {"指纹": "alert-1", "详情": "规则事实"}

    def test_强制宿主但地址不可达时只保留规则版(self):
        with patch.dict(
            os.environ,
            {
                "XJ_ALERT_RENDER_REQUIRED": "1",
                "XJ_ALERT_RENDER_URL": "http://127.0.0.1:37657",
                "XJ_ALERT_RENDER_TOKEN": "t" * 40,
            },
            clear=False,
        ), patch("告警表达.urllib.request.urlopen", side_effect=OSError("down")), patch.object(
            告警表达, "启动并预热", side_effect=AssertionError("不能回到本地模型")
        ):
            rendered = 告警表达.呈现([self.alert], 首项等待秒=0)
        self.assertEqual(rendered[0]["详情"], "规则事实")
        self.assertEqual(rendered[0]["表达来源"], "规则")

    def test_宿主返回的人话会覆盖对应告警(self):
        response = _响应({"alerts": [{"详情": "宿主整理后的事实", "表达来源": "本地模型"}]})
        with patch.dict(
            os.environ,
            {
                "XJ_ALERT_RENDER_REQUIRED": "1",
                "XJ_ALERT_RENDER_URL": "http://127.0.0.1:37657",
                "XJ_ALERT_RENDER_TOKEN": "t" * 40,
            },
            clear=False,
        ), patch("告警表达.urllib.request.urlopen", return_value=response):
            rendered = 告警表达.呈现([self.alert], 首项等待秒=0)
        self.assertEqual(rendered[0]["详情"], "宿主整理后的事实")
        self.assertEqual(rendered[0]["表达来源"], "宿主本地模型")

    def test_宿主资源闸门返回规则卡时不冒充模型成功(self):
        response = _响应({"alerts": [{"详情": "规则事实"}]})
        with patch.dict(
            os.environ,
            {
                "XJ_ALERT_RENDER_REQUIRED": "1",
                "XJ_ALERT_RENDER_URL": "http://127.0.0.1:37657",
                "XJ_ALERT_RENDER_TOKEN": "t" * 40,
            },
            clear=False,
        ), patch("告警表达.urllib.request.urlopen", return_value=response):
            rendered = 告警表达.呈现([self.alert], 首项等待秒=0)
        self.assertEqual(rendered[0]["表达来源"], "规则")

    def test_正常办公室入口强制宿主边界(self):
        old_required = os.environ.get("XJ_ALERT_RENDER_REQUIRED")
        old_url = os.environ.get("XJ_ALERT_RENDER_URL")
        old_token = os.environ.get("XJ_ALERT_RENDER_TOKEN")
        try:
            os.environ["XJ_ALERT_RENDER_REQUIRED"] = "0"
            os.environ.pop("XJ_ALERT_RENDER_TOKEN", None)
            with patch.dict(os.environ, {"XJ_MOCK": "0", "XJ_MODE": ""}, clear=False), patch(
                "机房.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=("k" * 40) + "\n"),
            ):
                机房._设置告警宿主边界()
                self.assertEqual(os.environ.get("XJ_ALERT_RENDER_REQUIRED"), "1")
                self.assertEqual(os.environ.get("XJ_ALERT_RENDER_URL"), "http://127.0.0.1:37657")
                self.assertEqual(os.environ.get("XJ_ALERT_RENDER_TOKEN"), "k" * 40)
            os.environ["XJ_ALERT_RENDER_REQUIRED"] = "0"
            with patch.dict(os.environ, {"XJ_MOCK": "0", "XJ_MODE": "remote"}, clear=False), patch(
                "机房.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="k" * 40),
            ):
                机房._设置告警宿主边界()
                self.assertEqual(os.environ.get("XJ_ALERT_RENDER_REQUIRED"), "1")
        finally:
            if old_required is None:
                os.environ.pop("XJ_ALERT_RENDER_REQUIRED", None)
            else:
                os.environ["XJ_ALERT_RENDER_REQUIRED"] = old_required
            if old_url is None:
                os.environ.pop("XJ_ALERT_RENDER_URL", None)
            else:
                os.environ["XJ_ALERT_RENDER_URL"] = old_url
            if old_token is None:
                os.environ.pop("XJ_ALERT_RENDER_TOKEN", None)
            else:
                os.environ["XJ_ALERT_RENDER_TOKEN"] = old_token


if __name__ == "__main__":
    unittest.main()

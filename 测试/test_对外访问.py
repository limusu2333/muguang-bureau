from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from 工具 import 对外访问


class 对外访问启动测试(unittest.TestCase):
    def test_Sunny令牌文件使用客户端要求的JSON格式(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td)
            token_file = runtime / "client-token"
            with (
                patch.object(对外访问, "SUNNY_RUNTIME", runtime),
                patch.object(对外访问, "SUNNY_TOKEN_FILE", token_file),
                patch.object(
                    对外访问,
                    "_运行",
                    return_value=SimpleNamespace(returncode=0, stdout="sunny-client-token\n", stderr=""),
                ),
            ):
                对外访问._写Sunny令牌()

            self.assertEqual(json.loads(token_file.read_text(encoding="utf-8")), {"token": "sunny-client-token"})
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)

    def test_本机入口在线也会轻量核对运行版本(self):
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            python = root / ".venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            with (
                patch.object(对外访问, "PLATFORM_ROOT", root),
                patch.object(对外访问, "PLATFORM_PYTHON", python),
                patch.object(对外访问, "_网址可用", return_value=True),
                patch.object(对外访问, "_公网提供方", return_value="tailscale"),
                patch.object(对外访问, "_运行", side_effect=run),
            ):
                对外访问._确保用户公司()

        self.assertEqual(commands[0][-1], "runtime-current")
        self.assertFalse(any("start" in command for command in commands))

    def test_本机入口在线但版本过时时重新启动平台(self):
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=2 if command[-1] == "runtime-current" else 0,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            python = root / ".venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            with (
                patch.object(对外访问, "PLATFORM_ROOT", root),
                patch.object(对外访问, "PLATFORM_PYTHON", python),
                patch.object(对外访问, "_网址可用", side_effect=(True, True)),
                patch.object(对外访问, "_公网提供方", return_value="tailscale"),
                patch.object(对外访问, "_运行", side_effect=run),
            ):
                对外访问._确保用户公司()

        self.assertEqual(commands[0][-1], "runtime-current")
        self.assertIn("start", commands[1])

    def test_启动正式用户公司沿用Sunny公网提供方(self):
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=2 if command[-1] == "runtime-current" else 0,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            python = root / ".venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            with (
                patch.object(对外访问, "PLATFORM_ROOT", root),
                patch.object(对外访问, "PLATFORM_PYTHON", python),
                patch.object(对外访问, "_网址可用", side_effect=(True, True)),
                patch.object(对外访问, "_公网提供方", return_value="sunny"),
                patch.object(对外访问, "_运行", side_effect=run),
            ):
                对外访问._确保用户公司()

        self.assertEqual(commands[1][-2:], ["--provider", "sunny"])


class 对外访问进度测试(unittest.TestCase):
    def tearDown(self):
        对外访问._设置操作("")

    def test_操作状态返回阶段步骤和已用时间(self):
        with (
            patch.object(对外访问, "_公网网址", return_value="https://example.ts.net"),
            patch.object(对外访问, "_网址可用") as local_check,
            patch.object(对外访问, "_公网可用") as public_check,
        ):
            对外访问._设置操作(
                "正在开启",
                阶段="用户公司",
                说明="正在启动或核对用户公司…",
                步骤=("准备检查", "容器服务", "用户公司", "网络连接"),
                步骤索引=2,
            )
            value = 对外访问.状态()

        self.assertTrue(value["操作中"])
        self.assertEqual(value["操作阶段"], "用户公司")
        self.assertEqual(value["操作步骤索引"], 2)
        self.assertEqual(value["操作步骤"][2], "用户公司")
        self.assertGreaterEqual(value["已用秒"], 0)
        local_check.assert_not_called()
        public_check.assert_not_called()

    def test_开启会按真实顺序更新进度阶段(self):
        stages: list[str] = []
        original_set = 对外访问._设置操作

        def record(value, **kwargs):
            if value and kwargs.get("阶段"):
                stages.append(kwargs["阶段"])
            original_set(value, **kwargs)

        with (
            patch.object(对外访问, "_设置操作", side_effect=record),
            patch.object(对外访问, "_公网网址", return_value="https://example.ts.net"),
            patch.object(对外访问, "_公网提供方", return_value="tailscale"),
            patch.object(对外访问, "_确保Docker"),
            patch.object(对外访问, "_确保用户公司"),
            patch.object(对外访问, "_确保Tailscale"),
            patch.object(对外访问, "_运行"),
            patch.object(对外访问, "_通道已开启", return_value=True),
            patch.object(对外访问, "_通道状态", return_value={}),
            patch.object(对外访问, "_公网可用", return_value=True),
            patch.object(对外访问, "状态", return_value={"ok": True}),
        ):
            value = 对外访问.开启()

        self.assertTrue(value["ok"])
        self.assertEqual(
            stages,
            ["准备检查", "容器服务", "用户公司", "网络连接", "公网通道", "外网验证"],
        )

    def test_SunnyNgrok开启只启动客户端(self):
        with (
            patch.object(对外访问, "_公网提供方", return_value="sunny"),
            patch.object(对外访问, "_公网网址", return_value="https://example.free.idcfengye.com"),
            patch.object(对外访问, "_确保Docker"),
            patch.object(对外访问, "_确保用户公司"),
            patch.object(对外访问, "_确保Sunny") as ensure_sunny,
            patch.object(对外访问, "_Sunny运行中", return_value=True),
            patch.object(对外访问, "_公网可用", return_value=True),
            patch.object(对外访问, "状态", return_value={"ok": True}),
        ):
            value = 对外访问.开启()

        self.assertTrue(value["ok"])
        ensure_sunny.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)

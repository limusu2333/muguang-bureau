from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from 多用户 import 版本身份
from 多用户.控制面 import 管理服务
from 多用户.网关 import 应用 as 网关应用


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "多用户" / "界面" / "static"


class 运行版本身份测试(unittest.TestCase):
    def test_正式身份同时核对进程活动锁和封存清单(self):
        version = "twilight-v1.2-beta"
        with (
            patch.object(版本身份, "当前已发布版本", return_value=version),
            patch.object(
                版本身份,
                "读取正式版本",
                return_value={"version": version, "version_name": "暮光v1.2beta版"},
            ) as read_release,
        ):
            result = 版本身份.读取运行版本身份(
                environ={"XJ_ACTIVE_VERSION": version},
            )

        self.assertEqual(result, {
            "mode": "formal",
            "version": version,
            "version_name": "暮光v1.2beta版",
            "display_name": "暮光v1.2beta版",
        })
        read_release.assert_called_once_with(version, verify=False)

    def test_正式进程和活动锁不一致时拒绝伪造显示(self):
        with patch.object(
            版本身份,
            "当前已发布版本",
            return_value="twilight-v1.1-beta",
        ):
            with self.assertRaisesRegex(版本身份.版本身份错误, "不一致"):
                版本身份.读取运行版本身份(
                    environ={"XJ_ACTIVE_VERSION": "twilight-v1.2-beta"},
                )

    def test_开发身份同时显示开发版和当前正式版真实名称(self):
        version = "twilight-v1.2-beta"
        with (
            patch.object(版本身份, "当前已发布版本", return_value=version),
            patch.object(
                版本身份,
                "读取正式版本",
                return_value={"version": version, "version_name": "暮光v1.2beta版"},
            ),
        ):
            result = 版本身份.读取运行版本身份(development=True, environ={})

        self.assertEqual(result, {
            "mode": "development",
            "version": "dev",
            "version_name": "开发版",
            "display_name": "开发版 · 当前正式版 暮光v1.2beta版",
            "base_version": version,
            "base_version_name": "暮光v1.2beta版",
        })


class 版本身份接口测试(unittest.IsolatedAsyncioTestCase):
    async def test_正式登录页接口透传控制面的真实身份(self):
        class 假控制客户端:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            async def call(self, action: str, payload: dict) -> dict:
                self.calls.append((action, payload))
                return {
                    "mode": "formal",
                    "version": "twilight-v1.2-beta",
                    "version_name": "暮光v1.2beta版",
                    "display_name": "暮光v1.2beta版",
                }

        control = 假控制客户端()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(control=control)))
        response = await 网关应用.runtime_context(request)

        self.assertEqual(control.calls, [("runtime-context", {})])
        self.assertEqual(json.loads(response.body)["version_name"], "暮光v1.2beta版")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_开发管理登录页接口只显示开发身份(self):
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(development_admin=True)),
        )
        with patch.object(
            管理服务,
            "读取运行版本身份",
            return_value={
                "mode": "development",
                "version": "dev",
                "version_name": "开发版",
                "display_name": "开发版 · 当前正式版 暮光v1.2beta版",
            },
        ) as read_identity:
            result = await 管理服务.runtime_identity(request)

        self.assertEqual(result["display_name"], "开发版 · 当前正式版 暮光v1.2beta版")
        read_identity.assert_called_once_with(development=True)


class 页面防回归测试(unittest.TestCase):
    def test_正式登录品牌由接口填写且不能写死任何暮光版本号(self):
        html = (STATIC / "login.html").read_text(encoding="utf-8")
        script = (STATIC / "auth.js").read_text(encoding="utf-8")

        self.assertIn('id="auth-version">版本读取中…</small>', html)
        self.assertIn("request('/auth/runtime-context'", script)
        self.assertIn("identity.version_name", script)
        self.assertIsNone(re.search(r"暮光\s*v\d", html + script, re.IGNORECASE))

    def test_管理登录品牌由运行模式填写且不能写死任何暮光版本号(self):
        html = (STATIC / "admin.html").read_text(encoding="utf-8")
        script = (STATIC / "admin.js").read_text(encoding="utf-8")
        identity = re.search(
            r'<div class="company-identity".*?</div>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(identity)
        self.assertIn('id="owner-version">版本读取中…</span>', html)
        self.assertIn("api('/admin/runtime-identity')", script)
        self.assertIn("runtimeContext?.version_identity", script)
        self.assertIsNone(re.search(r"暮光\s*v\d", identity.group() + script, re.IGNORECASE))

    def test_办公室右下角从各自运行身份接口读取版本名(self):
        app = (ROOT / "前端" / "src" / "App.tsx").read_text(encoding="utf-8")
        style = (ROOT / "前端" / "src" / "styles" / "runtime-version.css").read_text(encoding="utf-8")
        development = (ROOT / "前端" / "src" / "components" / "RuntimeVersionBadge.tsx").read_text(encoding="utf-8")
        formal = (ROOT / "多用户" / "部署" / "前端覆盖" / "RuntimeVersionBadge.tsx").read_text(encoding="utf-8")
        office = (ROOT / "工具" / "机房.py").read_text(encoding="utf-8")

        self.assertIn("<RuntimeVersionBadge />", app)
        self.assertIn("right:28px", style)
        self.assertIn("bottom:9px", style)
        self.assertIn("z-index:200", style)
        self.assertIn("fetch('/运行身份'", development)
        self.assertIn("fetch('/auth/runtime-context'", formal)
        self.assertIn("identity.display_name", development)
        self.assertIn("identity.display_name", formal)
        self.assertIn("identity.version_name", development)
        self.assertIn("identity.version_name", formal)
        self.assertIn("读取运行版本身份(development=True)", office)
        self.assertIsNone(re.search(r"暮光\s*v\d", development + formal, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()

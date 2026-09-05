from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from 多用户.网关 import 应用 as 网关应用
from 多用户.控制面 import 服务 as 控制服务
from 多用户.控制面.注册表 import 账户注册表
from 多用户.控制面.账户认证 import 认证错误, 账户认证


账户ID = "acc_" + "0" * 26


def _待激活账户(registry: 账户注册表) -> None:
    record = {
        "account_id": 账户ID,
        "email": "user@example.com",
        "display_name": "张三",
        "call_name": "张先生",
        "instance_id": "inst_" + "1" * 26,
        "owner_id": "own_" + "2" * 26,
        "upstream": f"http://company-{账户ID}:8000",
        "chat_credential_ref": f"keychain://xj-multiuser/{账户ID}:chat",
        "search_credential_ref": f"keychain://xj-multiuser/{账户ID}:search",
        "data_volume": f"data-{账户ID}",
        "work_volume": f"work-{账户ID}",
        "instance_config_path": f"/var/xj/instance/{账户ID}/instance.yaml",
        "image_ref": "company@test",
        "runner_image_ref": "runner@test",
    }
    registry.开始开通(record)
    registry.完成开通(record, control_token="x" * 32)


def _请求(body: dict, auth: 账户认证) -> Request:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "server": ("127.0.0.1", 37654), "client": ("127.0.0.1", 12345),
        "scheme": "http", "method": "POST", "root_path": "", "path": "/control/unified-login",
        "raw_path": b"/control/unified-login", "query_string": b"", "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(auth=auth)),
    }, receive)


class 用户登录系统测试(unittest.TestCase):
    def test_统一登录按账户自动分流(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            _待激活账户(registry)
            auth = 账户认证(registry.path)
            auth.使用邀请(auth.发邀请(账户ID)["token"], "用户自己的密码足够长")
            auth.设置管理员账户("示例主人", "管理员自己的密码足够长")

            user = asyncio.run(控制服务.unified_login(_请求({
                "account": "user@example.com", "password": "用户自己的密码足够长",
            }, auth)))
            self.assertEqual(user["destination"], "company")
            self.assertEqual(auth.解析会话(user["session_token"])["account_id"], 账户ID)

            owner = asyncio.run(控制服务.unified_login(_请求({
                "account": "示例主人", "password": "管理员自己的密码足够长",
            }, auth)))
            self.assertEqual(owner["destination"], "management")
            session = auth.使用管理员登录交接(owner["handoff_token"])
            auth.校验管理员(session["session_token"])

    def test_公告发布编辑撤下和重新发布(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            first = registry.发布公告("测试开始", "今晚开始第一轮测试。")
            self.assertEqual(first["status"], "已发布")
            self.assertEqual(registry.公告列表(only_published=True)[0]["body"], "今晚开始第一轮测试。")

            published_at = first["published_at"]
            edited = registry.编辑公告(
                first["announcement_id"],
                "测试安排更新",
                "客户端传入正文",
                body_rich="<p><strong>今晚九点</strong>开始。</p><script>bad()</script>",
            )
            self.assertEqual(edited["title"], "测试安排更新")
            self.assertEqual(edited["body"], "今晚九点开始。")
            self.assertNotIn("script", edited["body_rich"])
            self.assertEqual(edited["status"], "已发布")
            self.assertEqual(edited["published_at"], published_at)

            withdrawn = registry.设置公告状态(first["announcement_id"], "已撤下")
            self.assertEqual(withdrawn["status"], "已撤下")
            self.assertEqual(registry.公告列表(only_published=True), [])

            withdrawn_edited = registry.编辑公告(first["announcement_id"], "撤下后仍可编辑", "修订正文")
            self.assertEqual(withdrawn_edited["status"], "已撤下")

            republished = registry.设置公告状态(first["announcement_id"], "已发布")
            self.assertEqual(republished["status"], "已发布")
            actions = [row["action"] for row in registry.审计记录()]
            self.assertEqual(actions[:5], [
                "publish-announcement", "edit-announcement", "withdraw-announcement",
                "edit-announcement", "publish-announcement",
            ])

            with self.assertRaisesRegex(RuntimeError, "公告标题"):
                registry.发布公告("", "正文")

            second = registry.发布公告("第二条", "最新公告")
            ordered = registry.公告列表(only_published=True)
            self.assertEqual(ordered[0]["announcement_id"], second["announcement_id"])

    def test_公告富文本只保留允许的排版并兼容旧公告(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            rich = registry.发布公告(
                "更新公告 🎉",
                "客户端传入的纯文本不能覆盖真实排版内容",
                body_rich=(
                    '<h3>重点更新 ✨</h3><p><span style="font-weight:bold;color:rgb(180, 122, 47);'
                    'font-family:Iowan Old Style;font-size:large">搜索更可靠</span></p>'
                    '<ul><li>第一项</li><li>第二项<script>alert(1)</script></li></ul>'
                    '<img src=x onerror=alert(1)>'
                ),
            )
            self.assertIn("announcement-color-gold", rich["body_rich"])
            self.assertIn("announcement-font-serif", rich["body_rich"])
            self.assertIn("announcement-size-large", rich["body_rich"])
            self.assertIn("<strong>", rich["body_rich"])
            self.assertNotIn("script", rich["body_rich"])
            self.assertNotIn("onerror", rich["body_rich"])
            self.assertNotIn("客户端传入", rich["body"])
            self.assertIn("重点更新 ✨", rich["body"])
            self.assertIn("• 第一项", rich["body"])

            plain = registry.发布公告("旧公告", "原来的纯文字仍然照常显示。")
            self.assertEqual(plain["body_rich"], "")
            self.assertEqual(plain["body"], "原来的纯文字仍然照常显示。")

            with sqlite3.connect(registry.path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(announcements)")}
            self.assertIn("body_rich", columns)

        with tempfile.TemporaryDirectory() as td:
            legacy_path = Path(td) / "legacy.sqlite3"
            with sqlite3.connect(legacy_path) as conn:
                conn.execute(
                    """CREATE TABLE announcements (
                        announcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT NOT NULL
                    )"""
                )
                conn.execute(
                    "INSERT INTO announcements(title,body,status,created_at,updated_at,published_at) VALUES(?,?,?,?,?,?)",
                    ("旧库公告", "旧库正文", "已发布", "2026-01-01", "2026-01-01", "2026-01-01"),
                )
            migrated = 账户注册表(legacy_path).公告列表()[0]
            self.assertEqual(migrated["body"], "旧库正文")
            self.assertEqual(migrated["body_rich"], "")

    def test_反馈归属版本截图和状态由服务端管理(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            _待激活账户(registry)
            registry.置状态(账户ID, "在用", action="test-activate")
            screenshot = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode()

            item = registry.提交反馈(
                账户ID,
                "发现 Bug",
                "切换班子时按钮没有反应。",
                "/hall",
                screenshot,
            )
            self.assertEqual(item["account_id"], 账户ID)
            self.assertEqual(item["email"], "user@example.com")
            self.assertEqual(item["version"], "company@test")
            self.assertEqual(item["status"], "待处理")
            self.assertTrue(item["has_screenshot"])
            self.assertEqual(registry.反馈截图(item["feedback_id"]), ("image/png", b"\x89PNG\r\n\x1a\nmock"))

            updated = registry.设置反馈状态(item["feedback_id"], "处理中")
            self.assertEqual(updated["status"], "处理中")
            self.assertEqual(registry.反馈列表(status="处理中")[0]["body"], "切换班子时按钮没有反应。")
            actions = [row["action"] for row in registry.审计记录()]
            self.assertEqual(actions[:2], ["update-feedback-status", "submit-feedback"])

            with self.assertRaisesRegex(RuntimeError, "截图"):
                registry.提交反馈(账户ID, "其他", "无效截图", "/", "data:image/svg+xml;base64,AAAA")

    def test_公开反馈接口强制使用当前登录账户(self):
        class 假控制客户端:
            def __init__(self):
                self.feedback_payload = None

            async def call(self, action, payload):
                if action == "resolve-session":
                    return {
                        "account_id": 账户ID,
                        "email": "user@example.com",
                        "状态": "在用",
                        "session_expires_at": 9999999999,
                    }
                if action == "feedback":
                    self.feedback_payload = payload
                    return {"ok": True, "feedback_id": 1}
                raise AssertionError(action)

        supplied_other = "acc_" + "3" * 26
        payload = json.dumps({
            "account_id": supplied_other,
            "kind": "功能建议",
            "body": "希望增加快捷入口",
            "page": "/hall",
        }, ensure_ascii=False).encode("utf-8")
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}

        control = 假控制客户端()
        session_cookie = 网关应用._会话Cookie
        csrf_cookie = 网关应用._防伪Cookie
        request = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "server": ("company.example", 443), "client": ("127.0.0.1", 12345),
            "scheme": "https", "method": "POST", "root_path": "", "path": "/auth/feedback",
            "raw_path": b"/auth/feedback", "query_string": b"",
            "headers": [
                (b"cookie", f"{session_cookie}=session-token; {csrf_cookie}=csrf-token".encode()),
                (b"x-xj-csrf-token", b"csrf-token"),
                (b"content-length", str(len(payload)).encode()),
            ],
            "app": SimpleNamespace(state=SimpleNamespace(control=control)),
        }, receive)
        response = asyncio.run(网关应用.feedback(request))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(control.feedback_payload["account_id"], 账户ID)
        self.assertNotEqual(control.feedback_payload["account_id"], supplied_other)

    def test_数据库连接用完后立即关闭(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            auth = 账户认证(registry.path)

            with registry._连接() as registry_conn:
                self.assertEqual(registry_conn.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                registry_conn.execute("SELECT 1")

            with auth._连接() as auth_conn:
                self.assertEqual(auth_conn.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                auth_conn.execute("SELECT 1")

    def test_未登录打开根网址直接返回登录页(self):
        request = Request({
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "server": ("company.example", 443),
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "method": "GET",
            "root_path": "",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
        })
        response = asyncio.run(网关应用.root(request))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Path(response.path).name, "login.html")

    def test_邀请激活登录和停用会话(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            _待激活账户(registry)
            auth = 账户认证(registry.path)

            invitation = auth.发邀请(账户ID)
            self.assertEqual(auth.看邀请(invitation["token"])["display_name"], "张三")
            activated = auth.使用邀请(invitation["token"], "这是一个足够长的登录密码")
            self.assertEqual(activated["状态"], "在用")
            self.assertEqual(auth.解析会话(activated["session_token"])["account_id"], 账户ID)

            auth.撤销用户会话(账户ID)
            with self.assertRaisesRegex(认证错误, "登录已经失效"):
                auth.解析会话(activated["session_token"])
            logged_in = auth.登录("USER@example.com", "这是一个足够长的登录密码")
            self.assertEqual(logged_in["display_name"], "张三")

            handoff = auth.创建用户登录交接("user@example.com", "这是一个足够长的登录密码")
            handed_in = auth.使用用户登录交接(handoff["handoff_token"])
            self.assertEqual(auth.解析会话(handed_in["session_token"])["account_id"], 账户ID)
            with self.assertRaisesRegex(认证错误, "跳转已经失效"):
                auth.使用用户登录交接(handoff["handoff_token"])

            registry.置状态(账户ID, "停用", action="test-stop")
            with self.assertRaisesRegex(认证错误, "登录已经失效"):
                auth.解析会话(logged_in["session_token"])

    def test_重置密码会废掉旧密码和旧登录(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            _待激活账户(registry)
            auth = 账户认证(registry.path)
            activated = auth.使用邀请(auth.发邀请(账户ID)["token"], "原来的密码足够长123")

            reset = auth.发邀请(账户ID, "reset")
            with self.assertRaises(认证错误):
                auth.解析会话(activated["session_token"])
            with self.assertRaises(认证错误):
                auth.登录("user@example.com", "原来的密码足够长123")

            auth.使用邀请(reset["token"], "换成新的密码足够长456")
            self.assertEqual(auth.登录("user@example.com", "换成新的密码足够长456")["状态"], "在用")

    def test_管理员首次设置后才能登录和退出(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            auth = 账户认证(registry.path)
            self.assertFalse(auth.管理员是否已设置())
            auth.设置管理员账户("示例主人", "管理员自己的密码足够长")
            with self.assertRaisesRegex(认证错误, "账户或密码错误"):
                auth.管理员登录("其他人", "管理员自己的密码足够长")
            session = auth.管理员登录("示例主人", "管理员自己的密码足够长")
            auth.校验管理员(session["session_token"], session["csrf_token"])
            auth.管理员退出(session["session_token"], session["csrf_token"])
            with self.assertRaisesRegex(认证错误, "登录已经失效"):
                auth.校验管理员(session["session_token"])

            handoff = auth.创建管理员登录交接("示例主人", "管理员自己的密码足够长")
            handed_in = auth.使用管理员登录交接(handoff["handoff_token"])
            auth.校验管理员(handed_in["session_token"])
            with self.assertRaisesRegex(认证错误, "跳转已经失效"):
                auth.使用管理员登录交接(handoff["handoff_token"])

    def test_旧管理员密码数据自动补入现有账户名(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            auth = 账户认证(registry.path)
            auth.设置管理员账户("示例主人", "管理员自己的密码足够长")
            with auth._连接() as conn:
                conn.execute("ALTER TABLE owner_auth RENAME TO owner_auth_current")
                conn.execute(
                    "CREATE TABLE owner_auth(singleton INTEGER PRIMARY KEY, password_hash TEXT NOT NULL, "
                    "failed_login_count INTEGER NOT NULL DEFAULT 0, locked_until INTEGER, "
                    "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO owner_auth SELECT singleton,password_hash,failed_login_count,locked_until,created_at,updated_at "
                    "FROM owner_auth_current"
                )
                conn.execute("DROP TABLE owner_auth_current")

            migrated = 账户认证(registry.path)
            session = migrated.管理员登录("示例主人", "管理员自己的密码足够长")
            migrated.校验管理员(session["session_token"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

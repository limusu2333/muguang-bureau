from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response

from 多用户.控制面 import 管理服务


class _假请求:
    def __init__(self, app, body: dict | None = None) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self._body = body or {}

    async def body(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


class _发布器:
    def __init__(self, *, candidate_error: Exception | None = None, deployment_error: Exception | None = None):
        self.candidate_error = candidate_error
        self.deployment_error = deployment_error
        self.prepared_candidates: list[tuple[str, str]] = []
        self.executed_candidates: list[str] = []
        self.executed_deployments: list[str] = []
        self.log_ids: list[str] = []
        self.approved_candidates: list[str] = []

    def 准备候选(self, notes: str, source_head: str) -> dict:
        self.prepared_candidates.append((notes, source_head))
        return {
            "candidate_id": "candidate-test",
            "status": "排队中",
            "stage": "等待候选制造",
        }

    def 执行候选(self, candidate_id: str) -> None:
        self.executed_candidates.append(candidate_id)
        if self.candidate_error:
            raise self.candidate_error

    def 候选日志(self, candidate_id: str) -> dict:
        self.log_ids.append(candidate_id)
        return {"candidate_id": candidate_id, "lines": ["候选验收完成"]}

    def 执行部署(self, deployment_id: str) -> None:
        self.executed_deployments.append(deployment_id)
        if self.deployment_error:
            raise self.deployment_error

    def 批准候选宿主环境(self, candidate_id: str) -> dict:
        self.approved_candidates.append(candidate_id)
        return {"status": "ready", "candidate_id": candidate_id}


class _候选注册表:
    def __init__(self) -> None:
        self.updated: tuple[str, dict] | None = None

    def 候选记录(self, *, candidate_id: str) -> dict:
        return {
            "candidate_id": candidate_id,
            "status": "构建中",
            "details": {"timeline": [{"status": "构建中", "stage": "开始"}]},
        }

    def 更新候选记录(self, candidate_id: str, **values) -> None:
        self.updated = (candidate_id, values)


class _部署注册表:
    def __init__(self) -> None:
        self.updated: tuple[str, dict] | None = None

    def 部署记录(self, *, deployment_id: str) -> dict:
        return {
            "deployment_id": deployment_id,
            "version": "development",
            "status": "更新账号中",
            "target_count": 2,
            "success_count": 1,
            "failed_count": 0,
            "details": {"timeline": [{"status": "更新账号中", "stage": "开始"}]},
        }

    def 更新部署记录(self, deployment_id: str, **values) -> None:
        self.updated = (deployment_id, values)


def _应用(releases, registry=None, *, release_task=None, development_admin=True):
    return SimpleNamespace(state=SimpleNamespace(
        operation_lock=asyncio.Lock(),
        release_task=release_task,
        releases=releases,
        registry=registry or SimpleNamespace(要求没有发布维护=lambda: None),
        development_admin=development_admin,
    ))


class 管理发布接口测试(unittest.IsolatedAsyncioTestCase):
    async def test_管理页静态资源禁止浏览器继续使用旧缓存(self):
        request = SimpleNamespace(url=SimpleNamespace(path="/admin-ui/admin.js"))

        async def call_next(_request):
            return Response("", media_type="text/javascript")

        response = await 管理服务.本机响应头(request, call_next)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_公告编辑接口更新原记录(self):
        class _公告注册表:
            def __init__(self) -> None:
                self.call = None

            def 编辑公告(self, announcement_id, title, body, *, body_rich=""):
                self.call = (announcement_id, title, body, body_rich)
                return {"announcement_id": announcement_id, "title": title, "status": "已发布"}

        registry = _公告注册表()
        app = SimpleNamespace(state=SimpleNamespace(registry=registry))
        with patch.object(管理服务, "_管理员", return_value="owner"):
            result = await 管理服务.update_announcement(
                7,
                _假请求(app, {"title": "修订公告", "body": "正文", "body_rich": "<p>正文</p>"}),
            )
        self.assertEqual(registry.call, (7, "修订公告", "正文", "<p>正文</p>"))
        self.assertEqual(result["announcement"]["announcement_id"], 7)

    async def test_运行环境明确区分开发发布和正式维护(self):
        app = _应用(_发布器(), development_admin=True)
        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(
                管理服务,
                "读取运行版本身份",
                side_effect=lambda *, development: {
                    "mode": "development" if development else "formal",
                    "version": "dev" if development else "twilight-v1.2-beta",
                    "version_name": "开发版" if development else "暮光v1.2beta版",
                },
            ),
        ):
            development = await 管理服务.runtime_context(_假请求(app))
        self.assertEqual(development["mode"], "development")
        self.assertTrue(development["can_prepare_candidate"])
        self.assertTrue(development["can_publish"])
        self.assertTrue(development["can_rollback"])

        app = _应用(_发布器(), development_admin=False)
        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(
                管理服务,
                "读取运行版本身份",
                return_value={
                    "mode": "formal",
                    "version": "twilight-v1.2-beta",
                    "version_name": "暮光v1.2beta版",
                },
            ),
        ):
            formal = await 管理服务.runtime_context(_假请求(app))
        self.assertEqual(formal["mode"], "formal")
        self.assertFalse(formal["can_prepare_candidate"])
        self.assertFalse(formal["can_publish"])
        self.assertTrue(formal["can_rollback"])
        self.assertEqual(formal["version_identity"]["version_name"], "暮光v1.2beta版")

    async def test_开发管理后台未加载当前提交时禁止发布动作(self):
        releases = _发布器()
        app = _应用(releases)
        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(管理服务, "_加载时开发提交", "a" * 40),
            patch.object(管理服务, "_读取当前开发提交", return_value="b" * 40),
        ):
            with self.assertRaises(HTTPException) as raised:
                await 管理服务.start_release_candidate(
                    _假请求(app, {"notes": "x", "source_head": "b" * 40}),
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "development-admin-reload-required")
        self.assertEqual(releases.prepared_candidates, [])

    async def test_开发页重启一次同时请求正式后台并重载发布后台(self):
        app = _应用(_发布器())
        reloaded = asyncio.Event()

        async def reload_development_admin():
            reloaded.set()

        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(管理服务, "_后台重启预检"),
            patch.object(管理服务, "请求重启", return_value={"status": "排队中"}) as restart,
            patch.object(管理服务, "_稍后退出开发管理后台", side_effect=reload_development_admin),
        ):
            result = await 管理服务.system_restart(_假请求(app))
            await asyncio.wait_for(reloaded.wait(), timeout=1)
        self.assertEqual(result["restart"]["status"], "排队中")
        restart.assert_called_once_with()

    async def test_正式维护端拒绝生成候选和发布且不触碰发布器(self):
        releases = _发布器()
        app = _应用(releases, development_admin=False)
        with patch.object(管理服务, "_管理员", return_value="owner"):
            for route, body in (
                (管理服务.start_release_candidate, {"notes": "x", "source_head": "a"}),
                (管理服务.start_release, {"candidate_id": "candidate-test", "version_name": "测试版"}),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await route(_假请求(app, body))
                self.assertEqual(raised.exception.status_code, 409)
                self.assertIn("正式运行维护页", str(raised.exception.detail))
        self.assertEqual(releases.prepared_candidates, [])

    async def test_未登录不能启动候选(self):
        releases = _发布器()
        app = _应用(releases)
        with patch.object(管理服务, "_管理员", side_effect=HTTPException(401, "未登录")):
            with self.assertRaises(HTTPException) as raised:
                await 管理服务.start_release_candidate(
                    _假请求(app, {"notes": "x", "source_head": "a"}),
                )
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(releases.prepared_candidates, [])

    async def test_候选宿主环境审批需要明确确认词并只返回隔离环境(self):
        releases = _发布器()
        registry = SimpleNamespace(
            要求没有发布维护=lambda: None,
            候选记录=lambda *, candidate_id: {"candidate_id": candidate_id, "status": "待发布"},
        )
        app = _应用(releases, registry)
        with patch.object(管理服务, "_管理员", return_value="owner"):
            with self.assertRaises(HTTPException) as raised:
                await 管理服务.approve_release_host_runtime(
                    _假请求(app, {"candidate_id": "candidate-test", "confirm": "错"}),
                )
            self.assertEqual(raised.exception.status_code, 400)
            result = await 管理服务.approve_release_host_runtime(
                _假请求(app, {
                    "candidate_id": "candidate-test",
                    "confirm": "为当前候选建立隔离环境",
                }),
            )
        self.assertEqual(result["host_runtime"]["status"], "ready")
        self.assertEqual(releases.approved_candidates, ["candidate-test"])

    async def test_候选启动返回结构并执行后收口(self):
        releases = _发布器()
        app = _应用(releases)
        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(管理服务, "正在重启", return_value=False),
        ):
            result = await 管理服务.start_release_candidate(
                _假请求(app, {"notes": "本轮", "source_head": "a" * 40}),
            )
        self.assertEqual(result["candidate"]["candidate_id"], "candidate-test")
        self.assertEqual(releases.prepared_candidates, [("本轮", "a" * 40)])
        task = app.state.release_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(releases.executed_candidates, ["candidate-test"])
        self.assertIsNone(app.state.release_task)

    async def test_候选后台异常写入结构化失败和终态(self):
        releases = _发布器(candidate_error=RuntimeError("构建程序错误"))
        registry = _候选注册表()
        app = _应用(releases, registry)
        with (
            patch.object(管理服务, "_管理员", return_value="owner"),
            patch.object(管理服务, "正在重启", return_value=False),
        ):
            await 管理服务.start_release_candidate(
                _假请求(app, {"notes": "异常测试", "source_head": "b" * 40}),
            )
        await asyncio.wait_for(app.state.release_task, timeout=1)
        self.assertIsNotNone(registry.updated)
        candidate_id, values = registry.updated
        self.assertEqual(candidate_id, "candidate-test")
        self.assertEqual(values["status"], "失败")
        self.assertIn("后台候选任务异常停止", values["stage"])
        failure = values["details"]["failure"]
        self.assertEqual(failure["phase"], "release-system")
        self.assertEqual(failure["step"], "后台候选任务")
        self.assertEqual(failure["account_effect"], "未触碰任何账号；当前正式版本继续运行。")
        terminal = [item for item in values["details"]["timeline"] if item.get("status") == "失败"]
        self.assertEqual(len(terminal), 1)

    async def test_日志接口使用路由中的候选ID并禁止缓存(self):
        releases = _发布器()
        app = _应用(releases)
        with patch.object(管理服务, "_管理员", return_value="owner"):
            response = await 管理服务.release_candidate_log("candidate-precise", _假请求(app))
        self.assertEqual(releases.log_ids, ["candidate-precise"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response.body)["candidate_id"], "candidate-precise")

    async def test_发布回退和重启在后台任务运行时均互斥(self):
        releases = _发布器()
        registry = SimpleNamespace(要求没有发布维护=lambda: None)
        gate = asyncio.Event()

        async def hold():
            await gate.wait()

        running = asyncio.create_task(hold())
        await asyncio.sleep(0)
        app = _应用(releases, registry, release_task=running)
        requests = (
            (管理服务.start_release, {"candidate_id": "candidate-test", "version_name": "测试版"}),
            (管理服务.start_release_rollback, {"version": "old-version"}),
            (管理服务.system_restart, {}),
        )
        try:
            with (
                patch.object(管理服务, "_管理员", return_value="owner"),
                patch.object(管理服务, "正在重启", return_value=False),
                patch.object(管理服务, "_后台重启预检"),
                patch.object(管理服务, "请求重启") as restart,
            ):
                for route, body in requests:
                    with self.assertRaises(HTTPException) as raised:
                        await route(_假请求(app, body))
                    self.assertEqual(raised.exception.status_code, 409)
            restart.assert_not_called()
            self.assertEqual(releases.prepared_candidates, [])
        finally:
            gate.set()
            await running

    async def test_部署后台异常保留回退证据并写唯一失败终态(self):
        releases = _发布器(deployment_error=RuntimeError("部署超时"))
        registry = _部署注册表()
        app = _应用(releases, registry)
        await 管理服务._执行部署(app, "deployment-test")
        self.assertEqual(releases.executed_deployments, ["deployment-test"])
        self.assertIsNotNone(registry.updated)
        deployment_id, values = registry.updated
        self.assertEqual(deployment_id, "deployment-test")
        self.assertEqual(values["status"], "失败")
        self.assertIn("后台部署任务异常停止", values["stage"])
        details = values["details"]
        self.assertEqual(details["rollback_failures"]["release-system"], "后台任务异常中断，尚未完成运行状态核对")
        self.assertEqual(details["retained_unactivated_release"]["version"], "development")
        self.assertEqual(details["failure"]["retry_class"], "manual")
        terminal = [item for item in details["timeline"] if item.get("status") == "失败"]
        self.assertEqual(len(terminal), 1)

    async def test_正式管理后台会等发布事实齐全后再次收口(self):
        target_version = "twilight-release-target"
        target_source = "b" * 64

        class _收口发布器:
            def __init__(self):
                self.calls = 0

            def 收口宿主切换(self):
                self.calls += 1
                if self.calls == 1:
                    return {"status": "host-ready"}
                return {"status": "完成"}

            def 恢复启动状态(self):
                return {"status": "waiting"}

        releases = _收口发布器()
        app = SimpleNamespace(state=SimpleNamespace(
            releases=releases,
            release_task=asyncio.current_task(),
        ))
        switching_state = {
            "status": "switching",
            "target": {"version": target_version, "source_sha256": target_source},
        }
        host_ready_state = {**switching_state, "status": "host-ready"}
        with (
            patch.dict(管理服务.os.environ, {
                "XJ_HOST_ROLE": "admin",
                "XJ_ACTIVE_VERSION": target_version,
                "XJ_RELEASE_SOURCE_SHA256": target_source,
            }, clear=False),
            patch.object(
                管理服务,
                "读取宿主切换",
                side_effect=[switching_state, host_ready_state],
            ),
            patch.object(管理服务, "标记新进程已恢复", return_value={"status": "完成"}),
        ):
            await 管理服务._后台启动恢复(app)

        self.assertEqual(releases.calls, 2)
        self.assertIsNone(app.state.release_task)

    async def test_部署助手失败后开发后台会立即执行完整恢复(self):
        class _恢复发布器:
            def __init__(self):
                self.recovered = 0

            def 执行部署(self, deployment_id):
                return {"deployment_id": deployment_id, "status": "更新账号中"}

            def 恢复启动状态(self):
                self.recovered += 1
                return {"recovered_deployments": ["deployment-test"]}

            def 收口宿主切换(self):
                raise AssertionError("失败状态不应继续收口")

        releases = _恢复发布器()
        registry = SimpleNamespace(
            # The old formal admin may write the deployment failure first.  The
            # development admin must still finish the helper-side recovery.
            部署记录=lambda **kwargs: {"status": "失败"},
        )
        app = SimpleNamespace(state=SimpleNamespace(
            releases=releases,
            registry=registry,
            release_task=asyncio.current_task(),
        ))
        with patch.object(
            管理服务,
            "读取宿主切换",
            return_value={"status": "failed"},
        ):
            await 管理服务._执行部署(app, "deployment-test")

        self.assertEqual(releases.recovered, 1)
        self.assertIsNone(app.state.release_task)


if __name__ == "__main__":
    unittest.main(verbosity=2)

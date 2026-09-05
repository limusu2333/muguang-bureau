from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from 多用户.控制面 import 宿主服务重启, 管理服务
from 多用户.部署 import 宿主启动器, 监督服务命令


class _假请求:
    def __init__(self, app, body: dict | None = None) -> None:
        self.app = app
        self.cookies = {}
        self.headers = {}
        self._body = body or {}

    async def body(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


class 宿主服务重启测试(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name)
        self.status_file = self.runtime / "restart-status.json"
        self.patches = [
            patch.object(宿主服务重启, "RUNTIME", self.runtime),
            patch.object(宿主服务重启, "STATUS_FILE", self.status_file),
            patch.object(宿主服务重启, "LOCK_FILE", self.runtime / "restart.lock"),
            patch.object(宿主服务重启, "ACTIVE_LOCK", self.runtime / "active-release.json"),
        ]
        for item in self.patches:
            item.start()
        release = self.runtime / "published" / "test"
        source = release / "source"
        source.mkdir(parents=True)
        (source / "marker.txt").write_text("test\n", encoding="utf-8")
        from 多用户.部署 import 宿主启动器
        fingerprint = 宿主启动器._树指纹(source)
        (release / "manifest.json").write_text(json.dumps({
            "schema": 2, "version": "test", "source": fingerprint,
        }), encoding="utf-8")
        (self.runtime / "active-release.json").write_text(json.dumps({
            "schema": 3, "version": "test", "source": fingerprint,
            "release_path": str(release),
        }), encoding="utf-8")

    def test_正式安装先停回退且失败时只恢复回退(self):
        release_store = self.runtime / "release-store"
        version = "release-test"
        source = release_store / "published" / version / "source"
        launcher = source / "多用户" / "部署" / "宿主启动器.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("# sealed launcher\n", encoding="utf-8")
        plists = {
            role: self.runtime / f"{role}.plist"
            for role in 监督服务命令.LABELS
        }
        for path in plists.values():
            path.write_text("plist\n", encoding="utf-8")
        fallback = self.runtime / "fallback.plist"
        fallback.write_text("fallback\n", encoding="utf-8")
        legacy = self.runtime / "legacy.plist"
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with (
            patch.object(监督服务命令, "RELEASE_STORE", release_store),
            patch.object(监督服务命令, "PLISTS", plists),
            patch.object(监督服务命令, "FALLBACK_PLIST", fallback),
            patch.object(监督服务命令, "LEGACY_PLIST", legacy),
            patch.object(监督服务命令, "准备不切换"),
            patch.object(监督服务命令, "当前已发布版本", return_value=version),
            patch.object(监督服务命令, "_角色健康", return_value=True),
            patch.object(监督服务命令, "_run", side_effect=run),
            patch.object(监督服务命令.os, "getuid", return_value=501),
        ):
            监督服务命令.安装()

        fallback_stop = [
            "launchctl", "bootout", "gui/501/com.xj.multiuser.formal-fallback",
        ]
        first_role_start = [
            "launchctl", "bootstrap", "gui/501", str(plists["control"]),
        ]
        self.assertLess(calls.index(fallback_stop), calls.index(first_role_start))
        self.assertNotIn(
            ["launchctl", "bootstrap", "gui/501", str(fallback)],
            calls,
        )

        calls.clear()
        with (
            patch.object(监督服务命令, "RELEASE_STORE", release_store),
            patch.object(监督服务命令, "PLISTS", plists),
            patch.object(监督服务命令, "FALLBACK_PLIST", fallback),
            patch.object(监督服务命令, "LEGACY_PLIST", legacy),
            patch.object(监督服务命令, "准备不切换"),
            patch.object(监督服务命令, "当前已发布版本", return_value=version),
            patch.object(监督服务命令, "_角色健康", return_value=False),
            patch.object(监督服务命令, "_run", side_effect=run),
            patch.object(监督服务命令.os, "getuid", return_value=501),
            patch.object(监督服务命令.time, "monotonic", side_effect=(0.0, 181.0)),
        ):
            with self.assertRaisesRegex(RuntimeError, "未全部恢复"):
                监督服务命令.安装()

        self.assertIn(
            ["launchctl", "bootstrap", "gui/501", str(fallback)],
            calls,
        )
        self.assertIn(
            [
                "launchctl", "kickstart", "-k",
                "gui/501/com.xj.multiuser.formal-fallback",
            ],
            calls,
        )

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def test_请求重启只登记并安排监督器(self):
        with (
            patch.object(
                宿主服务重启,
                "_正式助手命令",
                return_value=(["host-python", "release-helper", "kickstart", "placeholder"], self.runtime, {}),
            ) as helper,
            patch.object(宿主服务重启.subprocess, "Popen") as popen,
        ):
            popen.return_value.pid = os.getpid()
            status = 宿主服务重启.请求重启()

        self.assertEqual(status["status"], "排队中")
        self.assertTrue(status["restart_id"].startswith("restart-"))
        helper.assert_called_once_with(status["restart_id"])
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["kickstart", "placeholder"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(status["helper_pid"], os.getpid())
        self.assertEqual(宿主服务重启.读取状态()["restart_id"], status["restart_id"])

    def test_重复请求不会产生第二次重启(self):
        with (
            patch.object(
                宿主服务重启,
                "_正式助手命令",
                return_value=(["host-python", "release-helper", "kickstart", "placeholder"], self.runtime, {}),
            ),
            patch.object(宿主服务重启.subprocess, "Popen") as popen,
        ):
            popen.return_value.pid = os.getpid()
            宿主服务重启.请求重启()
            with self.assertRaisesRegex(RuntimeError, "已经在重启"):
                宿主服务重启.请求重启()
        popen.assert_called_once()

    def test_重启状态由新进程收口为完成(self):
        with patch.object(宿主服务重启, "_现在", return_value=200.0):
            status = 宿主服务重启._写状态({
                "restart_id": "restart-test",
                "status": "排队中",
                "stage": "等待后台服务重启",
                "message": "正在安排重新加载多用户后台",
                "requested_at": 100.0,
                "started_at": 0,
                "completed_at": 0,
                "helper_pid": os.getpid(),
                "error": "",
            })
            self.assertEqual(宿主服务重启.标记重启中("restart-test")["status"], "重启中")
            with (
                patch.object(宿主服务重启, "_读取活动身份", return_value={
                    "version": "test", "source_sha256": "a" * 64,
                }),
                patch.dict(os.environ, {
                    "XJ_HOST_ROLE": "admin",
                    "XJ_ACTIVE_VERSION": "test",
                    "XJ_RELEASE_SOURCE_SHA256": "a" * 64,
                }),
                patch.object(宿主服务重启, "核对运行版本", return_value={}),
            ):
                done = 宿主服务重启.标记新进程已恢复()

        self.assertEqual(done["status"], "完成")
        self.assertEqual(done["stage"], "三个正式宿主服务已恢复")
        self.assertGreaterEqual(done["elapsed_seconds"], 0)

    def test_核对旧封存版时只要求该版本实际拥有的岗位(self):
        identity = {
            "status": "running",
            "version": "test",
            "source_sha256": "a" * 64,
            "source": str(self.runtime / "published" / "test" / "source"),
            "dependency_sha256": "b" * 64,
            "python_version": "3.14",
        }
        role_modules = {
            "control": self.runtime / "published" / "test" / "source" / "control.py",
            "admin": self.runtime / "published" / "test" / "source" / "admin.py",
        }
        for path in role_modules.values():
            path.write_text("# test\n", encoding="utf-8")

        role_pids = {"control": 1001, "admin": 1002}

        def read_identity(role):
            return {
                **identity,
                "role": role,
                "pid": role_pids[role],
                "module": str(role_modules[role]),
            }

        with (
            patch.object(宿主服务重启, "_读取活动身份", return_value={
                "version": "test",
                "source_sha256": "a" * 64,
                "source_path": identity["source"],
            }),
            patch.object(宿主启动器, "_正式角色列表", return_value=("control", "admin")),
            patch.object(宿主服务重启, "读取角色身份", side_effect=read_identity) as reader,
            patch.object(
                宿主服务重启.httpx,
                "get",
                return_value=SimpleNamespace(status_code=200),
            ),
        ):
            result = 宿主服务重启.核对运行版本("test", "a" * 64)

        self.assertEqual(set(result), {"control", "admin"})
        self.assertEqual([call.args[0] for call in reader.call_args_list], ["control", "admin"])

    def test_当前正式后台恢复为独立宿主而不是停在备用模式(self):
        restored = {
            "status": "restored",
            "mode": "formal",
            "version": "test",
            "source_sha256": "a" * 64,
            "roles": ["control", "admin", "rerank"],
        }
        with (
            patch.object(宿主启动器, "恢复活动正式后台", return_value=restored) as recover,
            patch.object(宿主服务重启, "核对运行版本", return_value={}) as verify,
        ):
            result = 宿主服务重启.恢复当前正式后台()

        recover.assert_called_once_with()
        verify.assert_called_once_with(
            "test", "a" * 64, roles=("control", "admin", "rerank"),
        )
        self.assertEqual(result["mode"], "formal")

    def test_备用模式不能被记为宿主事务已收口(self):
        with (
            patch.object(宿主服务重启, "当前正式运行模式", return_value="formal-fallback"),
            patch.object(监督服务命令, "确认候选正式宿主已生效") as finalize,
        ):
            with self.assertRaisesRegex(RuntimeError, "备用模式"):
                宿主服务重启.确认候选宿主运行面("switch-test")
        finalize.assert_not_called()

    def test_开发管理后台不会冒充正式后台收口重启(self):
        status = 宿主服务重启._写状态({
            "restart_id": "restart-development-admin",
            "status": "重启中",
            "requested_at": 100.0,
            "started_at": 100.0,
            "helper_pid": os.getpid(),
        })
        with patch.dict(os.environ, {"XJ_HOST_BOUNDARY_MODE": "development-admin"}):
            current = 宿主服务重启.标记新进程已恢复()
        self.assertEqual(current["restart_id"], status["restart_id"])
        self.assertEqual(current["status"], "重启中")

    def test_退出的维护程序会把排队记录改为失败(self):
        with patch.object(宿主服务重启, "_现在", return_value=200.0):
            宿主服务重启._写状态({
                "restart_id": "restart-dead-helper",
                "status": "排队中",
                "requested_at": 100.0,
                "started_at": 0,
                "helper_pid": 987654321,
            })
            with patch.object(宿主服务重启.os, "kill", side_effect=ProcessLookupError):
                failed = 宿主服务重启.读取状态()
        self.assertEqual(failed["status"], "失败")
        self.assertIn("维护程序异常退出", failed["error"])

    def test_正式运行方式从实际加载任务判断(self):
        def fallback_only(command, **kwargs):
            label = command[-1].rsplit("/", 1)[-1]
            return subprocess.CompletedProcess(
                command,
                0 if label == 宿主服务重启.FALLBACK_LABEL else 113,
                stdout="",
            )

        self.assertEqual(
            宿主服务重启.当前正式运行模式(runner=fallback_only),
            "formal-fallback",
        )

    def test_宿主边界不把开发管理进程冒充正式后台(self):
        host_python = self.runtime / "host-python" / "bin" / "python3"
        host_python.parent.mkdir(parents=True)
        host_python.write_text("python", encoding="utf-8")
        host_launcher = self.runtime / "launcher" / "host-launcher.py"
        host_launcher.parent.mkdir(parents=True)
        host_launcher.write_text("launcher", encoding="utf-8")
        (self.runtime / "host-python.json").write_text("{}", encoding="utf-8")
        private_terms = self.runtime / "private" / "本机私密扫描词.txt"
        private_terms.parent.mkdir(parents=True)
        private_terms.write_text("secret\n", encoding="utf-8")
        with (
            patch.object(宿主服务重启, "HOST_PYTHON", host_python),
            patch.object(宿主服务重启, "HOST_LAUNCHER", host_launcher),
            patch.object(宿主服务重启, "当前正式运行模式", return_value="formal-fallback"),
            patch.object(宿主服务重启, "_启动描述正确", return_value=True),
            patch.object(宿主服务重启, "_回退启动描述正确", return_value=True),
            patch.object(
                监督服务命令,
                "读取宿主运行环境状态",
                return_value={"ready": True, "requires_approval": False},
            ),
            patch.dict(os.environ, {"XJ_HOST_ROLE": "admin"}, clear=False),
        ):
            state = 宿主服务重启.宿主边界状态()

        self.assertEqual(state["mode"], "formal-fallback")
        self.assertFalse(state["ready"])
        self.assertIn("冻结回退模式", state["reason"])

    def test_重启助手使用开发管理代码但只重启正式后台(self):
        with patch.object(
            宿主服务重启, "当前正式运行模式", return_value="formal-fallback",
        ):
            command, cwd, env = 宿主服务重启._正式助手命令("restart-test")
        development = Path(宿主服务重启.__file__).resolve().parents[2]
        self.assertEqual(cwd, development)
        self.assertEqual(env["PYTHONPATH"], str(development))
        self.assertEqual(env["XJ_RESTART_MODE"], "formal-fallback")
        self.assertEqual(command[-2:], ["kickstart", "restart-test"])
        self.assertNotIn(str(self.runtime / "published"), " ".join(command))

    def test_正式回退重启会恢复三个独立宿主再核对收口(self):
        宿主服务重启._写状态({
            "restart_id": "restart-fallback",
            "status": "排队中",
            "requested_at": 100.0,
            "started_at": 0,
            "helper_pid": os.getpid(),
        })
        with (
            patch.object(宿主服务重启.time, "sleep"),
            patch.object(宿主启动器, "恢复活动正式后台") as recover,
            patch.object(
                宿主服务重启,
                "_等待正式后台恢复",
                return_value=("control", "admin"),
            ) as wait,
            patch.object(宿主服务重启, "_标记重启完成") as complete,
            patch.dict(os.environ, {"XJ_RESTART_MODE": "formal-fallback"}),
        ):
            宿主服务重启.触发系统重启("restart-fallback")
        recover.assert_called_once_with()
        wait.assert_called_once()
        complete.assert_called_once_with("restart-fallback", ("control", "admin"))

    def test_重启失败会留下明确状态(self):
        宿主服务重启._写状态({
            "restart_id": "restart-fail",
            "status": "重启中",
            "stage": "正在重新加载后台服务",
            "message": "后台正在重启",
            "requested_at": 100.0,
            "started_at": 100.0,
            "completed_at": 0,
            "error": "",
        })
        failed = 宿主服务重启.标记失败("restart-fail", "launchctl 不可用")

        self.assertEqual(failed["status"], "失败")
        self.assertEqual(failed["error"], "launchctl 不可用")

    def test_重启前先确认新进程具备启动条件(self):
        plist = self.runtime / "com.xj.multiuser.supervisor.plist"
        plist.write_text("installed", encoding="utf-8")
        with (
            patch.object(监督服务命令, "PLIST", plist),
            patch.object(监督服务命令, "_环境", return_value={}) as environment,
        ):
            管理服务._后台重启预检()
        environment.assert_called_once_with()

    def test_后台尚未安装时重启预检会拦住(self):
        with (
            patch.object(监督服务命令, "PLIST", self.runtime / "missing.plist"),
            patch.object(监督服务命令, "LEGACY_PLIST", self.runtime / "legacy-missing.plist"),
        ):
            with self.assertRaisesRegex(RuntimeError, "尚未安装"):
                管理服务._后台重启预检()

    def test_旧单进程存在时重启预检仍可用(self):
        legacy = self.runtime / "legacy.plist"
        legacy.write_text("installed", encoding="utf-8")
        with (
            patch.object(监督服务命令, "PLIST", self.runtime / "missing.plist"),
            patch.object(监督服务命令, "LEGACY_PLIST", legacy),
            patch.object(监督服务命令, "_旧单进程环境", return_value={}) as environment,
        ):
            管理服务._后台重启预检()
        environment.assert_called_once_with()

    def test_冻结正式回退不会被旧启动项误认成开发模式(self):
        legacy = self.runtime / "legacy.plist"
        legacy.write_text("retained for audit", encoding="utf-8")
        with (
            patch.object(宿主服务重启, "LEGACY_PLIST", legacy),
            patch.dict(os.environ, {
                "XJ_HOST_BOUNDARY_MODE": "formal-fallback",
                "XJ_HOST_ROLE": "",
            }, clear=False),
        ):
            self.assertFalse(宿主服务重启._旧单进程模式())

    def test_冻结正式回退重启走回退启动项(self):
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)

        with (
            patch.object(宿主服务重启.subprocess, "run", side_effect=run),
            patch.object(宿主服务重启.os, "getuid", return_value=501),
        ):
            宿主服务重启._触发冻结回退重启()

        self.assertEqual(calls[0][-1], "gui/501/com.xj.multiuser.formal-fallback")

    def test_准备宿主边界把旧锁复制到独立活动指针(self):
        legacy = self.runtime / "legacy.lock"
        active = self.runtime / "active.lock"
        with (
            patch.object(监督服务命令, "LOCK_PATH", legacy),
            patch.object(监督服务命令, "ACTIVE_LOCK", active),
            patch.object(监督服务命令, "当前已发布版本", return_value="release-test"),
            patch.object(监督服务命令, "切换当前版本") as switch,
        ):
            self.assertEqual(监督服务命令._迁移活动指针(), "release-test")
        switch.assert_called_once_with("release-test", lock_path=active)

    def test_准备宿主边界不改开发源登记(self):
        identity = {
            "python_version": "3.14.2",
            "pip_freeze_sha256": "a" * 64,
            "requirements_sha256": "b" * 64,
        }
        with (
            patch.object(监督服务命令, "RUNTIME", self.runtime),
            patch.object(监督服务命令, "_准备私密扫描词"),
            patch.object(监督服务命令, "_登记开发源") as register,
            patch.object(监督服务命令, "_准备独立运行环境", return_value=identity),
            patch.object(监督服务命令, "_准备启动器"),
            patch.object(监督服务命令, "_迁移活动指针", return_value="release-test"),
            patch.object(监督服务命令, "核对独立运行环境"),
            patch.object(监督服务命令, "_写启动项"),
        ):
            result = 监督服务命令.准备不切换()

        register.assert_not_called()
        self.assertEqual(result["active_version"], "release-test")

    def test_候选宿主环境改名后登记最终解释器路径(self):
        candidate_id = "candidate-20260827T120000Z-1234abcd"
        candidate_root = self.runtime / "candidates" / candidate_id
        source = candidate_root / "source"
        source.mkdir(parents=True)
        (source / "requirements-host.txt").write_text("uvicorn==0\n", encoding="utf-8")
        launcher_source = source / "多用户" / "部署" / "宿主启动器.py"
        launcher_source.parent.mkdir(parents=True)
        launcher_source.write_text("# candidate launcher\n", encoding="utf-8")
        candidate_host_root = self.runtime / "candidate-host"
        staging = self.runtime / "candidate-stage"
        (staging / "bin").mkdir(parents=True)
        temporary_python = staging / "bin" / "python3"
        temporary_python.write_text("python\n", encoding="utf-8")
        private_source = self.runtime / "development-private-terms.txt"
        private_source.write_text("test-private-term\n", encoding="utf-8")
        identity = {
            "schema": 1,
            "python_executable": str(temporary_python.resolve()),
            "python_version": "3.14.2",
            "pip_freeze_sha256": "a" * 64,
        }
        release = {
            "candidate_id": candidate_id,
            "path": str(candidate_root),
            "build_context": str(source),
            "source": {"sha256": "b" * 64},
        }

        def run(command, **kwargs):
            if command[-1:] == ["runtime-identity"]:
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(identity), stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with (
            patch.object(宿主服务重启, "CANDIDATE_HOST_ROOT", candidate_host_root),
            patch.object(宿主服务重启.tempfile, "mkdtemp", return_value=str(staging)),
            patch.object(宿主服务重启.subprocess, "run", side_effect=run),
            patch.object(
                监督服务命令,
                "PRIVATE_TERMS",
                self.runtime / "private" / "本机私密扫描词.txt",
            ),
            patch.dict(
                os.environ,
                {"XJ_PRIVATE_TERMS_SOURCE": str(private_source)},
                clear=False,
            ),
        ):
            result = 宿主服务重启.准备候选宿主环境(release)

        target = candidate_host_root / candidate_id
        manifest = json.loads((target / "host-python.json").read_text(encoding="utf-8"))
        self.assertEqual(result["python"], str((target / "bin" / "python3").resolve()))
        self.assertEqual(
            manifest["python_executable"],
            str((target / "bin" / "python3").resolve()),
        )

    def test_后台启动项固定包含发布所需工具路径(self):
        path = 监督服务命令._宿主工具路径().split(":")
        self.assertIn("/opt/homebrew/bin", path)
        self.assertIn("/usr/local/bin", path)
        self.assertIn("/usr/bin", path)
        document = 监督服务命令._启动项文档()
        self.assertEqual(document["EnvironmentVariables"]["PATH"], 监督服务命令._宿主工具路径())

    def test_后台重启与四种账号变更共用同一把操作锁(self):
        async def scenario() -> None:
            calls: list[str] = []
            lock = asyncio.Lock()

            class Registry:
                def 要求没有发布维护(self):
                    self_test.assertTrue(lock.locked())

                def 未解决发布恢复(self):
                    self_test.assertTrue(lock.locked())
                    return []

                def 未解决发布恢复(self):
                    self_test.assertTrue(lock.locked())
                    return None

            class Lifecycle:
                def _record(self, name):
                    self_test.assertTrue(lock.locked())
                    calls.append(name)

                def 开通(self, **kwargs):
                    self._record("create")
                    return {"account_id": "acc_test"}

                def 停用(self, account_id):
                    self._record("stop")
                    return {"account_id": account_id}

                def 重启(self, account_id):
                    self._record("restart")
                    return {"account_id": account_id}

                def 永久删除(self, account_id, confirm):
                    self._record("delete")
                    return {"account_id": account_id, "deleted": True}

            class Auth:
                def 发邀请(self, account_id):
                    self_test.assertTrue(lock.locked())
                    return {"token": "invite-token", "account_id": account_id}

                def 撤销用户会话(self, account_id):
                    self_test.assertTrue(lock.locked())

            app = SimpleNamespace(state=SimpleNamespace(
                operation_lock=lock,
                registry=Registry(),
                lifecycle=Lifecycle(),
                auth=Auth(),
                release_task=None,
            ))
            create_request = _假请求(app, {
                "email": "test@example.invalid",
                "display_name": "测试账号",
                "call_name": "测试先生",
            })
            empty_request = _假请求(app)
            delete_request = _假请求(app, {"confirm": "acc_test"})
            operations = (
                ("create", lambda: 管理服务.create_account(create_request)),
                ("stop", lambda: 管理服务.stop("acc_test", empty_request)),
                ("restart", lambda: 管理服务.restart("acc_test", empty_request)),
                ("delete", lambda: 管理服务.purge("acc_test", delete_request)),
            )
            for expected, start in operations:
                before = list(calls)
                await lock.acquire()
                task = asyncio.create_task(start())
                await asyncio.sleep(0.01)
                self.assertEqual(calls, before)
                lock.release()
                await task
                self.assertEqual(calls[-1], expected)

            def restart_now():
                self.assertTrue(lock.locked())
                calls.append("system-restart")
                return {"restart_id": "restart-test", "status": "排队中"}

            with patch.object(管理服务, "请求重启", side_effect=restart_now):
                await lock.acquire()
                task = asyncio.create_task(管理服务.system_restart(empty_request))
                await asyncio.sleep(0.01)
                self.assertNotIn("system-restart", calls)
                lock.release()
                await task
            self.assertEqual(calls[-1], "system-restart")

        self_test = self
        with (
            patch.object(管理服务, "_管理员", return_value="owner-session"),
            patch.object(管理服务, "正在重启", return_value=False),
            patch.object(管理服务, "_后台重启预检"),
        ):
            asyncio.run(scenario())

    def test_后台正在重启时四种账号变更全部拒绝且不执行(self):
        async def scenario() -> None:
            calls: list[str] = []

            class Registry:
                def 要求没有发布维护(self):
                    calls.append("registry-check")

                def 未解决发布恢复(self):
                    calls.append("recovery-check")
                    return []

            class Lifecycle:
                def 开通(self, **kwargs):
                    calls.append("create")

                def 停用(self, account_id):
                    calls.append("stop")

                def 重启(self, account_id):
                    calls.append("restart")

                def 永久删除(self, account_id, confirm):
                    calls.append("delete")

            class Auth:
                def 发邀请(self, account_id):
                    calls.append("invite")

                def 撤销用户会话(self, account_id):
                    calls.append("revoke")

            app = SimpleNamespace(state=SimpleNamespace(
                operation_lock=asyncio.Lock(),
                registry=Registry(),
                lifecycle=Lifecycle(),
                auth=Auth(),
            ))
            requests = (
                lambda: 管理服务.create_account(_假请求(app, {"email": "test@example.invalid"})),
                lambda: 管理服务.stop("acc_test", _假请求(app)),
                lambda: 管理服务.restart("acc_test", _假请求(app)),
                lambda: 管理服务.purge("acc_test", _假请求(app, {"confirm": "acc_test"})),
            )
            for start in requests:
                with self.assertRaises(管理服务.HTTPException) as raised:
                    await start()
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("后台正在重启", str(raised.exception.detail))
            self.assertEqual(calls, [])

        with (
            patch.object(管理服务, "_管理员", return_value="owner-session"),
            patch.object(管理服务, "正在重启", return_value=True),
        ):
            asyncio.run(scenario())

    def test_历史恢复未核清时账号变更全阻断但后台重启仍允许自救(self):
        async def scenario() -> None:
            calls: list[str] = []

            class Registry:
                def 要求没有发布维护(self):
                    calls.append("maintenance-check")

                def 未解决发布恢复(self):
                    calls.append("recovery-check")
                    return [{"deployment_id": "deployment-unresolved"}]

            class Lifecycle:
                def 开通(self, **kwargs):
                    calls.append("create")

                def 停用(self, account_id):
                    calls.append("stop")

                def 重启(self, account_id):
                    calls.append("restart")

                def 永久删除(self, account_id, confirm):
                    calls.append("delete")

            class Auth:
                def 发邀请(self, account_id):
                    calls.append("invite")

                def 撤销用户会话(self, account_id):
                    calls.append("revoke")

            app = SimpleNamespace(state=SimpleNamespace(
                operation_lock=asyncio.Lock(),
                registry=Registry(),
                lifecycle=Lifecycle(),
                auth=Auth(),
                release_task=None,
            ))
            requests = (
                lambda: 管理服务.create_account(_假请求(app, {"email": "test@example.invalid"})),
                lambda: 管理服务.stop("acc_test", _假请求(app)),
                lambda: 管理服务.restart("acc_test", _假请求(app)),
                lambda: 管理服务.purge("acc_test", _假请求(app, {"confirm": "acc_test"})),
            )
            for start in requests:
                with self.assertRaises(管理服务.HTTPException) as raised:
                    await start()
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("没有恢复核清", str(raised.exception.detail))

            with patch.object(
                管理服务,
                "请求重启",
                side_effect=lambda: calls.append("system-restart") or {
                    "restart_id": "restart-rescue", "status": "排队中",
                },
            ):
                result = await 管理服务.system_restart(_假请求(app))

            self.assertEqual(result["restart"]["restart_id"], "restart-rescue")
            self.assertEqual(calls.count("recovery-check"), 4)
            self.assertIn("system-restart", calls)
            self.assertFalse({"create", "stop", "restart", "delete", "invite", "revoke"} & set(calls))

        with (
            patch.object(管理服务, "_管理员", return_value="owner-session"),
            patch.object(管理服务, "正在重启", return_value=False),
            patch.object(管理服务, "_后台重启预检"),
        ):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

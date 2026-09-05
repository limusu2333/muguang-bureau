from __future__ import annotations

import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from 多用户.控制面 import 宿主服务重启
from 多用户.控制面 import __main__ as 控制面入口
from 多用户.部署 import 宿主启动器, 监督服务命令


class 宿主版本边界测试(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_store = self.root / "release-store"
        self.version = "release-test-1"
        self.release = self.release_store / "published" / self.version
        self.source = self.release / "source"
        package = self.source / "多用户" / "控制面"
        package.mkdir(parents=True)
        (self.source / "多用户" / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text("VALUE = 1\n", encoding="utf-8")
        fingerprint = 宿主启动器._树指纹(self.source)
        self.sha = fingerprint["sha256"]
        manifest = {
            "schema": 2,
            "version": self.version,
            "version_name": "测试正式版",
            "source": fingerprint,
        }
        (self.release / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
        )
        self.active = self.root / "active-release.json"
        self.active.write_text(json.dumps({
            "schema": 3,
            "version": self.version,
            "version_name": "测试正式版",
            "source": fingerprint,
            "release_path": str(self.release),
        }), encoding="utf-8")
        self.active_patch = patch.object(宿主服务重启, "ACTIVE_LOCK", self.active)
        self.active_patch.start()

    def tearDown(self) -> None:
        self.active_patch.stop()
        self.temporary.cleanup()

    def test_活动指针只能解析已发布完整副本(self):
        result = 宿主启动器.解析活动发布(self.active)

        self.assertEqual(result["version"], self.version)
        self.assertEqual(Path(result["source_path"]), self.source.resolve())
        self.assertEqual(result["source_sha256"], self.sha)

    def test_源码被改后启动器拒绝运行(self):
        (self.source / "多用户" / "控制面" / "__main__.py").write_text(
            "VALUE = 2\n", encoding="utf-8",
        )

        with self.assertRaisesRegex(宿主启动器.宿主启动错误, "完整性"):
            宿主启动器.解析活动发布(self.active)

    def test_影子验收接受发布器生成的候选编号(self):
        candidate_id = "candidate-20260827T120000Z-1234abcd"

        result = 宿主启动器.解析指定源码(
            self.source,
            version=candidate_id,
            source_sha256=self.sha,
            active_path=self.active,
        )

        self.assertEqual(result["version"], candidate_id)
        self.assertEqual(Path(result["source_path"]), self.source.resolve())
        self.assertEqual(result["source_sha256"], self.sha)

    def test_正式版本校验仍拒绝候选编号(self):
        candidate_id = "candidate-20260827T120000Z-1234abcd"

        with self.assertRaisesRegex(宿主启动器.宿主启动错误, "宿主版本号无效"):
            宿主启动器.解析指定发布(
                self.release,
                version=candidate_id,
                source_sha256=self.sha,
            )

    def test_活动指针不能指向预发布区但影子验收可以(self):
        staged = self.release_store / "staged" / self.version
        staged.parent.mkdir(parents=True)
        self.release.rename(staged)
        pointer = json.loads(self.active.read_text(encoding="utf-8"))
        pointer["release_path"] = str(staged)
        self.active.write_text(json.dumps(pointer), encoding="utf-8")

        with self.assertRaisesRegex(宿主启动器.宿主启动错误, "正式副本"):
            宿主启动器.解析活动发布(self.active)
        result = 宿主启动器.解析指定发布(
            staged, version=self.version, source_sha256=self.sha, allow_staged=True,
        )
        self.assertEqual(Path(result["release_path"]), staged.resolve())

    def test_三个启动项使用独立解释器和独立角色(self):
        documents = {role: 监督服务命令._启动项文档(role) for role in 监督服务命令.LABELS}

        self.assertEqual(len({item["Label"] for item in documents.values()}), 3)
        for role, document in documents.items():
            arguments = document["ProgramArguments"]
            self.assertEqual(arguments[0], str(监督服务命令.HOST_PYTHON))
            self.assertEqual(arguments[1], str(监督服务命令.LAUNCHER))
            self.assertEqual(arguments[arguments.index("--role") + 1], role)
            self.assertEqual(document["WorkingDirectory"], str(监督服务命令.RUNTIME))
            self.assertNotIn(".venv", " ".join(arguments))
            self.assertEqual(
                document["EnvironmentVariables"]["XJ_RELEASE_LOCK"],
                str(监督服务命令.ACTIVE_LOCK),
            )
        self.assertNotIn(
            "XJ_DEVELOPMENT_SOURCE", documents["control"]["EnvironmentVariables"],
        )
        self.assertNotIn(
            "XJ_DEVELOPMENT_SOURCE", documents["rerank"]["EnvironmentVariables"],
        )
        for role in ("control", "admin", "rerank"):
            self.assertNotIn(
                "XJ_DEVELOPMENT_ADMIN", documents[role]["EnvironmentVariables"],
            )
        self.assertNotIn(
            "XJ_DEVELOPMENT_SOURCE", documents["admin"]["EnvironmentVariables"],
        )

    def test_本人开发管理后台使用独立端口和开发源(self):
        development = self.root / "development"
        (development / "多用户" / "控制面").mkdir(parents=True)
        (development / "多用户" / "控制面" / "管理服务.py").write_text(
            "app = object()\n", encoding="utf-8",
        )
        runtime = self.root / "runtime"
        with (
            patch.object(监督服务命令, "RUNTIME", runtime),
            patch.object(监督服务命令, "ROOT", development),
            patch.object(监督服务命令, "_开发源目录", return_value=development),
        ):
            document = 监督服务命令._开发管理启动项文档(
                host_python=self.root / "host-python",
                launcher=self.root / "host-launcher.py",
                manifest=self.root / "host-python.json",
                active_lock=self.root / "active-release.json",
                development_source=development,
            )

        self.assertEqual(document["Label"], 监督服务命令.DEVELOPMENT_ADMIN_LABEL)
        arguments = document["ProgramArguments"]
        self.assertIn("serve-development-admin", arguments)
        self.assertEqual(
            arguments[arguments.index("--port") + 1],
            str(监督服务命令.DEVELOPMENT_ADMIN_PORT),
        )
        self.assertEqual(
            document["EnvironmentVariables"]["XJ_DEVELOPMENT_SOURCE"],
            str(development.resolve()),
        )
        self.assertEqual(
            document["EnvironmentVariables"]["XJ_ADMIN_PORT"],
            str(监督服务命令.DEVELOPMENT_ADMIN_PORT),
        )
        self.assertEqual(document["EnvironmentVariables"]["XJ_DEVELOPMENT_ADMIN"], "1")

    def test_办公室只把本人管理页指向开发端口(self):
        owner = self.root.parent.parent / "前端" / "src" / "components" / "OwnerAccountMenu.tsx"
        if not owner.is_file():
            owner = Path(__file__).resolve().parents[1] / "前端" / "src" / "components" / "OwnerAccountMenu.tsx"
        content = owner.read_text(encoding="utf-8")
        self.assertIn("OWNER_DEVELOPMENT_ADMIN_URL = 'http://localhost:37660'", content)
        self.assertIn("${OWNER_DEVELOPMENT_ADMIN_URL}/?embedded=1", content)
        self.assertNotIn('iframe src="http://localhost:37655/?embedded=1"', content)

    def test_办公室只从自身开发区启动发布后台(self):
        script = Path(__file__).resolve().parents[1] / "打开办公室.command"
        content = script.read_text(encoding="utf-8")
        self.assertIn('install-development-admin --source "$OFFICE_ROOT"', content)
        self.assertIn('"$PYTHON" -u "$OFFICE_ROOT/工具/机房.py"', content)
        self.assertIn('[[ "$command" == *"$OFFICE_ROOT/工具/机房.py"* ]]', content)
        self.assertNotIn("30_开发公司_多用户施工", content)
        self.assertNotIn("MULTIUSER_ROOT", content)

    def test_现役运行代码不再引用旧多用户工作目录(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("工具/对外访问.py", "工具/工具间.py"):
            with self.subTest(relative=relative):
                content = (root / relative).read_text(encoding="utf-8")
                self.assertNotIn("30_开发公司_多用户施工", content)

    def test_旧封存版本缺少精排入口仍可识别回退角色(self):
        control = self.source / "多用户" / "控制面" / "服务.py"
        admin = self.source / "多用户" / "控制面" / "管理服务.py"
        control.write_text("app = object()\n", encoding="utf-8")
        admin.write_text("app = object()\n", encoding="utf-8")

        self.assertEqual(
            宿主启动器._正式角色列表(self.source),
            ("control", "admin"),
        )

    def test_正式启动项不携带管理员开发目录(self):
        runtime = self.root / "runtime"
        development = self.root / "development"
        released = self.root / "released"
        development.mkdir()
        released.mkdir()
        record = runtime / "data" / "development-source.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"schema": 1, "source": str(development)}), encoding="utf-8")
        with (
            patch.object(监督服务命令, "RUNTIME", runtime),
            patch.object(监督服务命令, "ROOT", released),
            patch.object(监督服务命令, "PRIVATE_TERMS", runtime / "private" / "terms.txt"),
            patch.dict(os.environ, {}, clear=True),
        ):
            document = 监督服务命令._启动项文档("admin")

        self.assertNotIn("XJ_DEVELOPMENT_SOURCE", document["EnvironmentVariables"])

    def test_坏启动描述不会让宿主边界检查抛索引错误(self):
        malformed = self.root / "control.plist"
        malformed.write_bytes(plistlib.dumps({
            "ProgramArguments": [
                str(宿主服务重启.HOST_PYTHON),
                str(宿主服务重启.HOST_LAUNCHER),
                "serve", "--role",
            ],
            "EnvironmentVariables": {
                "XJ_RELEASE_LOCK": str(宿主服务重启.ACTIVE_LOCK),
            },
        }))
        with patch.object(
            宿主服务重启, "PLISTS", {"control": malformed},
        ):
            self.assertFalse(宿主服务重启._启动描述正确("control"))

    def test_正式副本没有私密文件时从登记开发目录准备(self):
        runtime = self.root / "runtime"
        released = self.root / "released"
        development = self.root / "development"
        released.mkdir()
        development.mkdir()
        (development / "本机私密扫描词.txt").write_text("development-secret\n", encoding="utf-8")
        record = runtime / "data" / "development-source.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"schema": 1, "source": str(development)}), encoding="utf-8")
        with (
            patch.object(监督服务命令, "RUNTIME", runtime),
            patch.object(监督服务命令, "ROOT", released),
            patch.object(监督服务命令, "PRIVATE_TERMS", runtime / "private" / "terms.txt"),
            patch.dict(os.environ, {}, clear=True),
        ):
            监督服务命令._准备私密扫描词()

        self.assertEqual(
            (runtime / "private" / "terms.txt").read_text(encoding="utf-8"),
            "development-secret\n",
        )

    def test_候选暂存前会准备私密配置和正式回退启动项(self):
        runtime = self.root / "runtime"
        candidate_root = runtime / "candidate-host" / "candidate-test"
        candidate_root.mkdir(parents=True)
        candidate_python = candidate_root / "bin" / "python3"
        candidate_python.parent.mkdir()
        candidate_python.write_text("python", encoding="utf-8")
        candidate_launcher = candidate_root / "host-launcher.py"
        candidate_launcher.write_text("launcher", encoding="utf-8")
        candidate_manifest = candidate_root / "host-python.json"
        candidate_manifest.write_text("{}", encoding="utf-8")
        host_python = runtime / "host-python" / "bin" / "python3"
        host_python.parent.mkdir(parents=True)
        host_python.write_text("python", encoding="utf-8")
        host_launcher = runtime / "launcher" / "host-launcher.py"
        host_launcher.parent.mkdir(parents=True)
        host_launcher.write_text("stale-launcher", encoding="utf-8")
        launcher_source = self.root / "current-host-launcher.py"
        launcher_source.write_text("current-launcher", encoding="utf-8")
        host_identity = runtime / "host-python.json"
        host_identity.write_text("{}", encoding="utf-8")
        active = runtime / "data" / "active-release.json"
        active.parent.mkdir(parents=True)
        active.write_text("{}", encoding="utf-8")
        private_source = self.root / "private-source.txt"
        private_source.write_text("secret\n", encoding="utf-8")
        plists = {
            role: self.root / f"{role}.plist"
            for role in 监督服务命令.LABELS
        }
        fallback = self.root / "formal-fallback.plist"
        switch_file = runtime / "data" / "host-runtime-switch.json"
        with (
            patch.object(监督服务命令, "RUNTIME", runtime),
            patch.object(监督服务命令, "HOST_PYTHON", host_python),
            patch.object(监督服务命令, "LAUNCHER", host_launcher),
            patch.object(监督服务命令, "LAUNCHER_SOURCE", launcher_source),
            patch.object(监督服务命令, "HOST_IDENTITY", host_identity),
            patch.object(监督服务命令, "ACTIVE_LOCK", active),
            patch.object(监督服务命令, "PRIVATE_TERMS", runtime / "private" / "本机私密扫描词.txt"),
            patch.object(监督服务命令, "PLISTS", plists),
            patch.object(监督服务命令, "FALLBACK_PLIST", fallback),
            patch.object(监督服务命令, "HOST_RUNTIME_SWITCH", switch_file),
            patch.dict(os.environ, {"XJ_PRIVATE_TERMS_SOURCE": str(private_source)}),
        ):
            stale_fallback = 监督服务命令._冻结正式回退文档()
            stale_fallback["EnvironmentVariables"]["XJ_DEVELOPMENT_SOURCE"] = str(
                self.root / "development"
            )
            fallback.write_bytes(plistlib.dumps(stale_fallback))
            state = 监督服务命令.暂存候选正式宿主运行面(
                {
                    "python": str(candidate_python),
                    "launcher": str(candidate_launcher),
                    "manifest": str(candidate_manifest),
                },
                "switch-test",
            )

        self.assertEqual(state["status"], "staged")
        self.assertEqual(host_launcher.read_text(encoding="utf-8"), "current-launcher")
        self.assertEqual(
            (runtime / "private" / "本机私密扫描词.txt").read_text(encoding="utf-8"),
            "secret\n",
        )
        backup_fallback = Path(state["backup_dir"]) / "fallback.plist"
        document = plistlib.loads(backup_fallback.read_bytes())
        self.assertEqual(
            document["ProgramArguments"][:3],
            [str(host_python), str(host_launcher), "serve-legacy"],
        )
        active_fallback = plistlib.loads(fallback.read_bytes())
        self.assertEqual(
            active_fallback["ProgramArguments"][:3],
            [str(host_python), str(host_launcher), "serve-legacy"],
        )
        self.assertNotIn(
            "XJ_DEVELOPMENT_SOURCE",
            active_fallback["EnvironmentVariables"],
        )

    def test_成功收口的宿主可作为下一版回退基线(self):
        runtime = self.root / "runtime"
        private_terms = runtime / "private" / "terms.txt"
        private_terms.parent.mkdir(parents=True)
        private_terms.write_text("private\n", encoding="utf-8")
        active = runtime / "data" / "active-release.json"
        active.parent.mkdir(parents=True)
        active.write_text('{"version":"formal-v1"}', encoding="utf-8")
        switch_file = runtime / "data" / "host-runtime-switch.json"
        plists = {
            role: self.root / f"{role}.plist"
            for role in 监督服务命令.LABELS
        }
        fallback = self.root / "formal-fallback.plist"
        for path in (*plists.values(), fallback):
            path.write_text("formal-v1", encoding="utf-8")

        def candidate_runtime(name: str) -> dict[str, str]:
            root = runtime / "candidate-host" / name
            python = root / "bin" / "python3"
            launcher = root / "host-launcher.py"
            manifest = root / "host-python.json"
            python.parent.mkdir(parents=True)
            python.write_text("python", encoding="utf-8")
            launcher.write_text(name, encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            return {
                "python": str(python),
                "launcher": str(launcher),
                "manifest": str(manifest),
            }

        first_runtime = candidate_runtime("candidate-one")
        second_runtime = candidate_runtime("candidate-two")

        def write_descriptors(*, host_python, launcher, manifest, **_kwargs):
            for path in plists.values():
                path.write_text(str(launcher), encoding="utf-8")

        with (
            patch.object(监督服务命令, "RUNTIME", runtime),
            patch.object(监督服务命令, "ACTIVE_LOCK", active),
            patch.object(监督服务命令, "PRIVATE_TERMS", private_terms),
            patch.object(监督服务命令, "PLISTS", plists),
            patch.object(监督服务命令, "FALLBACK_PLIST", fallback),
            patch.object(监督服务命令, "HOST_RUNTIME_SWITCH", switch_file),
            patch.object(监督服务命令, "_准备私密扫描词"),
            patch.object(监督服务命令, "_准备启动器"),
            patch.object(监督服务命令, "_确保冻结正式回退文档"),
            patch.object(监督服务命令, "_写启动项", side_effect=write_descriptors),
        ):
            first = 监督服务命令.暂存候选正式宿主运行面(
                first_runtime, "switch-one",
            )
            with self.assertRaisesRegex(RuntimeError, "尚未收口"):
                监督服务命令.暂存候选正式宿主运行面(
                    second_runtime, "switch-two",
                )

            switch_file.write_text(
                json.dumps({**first, "status": "host-ready"}), encoding="utf-8",
            )
            committed = 监督服务命令.确认候选正式宿主已生效("switch-one")
            second = 监督服务命令.暂存候选正式宿主运行面(
                second_runtime, "switch-two",
            )
            restored = 监督服务命令.恢复暂存候选正式宿主("switch-two")

        self.assertEqual(committed["status"], "committed")
        self.assertEqual(second["status"], "staged")
        self.assertEqual(restored["status"], "restored")
        second_backup = Path(second["backup_dir"])
        for role in plists:
            self.assertEqual(
                (second_backup / f"{role}.plist").read_text(encoding="utf-8"),
                str(Path(first_runtime["launcher"]).resolve()),
            )
            self.assertEqual(
                plists[role].read_text(encoding="utf-8"),
                str(Path(first_runtime["launcher"]).resolve()),
            )

    def test_宿主进程就绪不等于发布已收口(self):
        writes: list[dict] = []
        state = {"schema": 1, "switch_id": "switch-test", "status": "staged"}
        reads = iter((
            {"schema": 1, "switch_id": "switch-test", "status": "switching"},
            state,
        ))
        with (
            patch.object(宿主启动器, "_读JSON", side_effect=lambda _path: next(reads)),
            patch.object(
                宿主启动器,
                "_写JSON",
                side_effect=lambda _path, value: writes.append(dict(value)) or value,
            ),
        ):
            宿主启动器._确认暂存宿主运行面("switch-test")

        self.assertEqual(writes[-1]["status"], "host-ready")
        self.assertNotIn("committed_at", writes[-1])

    def test_宿主边界缺少私密配置会明确阻止(self):
        runtime = self.root / "runtime"
        host_python = runtime / "host-python" / "bin" / "python3"
        host_python.parent.mkdir(parents=True)
        host_python.write_text("python", encoding="utf-8")
        host_launcher = runtime / "launcher" / "host-launcher.py"
        host_launcher.parent.mkdir(parents=True)
        host_launcher.write_text("launcher", encoding="utf-8")
        (runtime / "host-python.json").write_text("{}", encoding="utf-8")
        with (
            patch.object(宿主服务重启, "RUNTIME", runtime),
            patch.object(宿主服务重启, "HOST_PYTHON", host_python),
            patch.object(宿主服务重启, "HOST_LAUNCHER", host_launcher),
            patch.object(宿主服务重启, "FALLBACK_PLIST", runtime / "fallback.plist"),
            patch.object(宿主服务重启, "PLISTS", {
                role: runtime / f"{role}.plist"
                for role in 宿主服务重启.LABELS
            }),
            patch.object(宿主服务重启, "_旧单进程模式", return_value=False),
            patch.object(宿主服务重启, "当前正式运行模式", return_value="formal"),
            patch.object(
                监督服务命令,
                "读取宿主运行环境状态",
                return_value={"ready": True, "requires_approval": False},
            ),
            patch.dict(os.environ, {"XJ_HOST_ROLE": "admin"}, clear=False),
        ):
            result = 宿主服务重启.宿主边界状态()

        self.assertFalse(result["ready"])
        self.assertFalse(result["private_terms_ready"])
        self.assertIn("私密扫描词", result["reason"])

    def test_控制面一次只选择一个角色(self):
        control = 控制面入口.角色配置("control", {})
        admin = 控制面入口.角色配置("admin", {})
        rerank = 控制面入口.角色配置("rerank", {})

        self.assertEqual(control[2], 37654)
        self.assertEqual(admin[2], 37655)
        self.assertEqual(rerank[2], 37657)
        self.assertEqual(len({control[0], admin[0], rerank[0]}), 3)
        with self.assertRaisesRegex(RuntimeError, "只允许监听本机"):
            控制面入口.角色配置("control", {"XJ_SUPERVISOR_HOST": "0.0.0.0"})

    def test_正式环境不携带开发源开发管理环境单独携带(self):
        private_terms = self.root / "private.txt"
        private_terms.write_text("secret-term\n", encoding="utf-8")
        development = self.root / "development"
        development.mkdir()
        release = {
            "version": self.version,
            "source_path": str(self.source),
            "source_sha256": self.sha,
            "active_path": str(self.active),
        }
        inherited = {
            "XJ_PRIVATE_TERMS_FILE": str(private_terms),
            "XJ_DEVELOPMENT_SOURCE": str(development),
        }
        identity = {
            "schema": 1,
            "python_executable": str(Path(os.sys.executable).resolve()),
            "python_version": "3.test",
            "pip_freeze_sha256": "a" * 64,
        }
        with (
            patch.object(宿主启动器, "_钥匙串", return_value="x" * 40),
            patch.object(宿主启动器, "_docker路径", return_value="/usr/bin/true"),
            patch.object(宿主启动器, "核对运行环境身份", return_value=identity),
        ):
            control = 宿主启动器.构建正式环境(
                "control", release, inherited=inherited,
            )
            admin = 宿主启动器.构建正式环境(
                "admin", release, inherited=inherited,
            )

        self.assertNotIn("XJ_DEVELOPMENT_SOURCE", control)
        self.assertNotIn("XJ_DEVELOPMENT_SOURCE", admin)
        with (
            patch.object(宿主启动器, "核对运行环境身份", return_value=identity),
            patch.dict(
                inherited,
                {"XJ_DEVELOPMENT_ADMIN": "1"},
                clear=False,
            ),
        ):
            development_admin = 宿主启动器.构建正式环境(
                "admin", release, inherited=inherited,
            )
        self.assertEqual(
            development_admin["XJ_DEVELOPMENT_SOURCE"], str(development.resolve()),
        )
        self.assertEqual(control["PYTHONPATH"], str(self.source.resolve()))
        self.assertEqual(control["XJ_RELEASE_LOCK"], str(self.active.resolve()))
        self.assertEqual(control["XJ_HOST_DEPENDENCY_SHA256"], "a" * 64)

    def test_影子命令只引用固定启动器和预发布身份(self):
        staged = self.release_store / "staged" / self.version
        staged.parent.mkdir(parents=True)
        self.release.rename(staged)
        command, env = 宿主服务重启.构建影子启动命令(
            "control",
            {"version": self.version, "path": str(staged), "source": {"sha256": self.sha}},
            port=38654,
            status_file=self.root / "shadow-control.json",
        )

        self.assertEqual(command[:2], [
            str(宿主服务重启.HOST_PYTHON), str(宿主服务重启.HOST_LAUNCHER),
        ])
        self.assertIn("--allow-staged", command)
        self.assertEqual(command[command.index("--port") + 1], "38654")
        self.assertEqual(env["XJ_HOST_STATUS_FILE"], str((self.root / "shadow-control.json").resolve()))

    def test_旧启动项没有开发源变量时只给管理影子补当前开发源(self):
        development = self.root / "development"
        fake_module = development / "多用户" / "控制面" / "宿主服务重启.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# legacy imported module\n", encoding="utf-8")
        release = {
            "version": self.version,
            "path": str(self.release),
            "source": {"sha256": self.sha},
        }
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(宿主服务重启, "__file__", str(fake_module)),
        ):
            selected = 宿主服务重启._影子开发源({"mode": "legacy"})
            _, admin_env = 宿主服务重启.构建影子启动命令(
                "admin", release, port=38655,
                status_file=self.root / "shadow-admin.json",
                development_source=selected,
            )
            _, control_env = 宿主服务重启.构建影子启动命令(
                "control", release, port=38654,
                status_file=self.root / "shadow-control.json",
                development_source=selected,
            )
            _, rerank_env = 宿主服务重启.构建影子启动命令(
                "rerank", release, port=38657,
                status_file=self.root / "shadow-rerank.json",
                development_source=selected,
            )

        self.assertEqual(selected, development.resolve())
        self.assertEqual(admin_env["XJ_DEVELOPMENT_SOURCE"], str(development.resolve()))
        self.assertNotIn("XJ_DEVELOPMENT_SOURCE", control_env)
        self.assertNotIn("XJ_DEVELOPMENT_SOURCE", rerank_env)

    def test_正式影子缺少明确开发源时拒绝(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "缺少明确的开发源"):
                宿主服务重启._影子开发源({"mode": "formal"})

    def test_宿主切换每次都重新加载当前启动描述(self):
        calls: list[tuple[str, ...]] = []
        runtime = self.root / "runtime"
        fallback = self.root / "formal-fallback.plist"
        role_plist = self.root / "control.plist"
        fallback.write_text("fallback", encoding="utf-8")
        role_plist.write_text("role", encoding="utf-8")
        with (
            patch.object(宿主启动器, "RUNTIME", runtime),
            patch.object(宿主启动器, "FALLBACK_PLIST", fallback),
            patch.object(宿主启动器, "ROLE_PLISTS", {"control": role_plist}),
            patch.object(宿主启动器, "_launchctl", side_effect=lambda *args, **kwargs: calls.append(args)),
            patch.object(宿主启动器.time, "time", return_value=100.0),
            patch.object(宿主启动器.os, "getuid", return_value=501),
        ):
            result = 宿主启动器._启动新角色("control", migration=False)

        self.assertEqual(result, 100.0)
        self.assertEqual(calls[0][:3], ("bootout", "gui/501", str(fallback)))
        self.assertEqual(calls[1][:3], ("bootout", "gui/501", str(role_plist)))
        self.assertEqual(calls[2][:3], ("bootstrap", "gui/501", str(role_plist)))

    def test_切换失败可恢复旧封存版且清理所有角色状态(self):
        runtime = self.root / "runtime"
        fallback = self.root / "formal-fallback.plist"
        fallback.write_text("fallback", encoding="utf-8")
        role_plists = {
            role: self.root / f"{role}.plist"
            for role in 宿主启动器.ROLE_SETTINGS
        }
        for path in role_plists.values():
            path.write_text("role", encoding="utf-8")
        for role in 宿主启动器.ROLE_SETTINGS:
            status = runtime / "status" / f"{role}.json"
            status.parent.mkdir(parents=True, exist_ok=True)
            status.write_text("{}", encoding="utf-8")
        calls: list[tuple[str, ...]] = []
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        with (
            patch.object(宿主启动器, "RUNTIME", runtime),
            patch.object(宿主启动器, "ACTIVE_LOCK", self.active),
            patch.object(宿主启动器, "FALLBACK_PLIST", fallback),
            patch.object(宿主启动器, "ROLE_PLISTS", role_plists),
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(宿主启动器, "_正式角色列表", return_value=("control", "admin")),
            patch.object(宿主启动器, "_等待角色") as wait,
            patch.object(
                宿主启动器,
                "_launchctl",
                side_effect=lambda *args, **kwargs: calls.append(args),
            ),
            patch.object(宿主启动器.os, "getuid", return_value=501),
        ):
            result = 宿主启动器._恢复冻结正式单进程(
                ["control"], version=self.version, source_sha256=self.sha,
            )

        self.assertEqual(result, "已从激活的冻结正式副本恢复三端口")
        self.assertEqual(wait.call_count, 2)
        self.assertIn(("bootstrap", "gui/501", str(fallback)), calls)
        for role in 宿主启动器.ROLE_SETTINGS:
            self.assertFalse((runtime / "status" / f"{role}.json").exists())

    def test_活动正式版恢复会回到独立宿主(self):
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        starts = iter((101.0, 102.0, 103.0))
        with (
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(
                宿主启动器,
                "_正式角色列表",
                return_value=("control", "admin", "rerank"),
            ),
            patch.object(
                宿主启动器,
                "_启动新角色",
                side_effect=lambda role, **_kwargs: next(starts),
            ) as start,
            patch.object(宿主启动器, "_等待角色") as wait,
            patch.object(宿主启动器, "_恢复冻结正式单进程") as fallback,
        ):
            result = 宿主启动器.恢复活动正式后台()

        self.assertEqual(
            [item.args[0] for item in start.call_args_list],
            ["control", "rerank", "admin"],
        )
        self.assertEqual(wait.call_count, 3)
        fallback.assert_not_called()
        self.assertEqual(result["mode"], "formal")

    def test_独立宿主恢复失败时备用进程只保服务但仍报失败(self):
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        with (
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(
                宿主启动器,
                "_正式角色列表",
                return_value=("control", "admin", "rerank"),
            ),
            patch.object(
                宿主启动器,
                "_启动新角色",
                side_effect=宿主启动器.宿主启动错误("admin 启动失败"),
            ),
            patch.object(
                宿主启动器,
                "_恢复冻结正式单进程",
                return_value="已从激活的冻结正式副本恢复三端口",
            ) as fallback,
        ):
            with self.assertRaisesRegex(宿主启动器.宿主启动错误, "独立正式宿主恢复失败"):
                宿主启动器.恢复活动正式后台()

        fallback.assert_called_once_with(
            [], version=self.version, source_sha256=self.sha,
        )

    def test_正式三角色切换失败也回退到当前正式副本(self):
        state = {
            "schema": 1,
            "switch_id": "switch-test",
            "mode": "formal-switch",
            "status": "queued",
            "target": {"version": self.version, "source_sha256": self.sha},
        }
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        transition: list[dict] = []
        with (
            patch.object(宿主启动器, "_读JSON", return_value=state),
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(
                宿主启动器, "_切换状态",
                side_effect=lambda switch_id, **changes: transition.append(changes) or {**state, **changes},
            ),
            patch.object(
                宿主启动器, "_启动新角色",
                side_effect=宿主启动器.宿主启动错误("控制服务启动失败"),
            ),
            patch.object(
                宿主启动器, "_恢复冻结正式单进程",
                return_value="已从激活的冻结正式副本恢复三端口",
            ) as fallback,
        ):
            with self.assertRaises(宿主启动器.宿主启动错误):
                宿主启动器.执行宿主切换("switch-test")

        fallback.assert_called_once_with([], version=self.version, source_sha256=self.sha)
        self.assertEqual(transition[-1]["fallback"], "已从激活的冻结正式副本恢复三端口")

    def test_失败目标先移出正式目录再启动旧后台(self):
        store = self.root / "store"
        version = "twilight-release-failed"
        source_sha256 = "c" * 64
        published = store / "published" / version
        published.mkdir(parents=True)
        (published / "manifest.json").write_text(
            json.dumps({
                "schema": 2,
                "version": version,
                "source": {"sha256": source_sha256},
            }),
            encoding="utf-8",
        )
        state = {
            "target": {
                "version": version,
                "source_sha256": source_sha256,
                "release_path": str(published),
            },
        }
        with patch.object(宿主启动器, "ACTIVE_LOCK", self.root / "missing-active.json"):
            result = 宿主启动器._退回未完成正式副本(state)

        self.assertEqual(result, "未完成正式副本已退回预发布区")
        self.assertFalse(published.exists())
        self.assertTrue((store / "staged" / version / "manifest.json").is_file())

    def test_管理端终止切换时助手立即拒绝继续(self):
        for status in ("queued", "switching"):
            state = {
                "schema": 1,
                "switch_id": "switch-test",
                "status": status,
            }
            with self.subTest(status=status), patch.object(
                宿主启动器, "_读JSON", return_value=state,
            ):
                self.assertEqual(
                    宿主启动器._核对切换仍有效("switch-test")["status"], status,
                )

        for status, message in (("failed", "管理端终止"), ("host-ready", "不可继续")):
            state = {
                "schema": 1,
                "switch_id": "switch-test",
                "status": status,
            }
            with self.subTest(status=status), patch.object(
                宿主启动器, "_读JSON", return_value=state,
            ):
                with self.assertRaisesRegex(宿主启动器.宿主启动错误, message):
                    宿主启动器._核对切换仍有效("switch-test")

    def test_管理端标记失败后宿主助手负责恢复旧运行面(self):
        queued = {
            "schema": 1,
            "switch_id": "switch-test",
            "mode": "formal-switch",
            "status": "queued",
            "target": {"version": self.version, "source_sha256": self.sha},
        }
        failed = {**queued, "status": "failed", "error": "管理端已恢复旧运行面"}
        reads = iter((queued, failed, failed, failed))
        transitions: list[dict] = []
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        with (
            patch.object(宿主启动器, "_读JSON", side_effect=lambda path: next(reads)),
            patch.object(
                宿主启动器,
                "_切换状态",
                side_effect=lambda switch_id, **changes: transitions.append(changes)
                or {**failed, **changes},
            ),
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(宿主启动器, "_恢复暂存宿主运行面") as restore,
            patch.object(
                宿主启动器,
                "_恢复冻结正式单进程",
                return_value="已从激活的冻结正式副本恢复三端口",
            ) as fallback,
            patch.object(宿主启动器, "_启动新角色") as start,
        ):
            with self.assertRaises(宿主启动器.宿主启动错误):
                宿主启动器.执行宿主切换("switch-test")

        start.assert_not_called()
        restore.assert_called_once_with("switch-test")
        fallback.assert_called_once_with([], version=self.version, source_sha256=self.sha)
        self.assertEqual(transitions[-1]["status"], "failed")

    def test_切换编号被新助手接管后旧助手只退出(self):
        queued = {
            "schema": 1,
            "switch_id": "switch-test",
            "mode": "formal-switch",
            "status": "queued",
            "target": {"version": self.version, "source_sha256": self.sha},
        }
        switching = {**queued, "status": "switching"}
        newer = {**switching, "switch_id": "switch-new"}
        reads = iter((queued, switching, switching, newer))
        active = {
            "version": self.version,
            "source_sha256": self.sha,
            "source_path": str(self.source),
        }
        with (
            patch.object(宿主启动器, "_读JSON", side_effect=lambda path: next(reads)),
            patch.object(宿主启动器, "_切换状态", return_value=switching),
            patch.object(宿主启动器, "解析活动发布", return_value=active),
            patch.object(宿主启动器, "_启动新角色", side_effect=宿主启动器.宿主启动错误("启动失败")),
            patch.object(宿主启动器, "_恢复暂存宿主运行面") as restore,
            patch.object(宿主启动器, "_恢复冻结正式单进程") as fallback,
        ):
            self.assertIsNone(宿主启动器.执行宿主切换("switch-test"))

        restore.assert_not_called()
        fallback.assert_not_called()

    def test_切换已完成后旧助手只退出(self):
        completed = {
            "schema": 1,
            "switch_id": "switch-test",
            "mode": "formal-switch",
            "status": "completed",
            "target": {"version": self.version, "source_sha256": self.sha},
        }
        reads = iter((completed, completed, completed))
        with (
            patch.object(宿主启动器, "_读JSON", side_effect=lambda path: next(reads)),
            patch.object(宿主启动器, "_恢复暂存宿主运行面") as restore,
            patch.object(宿主启动器, "_恢复冻结正式单进程") as fallback,
        ):
            self.assertIsNone(宿主启动器.执行宿主切换("switch-test"))

        restore.assert_not_called()
        fallback.assert_not_called()

    def test_切换记录不可读时旧助手不碰运行面(self):
        queued = {
            "schema": 1,
            "switch_id": "switch-test",
            "mode": "formal-switch",
            "status": "queued",
            "target": {"version": self.version, "source_sha256": self.sha},
        }
        reads = iter((queued, queued, 宿主启动器.宿主启动错误("切换记录无法读取")))

        def read(_path):
            item = next(reads)
            if isinstance(item, BaseException):
                raise item
            return item

        with (
            patch.object(宿主启动器, "_读JSON", side_effect=read),
            patch.object(宿主启动器, "_启动新角色") as start,
            patch.object(宿主启动器, "_恢复暂存宿主运行面") as restore,
            patch.object(宿主启动器, "_恢复冻结正式单进程") as fallback,
        ):
            self.assertIsNone(宿主启动器.执行宿主切换("switch-test"))

        start.assert_not_called()
        restore.assert_not_called()
        fallback.assert_not_called()

    def test_角色重启顺序固定且管理端最后(self):
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)

        with patch.object(宿主服务重启.os, "getuid", return_value=501):
            宿主服务重启.触发角色重启(("control", "rerank", "admin"), runner=run)

        self.assertEqual(
            [command[-1] for command in commands],
            [
                "gui/501/com.xj.multiuser.control",
                "gui/501/com.xj.multiuser.rerank",
                "gui/501/com.xj.multiuser.admin",
            ],
        )

    def test_运行身份拒绝开发目录模块(self):
        development = self.root / "development"
        development.mkdir()
        status_root = self.root / "status"
        status_root.mkdir()
        files = {}
        for pid, role in enumerate(宿主服务重启.LABELS, start=1001):
            path = status_root / f"{role}.json"
            path.write_text(json.dumps({
                "status": "running",
                "role": role,
                "pid": pid,
                "version": self.version,
                "source": str(self.source),
                "source_sha256": self.sha,
                "module": str(self.source / "多用户" / "控制面" / "__main__.py"),
                "python_version": "3.test",
                "dependency_sha256": "a" * 64,
            }), encoding="utf-8")
            files[role] = path
        with (
            patch.object(宿主服务重启, "ACTIVE_LOCK", self.active),
            patch.dict(os.environ, {"XJ_DEVELOPMENT_SOURCE": str(development)}),
            patch.dict(宿主服务重启.ROLE_STATUS, files, clear=True),
            patch.object(
                宿主服务重启.httpx, "get",
                return_value=SimpleNamespace(status_code=200),
            ),
        ):
            result = 宿主服务重启.核对运行版本(
                self.version, self.sha, roles=tuple(宿主服务重启.LABELS),
            )
        self.assertEqual(set(result), set(宿主服务重启.LABELS))

        bad = json.loads(files["control"].read_text(encoding="utf-8"))
        bad["module"] = str(development / "多用户" / "控制面" / "__main__.py")
        files["control"].write_text(json.dumps(bad), encoding="utf-8")
        with (
            patch.object(宿主服务重启, "ACTIVE_LOCK", self.active),
            patch.dict(os.environ, {"XJ_DEVELOPMENT_SOURCE": str(development)}),
            patch.dict(宿主服务重启.ROLE_STATUS, files, clear=True),
            patch.object(
                宿主服务重启.httpx, "get",
                return_value=SimpleNamespace(status_code=200),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "control"):
                宿主服务重启.核对运行版本(
                    self.version, self.sha, roles=tuple(宿主服务重启.LABELS),
                )

        fallback = json.loads(files["control"].read_text(encoding="utf-8"))
        fallback["module"] = str(self.source / "多用户" / "控制面" / "__main__.py")
        fallback["fallback"] = "formal-single-process"
        files["control"].write_text(json.dumps(fallback), encoding="utf-8")
        with (
            patch.object(宿主服务重启, "ACTIVE_LOCK", self.active),
            patch.dict(os.environ, {"XJ_DEVELOPMENT_SOURCE": str(development)}),
            patch.dict(宿主服务重启.ROLE_STATUS, files, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "control"):
                宿主服务重启.核对运行版本(
                    self.version, self.sha, roles=tuple(宿主服务重启.LABELS),
                )

    def test_身份文件仍在但管理入口已退出时拒绝通过(self):
        status_root = self.root / "status"
        status_root.mkdir()
        files = {}
        for pid, role in enumerate(宿主服务重启.LABELS, start=2001):
            path = status_root / f"{role}.json"
            path.write_text(json.dumps({
                "status": "running",
                "role": role,
                "pid": pid,
                "version": self.version,
                "source": str(self.source),
                "source_sha256": self.sha,
                "module": str(self.source / "多用户" / "控制面" / "__main__.py"),
                "python_version": "3.test",
                "dependency_sha256": "a" * 64,
            }), encoding="utf-8")
            files[role] = path

        def health(url, **kwargs):
            return SimpleNamespace(status_code=503 if ":37655/" in url else 200)

        with (
            patch.object(宿主服务重启, "ACTIVE_LOCK", self.active),
            patch.dict(os.environ, {"XJ_DEVELOPMENT_SOURCE": str(self.root / "development")}),
            patch.dict(宿主服务重启.ROLE_STATUS, files, clear=True),
            patch.object(宿主服务重启.httpx, "get", side_effect=health),
        ):
            with self.assertRaisesRegex(RuntimeError, "admin 正式入口"):
                宿主服务重启.核对运行版本(
                    self.version, self.sha, roles=tuple(宿主服务重启.LABELS),
                )


if __name__ == "__main__":
    unittest.main()

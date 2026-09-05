from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from 多用户.部署.候选验收 import (
    候选步骤错误,
    安全操作日志,
    执行代码验收,
    检查镜像归档空间,
    最小验收环境,
    镜像归档空间预算,
    _绝对但不解析链接,
)


class 候选验收安全测试(unittest.TestCase):
    def test_八步验收只改一次性沙箱而不改只读候选(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "source"
            tools = root / "工具"
            pending = root / "待验收"
            tools.mkdir(parents=True)
            pending.mkdir()
            (tools / "示例.py").write_text("答案 = 42\n", encoding="utf-8")
            marker = pending / ".gitkeep"
            marker.write_text("原件\n", encoding="utf-8")
            marker.chmod(0o444)
            pending.chmod(0o555)
            (tools / "示例.py").chmod(0o444)
            tools.chmod(0o555)
            root.chmod(0o555)
            try:
                steps = (
                    (
                        "compile", "Python 语法检查",
                        [sys.executable, "-m", "compileall", "-q", "工具"], 30,
                    ),
                    (
                        "write", "模拟回归和前端写入",
                        [
                            sys.executable, "-c",
                            "from pathlib import Path; "
                            "Path('待验收/.gitkeep').write_text('沙箱\\n'); "
                            "Path('前端/node_modules').mkdir(parents=True)",
                        ],
                        30,
                    ),
                )
                with (
                    安全操作日志(base / "release.log") as log,
                    patch("多用户.部署.候选验收.代码验收步骤", return_value=steps),
                ):
                    results = 执行代码验收(
                        root,
                        tools={"python": sys.executable, "npm": "npm", "zsh": "zsh"},
                        env=最小验收环境({"PATH": os.environ.get("PATH", "/usr/bin:/bin")}),
                        log=log,
                    )
            finally:
                root.chmod(0o755)
                tools.chmod(0o755)
                pending.chmod(0o755)
                marker.chmod(0o644)

            self.assertEqual(results[0]["status"], "passed")
            self.assertEqual(results[1]["status"], "passed")
            self.assertEqual(marker.read_text(encoding="utf-8"), "原件\n")
            self.assertFalse((root / "前端").exists())
            self.assertFalse(any(root.rglob("__pycache__")))

    def test_验收保留虚拟环境启动器而不解析成系统解释器(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "python3"
            link.symlink_to(sys.executable)
            self.assertEqual(_绝对但不解析链接(str(link)), link)

    def test_六镜像归档空间使用动态预算(self) -> None:
        budget = 镜像归档空间预算([100] * 6)
        self.assertEqual(budget["images_total_bytes"], 600)
        self.assertGreaterEqual(budget["required_bytes"], 4 * 1024 ** 3)

    def test_动态预算不足时结构化失败(self) -> None:
        with patch("多用户.部署.候选验收.shutil.disk_usage") as usage:
            usage.return_value = os.statvfs("/")
            usage.return_value = type("Usage", (), {"free": 1})()
            with self.assertRaises(候选步骤错误) as caught:
                检查镜像归档空间(Path("/tmp"), [1024] * 6)
        self.assertEqual(caught.exception.details["phase"], "image-archive")
        self.assertEqual(caught.exception.details["retry_class"], "environment")

    def test_最小环境不继承任何业务密钥或后台通行证(self) -> None:
        source = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp/release-home",
            "LANG": "zh_CN.UTF-8",
            "XJ_DOCKER_BIN": "/usr/local/bin/docker",
            "XJ_SUPERVISOR_TOKEN": "supervisor-token-must-not-leak",
            "XJ_RERANK_TOKEN": "rerank-token-must-not-leak",
            "BAILIAN_API_KEY": "sk-business-key-must-not-leak",
            "DATABASE_URL": "postgres://user:password@example.invalid/db",
            "XJ_REGISTRY_PATH": "/real/registry.sqlite3",
        }

        env = 最小验收环境(source)

        self.assertEqual(env["XJ_DOCKER_BIN"], "/usr/local/bin/docker")
        self.assertEqual(env["CI"], "1")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        for forbidden in (
            "XJ_SUPERVISOR_TOKEN",
            "XJ_RERANK_TOKEN",
            "BAILIAN_API_KEY",
            "DATABASE_URL",
            "XJ_REGISTRY_PATH",
        ):
            self.assertNotIn(forbidden, env)
        self.assertFalse(any("must-not-leak" in value for value in env.values()))

    def test_操作日志统一脱敏且文件权限只有当前用户可读写(self) -> None:
        known_secret = "known-secret-value-123456"
        raw_values = (
            known_secret,
            "bearer-secret-value",
            "sk-abcdefghijklmnopqrstuvwxyz",
            "plain-api-key-value",
            "command-password-value",
            "url-password-value",
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "release.log"
            with 安全操作日志(path, known_secrets=(known_secret,)) as log:
                log.write(
                    f"known={known_secret}\n"
                    "Authorization: Bearer bearer-secret-value\n"
                    "provider=sk-abcdefghijklmnopqrstuvwxyz\n"
                    "api_key=plain-api-key-value\n"
                    "command --password command-password-value\n"
                    "https://operator:url-password-value@example.invalid/path\n"
                )

            content = path.read_text(encoding="utf-8")
            summary = log.摘要(complete=True)

            for raw in raw_values:
                self.assertNotIn(raw, content)
            self.assertIn("[已隐藏]", content)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["bytes"], path.stat().st_size)
            self.assertEqual(summary["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_失败步骤返回可定位且不含密钥的结构化结果(self) -> None:
        secret = "structured-secret-value-123456"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "release.log"
            env = 最小验收环境({
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": td,
                "API_KEY": secret,
            })
            with 安全操作日志(path, known_secrets=(secret,)) as log:
                with self.assertRaises(候选步骤错误) as raised:
                    log.运行(
                        [
                            sys.executable,
                            "-c",
                            (
                                "print('FAIL: 数据契约不匹配'); "
                                f"print('api_key={secret}'); "
                                "raise SystemExit(7)"
                            ),
                        ],
                        cwd=root,
                        env=env,
                        phase="acceptance",
                        label="多用户单元测试",
                        retry_class="source-fix",
                        timeout=10,
                    )

            details = raised.exception.details
            self.assertEqual(details["phase"], "acceptance")
            self.assertEqual(details["step"], "多用户单元测试")
            self.assertEqual(details["exit_code"], 7)
            self.assertEqual(details["retry_class"], "source-fix")
            self.assertIn("数据契约不匹配", details["failed_checks"])
            self.assertIn("未触碰任何账号", details["account_effect"])
            self.assertNotIn(secret, details["log_tail"])
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

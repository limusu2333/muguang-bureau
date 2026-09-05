"""One-shot, networkless validation runners started by the host supervisor."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import Any


_名字 = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
_账户编号 = re.compile(r"acc_[0-9a-z]{26}")
_输出上限 = 1024 * 1024


def _限量读(pipe, output: bytearray) -> None:
    try:
        for chunk in iter(lambda: pipe.read(64 * 1024), b""):
            remaining = _输出上限 + 1 - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
    finally:
        pipe.close()


class 执行拒绝(RuntimeError):
    pass


def _相对目录(value: str) -> str:
    raw = str(value or ".")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in raw) or len(raw) > 512:
        raise 执行拒绝("工作目录无效")
    return path.as_posix()


def 验证命令(command: str) -> tuple[list[str], str]:
    if not command or len(command) > 4096 or any(ord(c) < 32 and c not in "\t" for c in command):
        raise 执行拒绝("命令为空、过长或含控制字符")
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise 执行拒绝("命令无法解析") from exc
    if not parts:
        raise 执行拒绝("命令为空")
    program = PurePosixPath(parts[0]).name
    rest = parts[1:]
    if program in {"python", "python3"}:
        if rest[:2] == ["-m", "pytest"] and len(rest) >= 2:
            return ["python3", *rest], "pytest"
        if rest[:2] == ["-m", "unittest"] and len(rest) >= 3:
            return ["python3", *rest], "unittest"
        if rest[:2] in (["-m", "py_compile"], ["-m", "compileall"]) and len(rest) >= 3:
            return ["python3", *rest], "python编译"
        if rest == ["测试/回归.py"]:
            return ["python3", "测试/回归.py"], "公司回归"
    elif program == "pytest":
        return ["python3", "-m", "pytest", *rest], "pytest"
    elif program == "npm" and rest in (["run", "build"], ["run", "typecheck"], ["test"]):
        return ["npm", *rest], "前端验证"
    elif program == "tsc" and rest == ["--noEmit"]:
        return ["tsc", "--noEmit"], "前端类型检查"
    raise 执行拒绝("命令不在只读验收白名单")


class 一次性执行器:
    def __init__(self) -> None:
        self.docker = os.environ.get("XJ_DOCKER_BIN", "docker")
        self.timeout = max(10, min(int(os.environ.get("XJ_RUNNER_TIMEOUT", "180")), 600))
        self.policy_root = Path(os.environ.get("XJ_POLICY_DIR", "/var/xj/policy")).resolve()

    def 执行(self, account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        actual, kind = 验证命令(str(payload.get("command") or ""))
        root = str(payload.get("root") or "")
        if root not in {"code", "work"}:
            raise 执行拒绝("执行根只允许 code 或 work")
        rel_wd = _相对目录(str(payload.get("working_directory") or "."))
        image = str(account.get("runner_image_ref") or "")
        if not image or len(image) > 512 or any(c.isspace() for c in image):
            raise 执行拒绝("注册表 runner 镜像无效")
        volume = str(account.get("work_volume") or "")
        if root == "work" and not _名字.fullmatch(volume):
            raise 执行拒绝("注册表工作卷无效")

        account_id = str(account["account_id"]).lower()
        if not _账户编号.fullmatch(account_id):
            raise 执行拒绝("注册表账户编号无效")
        name = f"xj-runner-{account_id[-8:]}-{token_hex(4)}"
        source = "/app" if root == "code" else "/source"
        script = (
            "set -eu; cp -R \"$1\"/. /runner/; shift; "
            "if [ -d /policy ]; then cp -R /policy/. /runner/; fi; "
            "cd \"/runner/$1\"; shift; exec \"$@\""
        )
        command = [
            self.docker, "run", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "128", "--memory", "1g", "--cpus", "1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            "--tmpfs", "/runner:rw,nosuid,nodev,exec,size=1g",
            "--env", "HOME=/tmp/home", "--env", "TMPDIR=/tmp", "--env", "XJ_MOCK=1",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONNOUSERSITE=1",
        ]
        if root == "code":
            policy = self.policy_root / account_id
            if not policy.is_dir():
                raise 执行拒绝("账户制度目录不存在")
            command.extend(["--mount", f"type=bind,source={policy},target=/policy,readonly"])
        if root == "work":
            command.extend(["--mount", f"type=volume,source={volume},target=/source,readonly"])
        command.extend([image, "/bin/sh", "-c", script, "runner", source, rel_wd, *actual])

        started = time.monotonic()
        out, err = bytearray(), bytearray()
        readers: list[threading.Thread] = []
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(target=_限量读, args=(process.stdout, out), daemon=True),
                threading.Thread(target=_限量读, args=(process.stderr, err), daemon=True),
            ]
            for reader in readers:
                reader.start()
            try:
                code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                subprocess.run(
                    [self.docker, "rm", "-f", name], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
                )
                raise 执行拒绝(f"验收执行超过 {self.timeout} 秒，已终止") from exc
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                raise 执行拒绝("验收输出管道未能正常关闭")
        except FileNotFoundError as exc:
            raise 执行拒绝("宿主找不到 Docker 命令") from exc
        finally:
            try:
                subprocess.run(
                    [self.docker, "rm", "-f", name], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        truncated = len(out) > _输出上限 or len(err) > _输出上限
        return {
            "exit_code": int(code),
            "stdout": bytes(out[:_输出上限]).decode("utf-8", errors="replace"),
            "stderr": bytes(err[:_输出上限]).decode("utf-8", errors="replace"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "kind": kind,
            "truncated": truncated,
        }

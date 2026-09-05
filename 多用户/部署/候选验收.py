"""Run release checks with one explicit environment and one safe operation log."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


_环境白名单 = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL")
_敏感环境名 = re.compile(r"(?i)(?:token|secret|password|api.?key|credential|authorization)")
_敏感文本 = (
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:sk-|xj(?:rt|ct|sa)_)[A-Za-z0-9._~-]{12,}"),
    re.compile(
        r"(?i)\b(token|secret|password|api[_-]?key|authorization)"
        r"(\s*(?:=|:)\s*)([^\s,;]+)"
    ),
    re.compile(r"(?i)(--(?:token|password|api-key)(?:=|\s+))([^\s]+)"),
    re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)"),
)
_最大日志字节 = 4 * 1024 * 1024
_最小可用空间 = 4 * 1024 * 1024 * 1024
_归档固定余量 = 2 * 1024 * 1024 * 1024


def _现在() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class 候选步骤错误(RuntimeError):
    def __init__(self, summary: str, details: dict[str, object]) -> None:
        super().__init__(summary)
        self.details = details


def 最小验收环境(source: dict[str, str] | None = None, *, docker_bin: str = "") -> dict[str, str]:
    original = os.environ if source is None else source
    env = {name: original[name] for name in _环境白名单 if original.get(name)}
    env.update({
        "CI": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    })
    selected_docker = docker_bin or str(original.get("XJ_DOCKER_BIN") or "").strip()
    if selected_docker:
        env["XJ_DOCKER_BIN"] = selected_docker
    return env


def _脱敏(text: str, known_secrets: Iterable[str]) -> str:
    value = text
    for secret in known_secrets:
        if secret and len(secret) >= 6:
            value = value.replace(secret, "[已隐藏]")
    value = _敏感文本[0].sub(r"\1[已隐藏]", value)
    value = _敏感文本[1].sub("[已隐藏]", value)
    value = _敏感文本[2].sub(r"\1\2[已隐藏]", value)
    value = _敏感文本[3].sub(r"\1[已隐藏]", value)
    return _敏感文本[4].sub(r"\1[已隐藏]\3", value)


def _失败检查(output: str) -> list[str]:
    found: list[str] = []
    patterns = (
        re.compile(r"^(?:FAIL|ERROR):\s+(.+)$"),
        re.compile(r"^\s*[❌xX]\s+(.+)$"),
    )
    for line in output.splitlines():
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                item = match.group(1).strip()
                if item and item not in found:
                    found.append(item[:300])
                break
        if len(found) >= 12:
            break
    return found


def _失败摘要(label: str, output: str, exit_code: int) -> str:
    match = re.search(r"FAILED \(([^\n)]+)\)", output)
    if match:
        counts: list[str] = []
        for name, value in re.findall(r"(failures|errors)=(\d+)", match.group(1)):
            counts.append(f"{value}项{'失败' if name == 'failures' else '错误'}")
        if counts:
            return f"{label}未通过：{'、'.join(counts)}"
    return f"{label}未通过（退出码 {exit_code}）"


class 安全操作日志:
    def __init__(
        self,
        path: Path,
        *,
        known_secrets: Iterable[str] = (),
        mirror: bool = False,
    ) -> None:
        self.path = path.resolve()
        self.known_secrets = tuple(value for value in known_secrets if value)
        self.mirror = mirror
        self._handle = None
        self._bytes = 0
        self._lines = 0
        self._truncated = False
        self.tail: deque[str] = deque(maxlen=220)

    def __enter__(self) -> "安全操作日志":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._handle = self.path.open("w", encoding="utf-8")
        self.path.chmod(0o600)
        self.write(f"发布操作日志开始 {_现在()}\n")
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.write(f"发布操作日志结束 {_现在()}\n")
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None

    def write(self, text: str) -> None:
        safe = _脱敏(str(text), self.known_secrets)
        if safe:
            self.tail.extend(safe.splitlines())
            self._lines += safe.count("\n") or 1
        encoded = safe.encode("utf-8", errors="replace")
        if self._handle is not None and not self._truncated:
            if self._bytes + len(encoded) <= _最大日志字节:
                self._handle.write(safe)
                self._handle.flush()
                self._bytes += len(encoded)
            else:
                marker = "\n[日志超过 4MB，后续内容只保留末尾摘要]\n"
                self._handle.write(marker)
                self._handle.flush()
                self._bytes += len(marker.encode("utf-8"))
                self._truncated = True
        if self.mirror:
            print(safe, end="" if safe.endswith("\n") else "\n", flush=True)

    def 摘要(self, *, complete: bool) -> dict[str, object]:
        if self._handle is not None:
            self._handle.flush()
        digest = hashlib.sha256()
        with self.path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "file": self.path.name,
            "sha256": digest.hexdigest(),
            "bytes": self.path.stat().st_size,
            "lines": self._lines,
            "truncated": self._truncated,
            "complete": complete,
        }

    def 运行(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        phase: str,
        label: str,
        retry_class: str,
        timeout: int,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        started = time.monotonic()
        self.write(f"\n===== {label} =====\n")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            summary = f"{label}无法启动：{type(exc).__name__}"
            details = self._错误详情(
                phase, label, summary, None, retry_class, "", started,
            )
            raise 候选步骤错误(summary, details) from exc

        output: list[str] = []
        output_size = 0
        pending_text = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def capture(text: str, *, final: bool = False) -> None:
            nonlocal output_size, pending_text
            pending_text += text
            lines = pending_text.splitlines(keepends=True)
            if not final and lines and not lines[-1].endswith(("\n", "\r")):
                pending_text = lines.pop()
            else:
                pending_text = ""
            if final and pending_text:
                lines.append(pending_text)
                pending_text = ""
            for line in lines:
                safe = _脱敏(line, self.known_secrets)
                output.append(safe)
                output_size += len(safe)
                if output_size > 256_000:
                    output[:] = output[-160:]
                    output_size = sum(map(len, output))
                self.write(line)

        def stop_process_group() -> None:
            if process.poll() is not None:
                return
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=5)

        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        last_heartbeat = time.monotonic()
        try:
            while True:
                for key, _mask in selector.select(timeout=0.5):
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if chunk:
                        capture(decoder.decode(chunk))
                now = time.monotonic()
                if heartbeat is not None and now - last_heartbeat >= 5:
                    heartbeat()
                    last_heartbeat = now
                if process.poll() is not None:
                    remaining = process.stdout.read()
                    if remaining:
                        capture(decoder.decode(remaining))
                    capture(decoder.decode(b"", final=True), final=True)
                    break
                if now - started > timeout:
                    stop_process_group()
                    summary = f"{label}超过 {timeout} 秒，已停止"
                    text = "".join(output)[-80_000:]
                    details = self._错误详情(
                        phase, label, summary, None, retry_class, text, started,
                    )
                    raise 候选步骤错误(summary, details)
        except BaseException:
            stop_process_group()
            raise
        finally:
            selector.close()
            process.stdout.close()

        text = "".join(output)[-80_000:]
        elapsed = round(time.monotonic() - started, 1)
        self.write(f"===== {label} {'通过' if process.returncode == 0 else '失败'}，耗时 {elapsed}s =====\n")
        if process.returncode != 0:
            summary = _失败摘要(label, text, int(process.returncode or 1))
            details = self._错误详情(
                phase, label, summary, int(process.returncode or 1), retry_class, text, started,
            )
            raise 候选步骤错误(summary, details)
        return {"label": label, "status": "passed", "elapsed_seconds": elapsed}

    def _错误详情(
        self,
        phase: str,
        label: str,
        summary: str,
        exit_code: int | None,
        retry_class: str,
        output: str,
        started: float,
    ) -> dict[str, object]:
        return {
            "phase": phase,
            "step": label,
            "summary": summary,
            "exit_code": exit_code,
            "failed_checks": _失败检查(output),
            "log_tail": "\n".join(self.tail)[-12000:],
            "next_action": (
                "恢复缺失的本机依赖后，重新生成候选。"
                if retry_class == "environment"
                else "修正这一步并提交代码后，重新生成候选。"
            ),
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": retry_class,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }


def _工具路径(name: str, env: dict[str, str]) -> str:
    path = shutil.which(name, path=env.get("PATH"))
    if not path:
        raise 候选步骤错误(
            f"发布环境缺少 {name}",
            {
                "phase": "environment",
                "step": "发布环境预检",
                "summary": f"发布环境缺少 {name}",
                "exit_code": None,
                "failed_checks": [name],
                "next_action": f"安装或恢复 {name} 后，重新生成候选。",
                "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                "retry_class": "environment",
            },
        )
    return path


def _绝对但不解析链接(value: str) -> Path:
    """保留虚拟环境启动器；解析 symlink 会把 venv 变成系统 Python。"""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def 镜像归档空间预算(image_sizes: Iterable[int]) -> dict[str, int]:
    sizes = [int(value) for value in image_sizes]
    if len(sizes) != 6 or any(value <= 0 for value in sizes):
        raise 候选步骤错误("无法计算六个候选镜像的归档空间", {
            "phase": "image-archive", "step": "归档空间预算",
            "summary": "无法计算六个候选镜像的归档空间", "exit_code": None,
            "failed_checks": ["镜像 Size"],
            "next_action": "确认 Docker 能返回六个镜像的完整信息后，重新生成候选。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "environment",
        })
    total = sum(sizes)
    # 保存归档约需一份镜像总量；额外 50% 覆盖临时层/元数据，再留 2GiB 系统余量。
    required = max(_最小可用空间, (total * 3 + 1) // 2 + _归档固定余量)
    return {
        "images_total_bytes": total,
        "required_bytes": required,
        "minimum_bytes": _最小可用空间,
    }


def 检查镜像归档空间(root: Path, image_sizes: Iterable[int]) -> dict[str, int]:
    budget = 镜像归档空间预算(image_sizes)
    free = shutil.disk_usage(root.resolve()).free
    if free < budget["required_bytes"]:
        required_gib = budget["required_bytes"] / (1024 ** 3)
        free_gib = free / (1024 ** 3)
        raise 候选步骤错误(
            f"候选镜像归档空间不足：需要约 {required_gib:.1f}GiB，当前 {free_gib:.1f}GiB",
            {
                "phase": "image-archive", "step": "归档空间预算",
                "summary": (
                    f"候选镜像归档空间不足：需要约 {required_gib:.1f}GiB，"
                    f"当前 {free_gib:.1f}GiB"
                ),
                "exit_code": None, "failed_checks": ["镜像归档磁盘空间"],
                "next_action": "释放足够磁盘空间后，重新生成候选。",
                "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                "retry_class": "environment",
            },
        )
    return {**budget, "free_bytes_before_archive": free}


def 预检发布环境(
    root: Path,
    *,
    python: str,
    env: dict[str, str],
    log: 安全操作日志,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, str]:
    root = root.resolve()
    if not root.is_dir() or not (root / "Makefile").is_file():
        raise 候选步骤错误("候选源码副本不完整", {
            "phase": "environment", "step": "发布环境预检", "summary": "候选源码副本不完整",
            "exit_code": None, "failed_checks": ["Makefile"],
            "next_action": "重新固定候选源码后再试。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "source-fix",
        })
    # 磁盘门槛不能在这里用一个固定数字猜。六个镜像实际 Size 确认后，
    # `检查镜像归档空间` 会按归档总量、临时层和固定余量重新计算。
    # `.venv/bin/python3` 通常是指向同目录解释器的 symlink。这里不能调用
    # resolve()，否则 Python 会失去 pyvenv.cfg，候选验收会误用系统环境。
    python_path = str(_绝对但不解析链接(python))
    if not Path(python_path).is_file() or not os.access(python_path, os.X_OK):
        raise 候选步骤错误("发布环境缺少可执行 Python", {
            "phase": "environment", "step": "发布环境预检", "summary": "发布环境缺少可执行 Python",
            "exit_code": None, "failed_checks": ["Python"],
            "next_action": "恢复项目 Python 环境后，重新生成候选。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "environment",
        })
    tools = {name: _工具路径(name, env) for name in ("git", "make", "node", "npm", "rg", "zsh")}
    docker = str(env.get("XJ_DOCKER_BIN") or _工具路径("docker", env)).strip()
    if not Path(docker).is_file() and shutil.which(docker, path=env.get("PATH")) is None:
        raise 候选步骤错误("发布环境中的 Docker 路径无效", {
            "phase": "environment", "step": "Docker", "summary": "发布环境中的 Docker 路径无效",
            "exit_code": None, "failed_checks": [docker],
            "next_action": "恢复 Docker 命令后，重新生成候选。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "environment",
        })
    tools.update({"python": python_path, "docker": docker})
    checks = (
        ("Python", [python_path, "--version"]),
        ("Git", [tools["git"], "--version"]),
        ("Make", [tools["make"], "--version"]),
        ("Node.js", [tools["node"], "--version"]),
        ("npm", [tools["npm"], "--version"]),
        ("ripgrep", [tools["rg"], "--version"]),
        ("zsh", [tools["zsh"], "--version"]),
        ("Docker 命令与服务", [docker, "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"]),
    )
    for label, command in checks:
        log.运行(
            command, cwd=root, env=env, phase="environment", label=f"预检：{label}",
            retry_class="environment", timeout=30, heartbeat=heartbeat,
        )
    return tools


def 代码验收步骤(root: Path, tools: dict[str, str]) -> tuple[tuple[str, str, list[str], int], ...]:
    python = tools["python"]
    npm = tools["npm"]
    zsh = tools["zsh"]
    return (
        ("compile", "Python 语法检查", [python, "-m", "compileall", "-q", "工具", "测试", "多用户"], 180),
        (
            "contracts", "公司结构契约",
            [python, "-c", "import sys; sys.path.insert(0,'工具'); import 部门,房间注册; "
             "p=部门.校验()+房间注册.校验(); print('契约自检通过' if not p else '\\n'.join(p)); "
             "raise SystemExit(bool(p))"],
            120,
        ),
        ("company-regression", "公司隔离回归", [python, "测试/回归.py"], 1200),
        (
            "multiuser-unit", "多用户单元测试",
            [python, "-m", "unittest", "discover", "-s", "测试", "-p", "test_*.py", "-v"],
            1200,
        ),
        ("frontend-install", "前端锁定依赖安装", [npm, "--prefix", "前端", "ci"], 900),
        ("frontend-build", "前端生产构建", [npm, "--prefix", "前端", "run", "build"], 900),
        ("office-script", "办公室启动脚本检查", [zsh, "-n", "打开办公室.command"], 60),
        ("backup-script", "备份启动脚本检查", [zsh, "-n", "备份公司记忆.command"], 60),
    )


def _准备可写代码验收副本(source: Path, destination: Path) -> Path:
    """复制不可变输入，只让一次性验收副本承接缓存、测试改写和前端产物。"""
    source = source.resolve()
    if not source.is_dir() or source.is_symlink():
        raise 候选步骤错误("候选源码副本无效", {
            "phase": "acceptance", "step": "准备代码验收沙箱",
            "summary": "候选源码副本无效", "exit_code": None,
            "failed_checks": [str(source)],
            "next_action": "重新固定候选源码后再试。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "source-fix",
        })
    try:
        shutil.copytree(
            source,
            destination,
            copy_function=shutil.copy2,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "node_modules", "dist", "__pycache__", "*.pyc",
            ),
        )
        for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts)):
            if path.is_symlink():
                raise OSError(f"验收源码包含符号链接：{path.relative_to(destination)}")
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.is_dir():
                path.chmod(mode | 0o700)
            elif path.is_file():
                path.chmod(mode | 0o600)
        destination.chmod(stat.S_IMODE(destination.stat().st_mode) | 0o700)
    except (OSError, shutil.Error) as exc:
        raise 候选步骤错误("无法建立可写代码验收沙箱", {
            "phase": "acceptance", "step": "准备代码验收沙箱",
            "summary": "无法建立可写代码验收沙箱", "exit_code": None,
            "failed_checks": [type(exc).__name__],
            "next_action": "检查本机临时目录和磁盘空间后重新生成候选。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "environment",
        }) from exc
    return destination


def 执行代码验收(
    root: Path,
    *,
    tools: dict[str, str],
    env: dict[str, str],
    log: 安全操作日志,
    on_step: Callable[[int, int, str, str], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> list[dict[str, object]]:
    steps = 代码验收步骤(root, tools)
    results: list[dict[str, object]] = []
    # 候选原件永久只读。回归测试、npm ci、前端构建和 compileall 都可能写文件，
    # 所以八个代码验收阶段只在一次性可写沙箱中运行，绝不放宽候选原件权限。
    with tempfile.TemporaryDirectory(prefix="xj-candidate-code-") as temporary:
        temporary_root = Path(temporary)
        workspace = _准备可写代码验收副本(root, temporary_root / "source")
        pycache = temporary_root / "pycache"
        pycache.mkdir(mode=0o700)
        step_env = dict(env)
        step_env["PYTHONPYCACHEPREFIX"] = str(pycache)
        for index, (code, label, command, timeout) in enumerate(steps, start=1):
            if on_step is not None:
                on_step(index, len(steps), code, label)
            result = log.运行(
                command, cwd=workspace, env=step_env, phase="acceptance", label=label,
                retry_class="source-fix", timeout=timeout, heartbeat=heartbeat,
            )
            results.append({"code": code, **result})
    return results


_服务就绪探针 = r"""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

url = sys.argv[1]
command = sys.argv[2:]
fixture = os.environ.get("XJ_SMOKE_INSTANCE_YAML", "")
if fixture:
    Path("/tmp/instance.yaml").write_text(fixture, encoding="utf-8")
process = subprocess.Popen(command, start_new_session=True)
deadline = time.monotonic() + 45
try:
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"服务入口提前退出，退出码 {code}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = response.read(4096)
                if response.status == 200:
                    print(f"隔离服务入口已响应：HTTP {response.status}，{len(payload)} 字节")
                    break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("服务入口在 45 秒内没有就绪")
finally:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
""".strip()

_隔离实例配置 = """\
schema_version: 1
account_id: acc_release_smoke
instance_id: inst_release_smoke
owner:
  owner_id: own_release_smoke
  display_name: 发布验收
  称呼: 验收人
  别名: [验收人]
公司目的: 只验证候选镜像能否在隔离环境启动
"""


def 隔离运行时检查步骤(
    docker: str,
    components: dict[str, dict[str, str]],
) -> tuple[dict[str, object], ...]:
    """Build commands that exercise real runtime entrypoints without real accounts or networks."""
    ids = {
        name: str((components.get(name) or {}).get("id") or "")
        for name in ("company", "runner", "platform")
    }
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in ids.values()):
        raise 候选步骤错误("候选运行时检查缺少精确镜像身份", {
            "phase": "runtime-smoke", "step": "准备隔离运行时检查",
            "summary": "候选运行时检查缺少精确镜像身份", "exit_code": None,
            "failed_checks": [name for name, value in ids.items() if not value],
            "next_action": "重新构建三个候选镜像后，再生成候选。",
            "account_effect": "未触碰任何账号；当前正式版本继续运行。",
            "retry_class": "source-fix",
        })
    common = [
        docker, "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", "128", "--memory", "1g", "--cpus", "1",
    ]
    company = [
        *common,
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700",
        "--env", "HOME=/tmp/home", "--env", "XJ_MODE=remote",
        "--env", "XJ_CODE_ROOT=/app", "--env", "XJ_DATA_ROOT=/tmp/data",
        "--env", "XJ_WORKSPACE_ROOT=/tmp/work", "--env", "XJ_INSTANCE_CONFIG=/tmp/instance.yaml",
        "--env", "XJ_OFFICE_PORT=8000", "--env", "XJ_MOCK=1",
        "--env", f"XJ_SMOKE_INSTANCE_YAML={_隔离实例配置}",
        ids["company"], "python3", "-c", _服务就绪探针,
        "http://127.0.0.1:8000/", "python3", "-u", "/app/工具/机房.py",
    ]
    runner_script = (
        "set -eu; cp -R /app/. /runner/; cd /runner; "
        "python3 -c \"from 多用户.控制面.执行器 import 验证命令; "
        "cmd,kind=验证命令('python3 -m unittest discover -s 测试 -p test_*.py'); "
        "assert kind == 'unittest' and cmd[0] == 'python3'\"; "
        "node --version >/dev/null; npm --version >/dev/null"
    )
    runner = [
        *common,
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
        "--tmpfs", "/runner:rw,nosuid,nodev,exec,size=512m,uid=10001,gid=10001,mode=0700",
        "--env", "HOME=/tmp/home", "--env", "XJ_MOCK=1",
        ids["runner"], "/bin/sh", "-c", runner_script,
    ]
    platform = [
        *common,
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,uid=10002,gid=10002,mode=0700",
        "--env", "HOME=/tmp/home", "--env", "XJ_LOCAL_TEST_MODE=1",
        "--env", "XJ_SUPERVISOR_TOKEN=release-smoke-token-0000000000000000",
        ids["platform"], "python3", "-c", _服务就绪探针,
        "http://127.0.0.1:8081/healthz", "python3", "-m", "多用户.网关.启动",
    ]
    isolation = {
        "network": "none", "real_accounts": False, "real_data": False,
        "real_credentials": False,
    }
    return (
        {
            "code": "company-entry", "label": "用户公司真实入口隔离启动",
            "command": company, "timeout": 90, "kind": "service-entry",
            "coverage": "启动机房 HTTP 主入口并读取首页",
            "boundary": "不加载真实账号卷、正式制度挂载、真实模型和统一搜索",
            "isolation": isolation,
        },
        {
            "code": "runner-entry", "label": "任务执行器隔离运行能力",
            "command": runner, "timeout": 90, "kind": "one-shot-runtime",
            "coverage": "按正式只读沙箱复制代码，加载命令白名单并核对 Python、Node.js、npm",
            "boundary": "任务执行器不是常驻服务；这里不执行用户项目或真实账号命令",
            "isolation": isolation,
        },
        {
            "code": "platform-entry", "label": "共享网关真实入口隔离启动",
            "command": platform, "timeout": 90, "kind": "service-entry",
            "coverage": "启动公开网关与控制网关，并读取控制健康接口",
            "boundary": "不连接监督服务、数据库、模型代理、搜索服务或公网入口",
            "isolation": isolation,
        },
    )


def 执行隔离运行时检查(
    docker: str,
    components: dict[str, dict[str, str]],
    *,
    cwd: Path,
    env: dict[str, str],
    log: 安全操作日志,
    on_step: Callable[[int, int, str], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> list[dict[str, object]]:
    checks = 隔离运行时检查步骤(docker, components)
    results: list[dict[str, object]] = []
    for index, check in enumerate(checks, start=1):
        label = str(check["label"])
        if on_step is not None:
            on_step(index, len(checks), label)
        result = log.运行(
            list(check["command"]), cwd=cwd, env=env, phase="runtime-smoke",
            label=label, retry_class="source-fix", timeout=int(check["timeout"]),
            heartbeat=heartbeat,
        )
        results.append({
            key: value for key, value in {**check, **result}.items()
            if key not in {"command", "timeout"}
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    root = args.root.resolve()
    source_env = os.environ.copy()
    docker = str(source_env.get("XJ_DOCKER_BIN") or "")
    env = 最小验收环境(source_env, docker_bin=docker)
    secrets_in_source = [value for name, value in source_env.items() if _敏感环境名.search(name)]
    with tempfile.TemporaryDirectory(prefix="xj-release-check-") as td:
        with 安全操作日志(Path(td) / "release-check.log", known_secrets=secrets_in_source, mirror=True) as log:
            try:
                tools = 预检发布环境(root, python=args.python, env=env, log=log)
                执行代码验收(root, tools=tools, env=env, log=log)
            except 候选步骤错误 as exc:
                print(f"发布验收失败：{exc}", file=sys.stderr)
                return 1
    print("发布验收全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

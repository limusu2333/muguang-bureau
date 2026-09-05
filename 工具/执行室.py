#!/usr/bin/env python3
"""执行室：在一次性副本和操作系统沙箱里运行受控验证命令。

这里不是通用终端。只接受 Python 编译/pytest/公司回归和前端 build/test/typecheck；
命令看见的是临时副本，网络被禁，环境变量使用显式最小集合，真实源码和密钥不暴露给子进程。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from 实例配置 import 主人称呼, 读取实例配置
from 根 import 产品根, 代码根, 工作根, 数据根, 是远程实例

COMPANY = 代码根
PRODUCT = 产品根
WORK = 工作根
记录目录 = 数据根 / "执行室" / "记录"
超时秒 = 180
输出上限 = 6000
_记录锁 = threading.Lock()


def _留痕(obj: dict) -> None:
    with _记录锁:
        记录目录.mkdir(parents=True, exist_ok=True)
        obj.setdefault("时间", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        p = 记录目录 / f"{dt.date.today()}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def _响应(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _拒(理由: str, 命令: str) -> ToolResponse:
    _留痕({"命令": 命令, "结果": "拒绝", "理由": 理由})
    return _响应(f"【执行室拒绝】{理由}\n需要扩大执行范围，先用 ask_owner 请示{主人称呼()}。")


def _工作根(wd: Path) -> tuple[Path, Path] | None:
    wd = wd.resolve()
    for root in dict.fromkeys((COMPANY.resolve(), WORK.resolve(), PRODUCT.resolve())):
        if root.exists() and wd.is_relative_to(root):
            return root, wd.relative_to(root)
    return None


def _远程运行(命令: str, kind: str, root: Path, rel_wd: Path) -> dict:
    cfg = 读取实例配置()
    account_id = str(cfg.get("account_id") or "")
    token = os.environ.get("XJ_CONTROL_TOKEN", "").strip()
    if not account_id or not token:
        raise RuntimeError("远程执行控制凭据缺失，执行室按失败关闭处理")
    if root == COMPANY.resolve():
        root_name = "code"
    else:
        root_name = "work"
    body = json.dumps({
        "command": 命令,
        "root": root_name,
        "working_directory": rel_wd.as_posix() if rel_wd.parts else ".",
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "http://control-gw:8081/run-check",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=超时秒 + 10) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"监督服务拒绝执行（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        raise RuntimeError("监督服务当前不可达") from e
    if len(raw) > 1024 * 1024:
        raise RuntimeError("监督服务响应超过 1MB 上限")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError("监督服务返回了无效响应") from e
    if not isinstance(result, dict) or not isinstance(result.get("exit_code"), int):
        raise RuntimeError("监督服务响应缺少有效退出码")
    return result


async def _远程响应(命令: str, kind: str, wd: Path, root: Path, rel_wd: Path) -> ToolResponse:
    try:
        result = await asyncio.to_thread(_远程运行, 命令, kind, root.resolve(), rel_wd)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        output = (stdout + (("\n[stderr]\n" + stderr) if stderr else "")).strip()
        if len(output) > 输出上限:
            output = output[:输出上限] + f"\n…（输出较长，已截断前 {输出上限} 字）"
        code = int(result["exit_code"])
        _留痕({
            "命令": 命令, "类别": kind, "目录": str(wd.resolve()),
            "隔离": "监督服务一次性runner+network=none", "退出码": code,
            "耗时毫秒": result.get("duration_ms"), "输出": output[:2000],
        })
        mark = "退出码 0" if code == 0 else f"退出码 {code}"
        return _响应(f"【执行室·远程隔离】{mark}\n$ {命令}\n{output or '（无输出）'}\n（命令由一次性断网 runner 执行，结束后已销毁。）")
    except Exception as e:  # noqa: BLE001
        _留痕({"命令": 命令, "类别": kind, "目录": str(wd.resolve()), "结果": f"远程执行失败 {type(e).__name__}: {e}"})
        return _响应(f"【执行室】远程隔离执行失败：{e}")


def _验证计划(parts: list[str]) -> tuple[list[str], str] | None:
    """返回可信可执行参数和命令类别；不接受任意解释器脚本、-c、npx、make 或文件工具。"""
    if not parts:
        return None
    program = Path(parts[0]).name
    rest = parts[1:]
    if program in ("python", "python3"):
        if rest[:2] == ["-m", "pytest"] and len(rest) >= 2:
            return [sys.executable, *rest], "pytest"
        if rest[:2] == ["-m", "unittest"] and len(rest) >= 3:
            return [sys.executable, *rest], "unittest"
        if rest[:2] in (["-m", "py_compile"], ["-m", "compileall"]) and len(rest) >= 3:
            return [sys.executable, *rest], "python编译"
        if rest and Path(rest[0]).as_posix() == "测试/回归.py" and len(rest) == 1:
            return [sys.executable, "测试/回归.py"], "公司回归"
        return None
    if program == "pytest":
        return [sys.executable, "-m", "pytest", *rest], "pytest"
    if program == "npm":
        if rest in (["run", "build"], ["run", "typecheck"], ["test"]):
            exe = shutil.which("npm")
            return ([exe, *rest], "前端验证") if exe else None
        return None
    if program == "tsc" and rest == ["--noEmit"]:
        exe = shutil.which("tsc")
        return ([exe, *rest], "前端类型检查") if exe else None
    return None


def _复制工作区(root: Path, target: Path) -> list[tuple[Path, Path]]:
    """复制源码与小型状态；排除真实密钥、运行数据、备份和依赖目录。返回只读依赖映射。"""
    ignore = shutil.ignore_patterns(
        ".env", ".git", ".venv", "node_modules", "dist", "__pycache__", "*.pyc",
        "运行状态", "90_备份", "附件", "scratchpad", "执行室",
    )
    shutil.copytree(root, target, ignore=ignore)
    deps: list[tuple[Path, Path]] = []
    candidates = [root / "node_modules", root / "前端" / "node_modules"]
    for src in candidates:
        if not src.is_dir():
            continue
        rel = src.relative_to(root)
        link = target / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(src, target_is_directory=True)
        deps.append((src.resolve(), link))
    return deps


def _拒读树(root: Path, 允许路径: list[Path], quote) -> str:
    """在一棵受保护目录里只留下明确依赖路径；其他兄弟文件和目录逐层拒读。"""
    root = root.resolve()
    allowed = [p.resolve() for p in 允许路径 if p.exists() and p.resolve().is_relative_to(root)]
    if not allowed:
        return f'(deny file-read* (subpath "{quote(root)}"))'
    rules: list[str] = []

    def walk(current: Path, targets: list[Path]) -> None:
        if any(t == current for t in targets):
            return
        try:
            children = list(current.iterdir())
        except OSError:
            return
        for child in children:
            related = [t for t in targets if t == child or t.is_relative_to(child)]
            if related:
                if child.is_dir() and not any(t == child for t in related):
                    walk(child, related)
                continue
            kind = "subpath" if child.is_dir() else "literal"
            rules.append(f'(deny file-read* ({kind} "{quote(child)}"))')

    walk(root, allowed)
    return "\n".join(rules)


def _profile(work: Path, read_only: list[Path], source_root: Path) -> str:
    def q(p: Path | str) -> str:
        return str(p).replace("\\", "\\\\").replace('"', '\\"')

    user_allowed = [Path(sys.prefix).resolve(), *read_only]
    home_denies = _拒读树(Path.home(), user_allowed, q)
    source_denies = _拒读树(source_root, user_allowed, q)
    return f"""(version 1)
(deny default)
(allow process*)
(allow signal)
(allow sysctl-read)
(allow mach*)
(allow ipc*)
(allow file-read-metadata)
(allow file-read*)
{home_denies}
{source_denies}
(allow file-write* (subpath "{q(work.resolve())}"))
(allow file-write-data (literal "/dev/null"))
"""


def _最小环境(work: Path) -> dict[str, str]:
    home = work / ".sandbox-home"
    tmp = work / ".sandbox-tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "XJ_MOCK": "1",
        "XJ_EXECUTION_SANDBOX": "1",
    }


async def run_command(命令: str, 工作目录: str = "") -> ToolResponse:
    """在一次性副本中运行受控验证。真实工作区不会被命令直接改写。"""
    命令 = str(命令 or "").strip()
    if not 命令:
        return _拒("空命令", 命令)
    try:
        parts = shlex.split(命令)
    except ValueError as e:
        return _拒(f"命令解析失败：{e}", 命令)
    plan = _验证计划(parts)
    if plan is None:
        return _拒("只允许 Python 编译、pytest/unittest、公司回归、npm build/test/typecheck 或 tsc --noEmit；不开放通用解释器和文件命令", 命令)
    actual, kind = plan
    wd = Path(工作目录).expanduser() if 工作目录 else COMPANY
    if not wd.is_absolute():
        wd = COMPANY / wd
    pair = _工作根(wd)
    if pair is None or not wd.resolve().is_dir():
        return _拒(f"工作目录必须是实例代码区或工作区内的已有目录：{wd.resolve()}", 命令)
    root, rel_wd = pair
    if 是远程实例():
        return await _远程响应(命令, kind, wd, root, rel_wd)
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        return _拒("本机没有可用的操作系统隔离器，执行室按失败关闭处理", 命令)

    try:
        with tempfile.TemporaryDirectory(prefix="xj-execution-") as td:
            temp_root = Path(td).resolve()
            snapshot = temp_root / root.name
            deps = await asyncio.to_thread(_复制工作区, root, snapshot)
            run_wd = snapshot / rel_wd
            profile = _profile(temp_root, [src for src, _ in deps], root)
            env = _最小环境(temp_root)
            proc = await asyncio.create_subprocess_exec(
                str(sandbox), "-p", profile, *actual,
                cwd=str(run_wd), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=超时秒)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    proc.kill()
                _留痕({"命令": 命令, "类别": kind, "目录": str(wd.resolve()), "结果": "超时"})
                return _响应(f"【执行室】命令超时（>{超时秒}s）已终止；真实工作区未被命令改写。")
            stdout = out_b.decode(errors="replace")
            stderr = err_b.decode(errors="replace")
            output = (stdout + (("\n[stderr]\n" + stderr) if stderr else "")).strip()
            if len(output) > 输出上限:
                output = output[:输出上限] + f"\n…（输出较长，已截断前 {输出上限} 字）"
            _留痕({
                "命令": 命令, "类别": kind, "目录": str(wd.resolve()), "隔离": "临时副本+网络禁用+最小环境",
                "退出码": proc.returncode, "输出": output[:2000],
            })
            mark = "✅ 退出码 0" if proc.returncode == 0 else f"❌ 退出码 {proc.returncode}"
            return _响应(f"【执行室·隔离副本】{mark}\n$ {命令}\n{output or '（无输出）'}\n（命令只改得到临时副本，结束后已销毁。）")
    except Exception as e:  # noqa: BLE001
        _留痕({"命令": 命令, "类别": kind, "目录": str(wd.resolve()), "结果": f"异常 {type(e).__name__}: {e}"})
        return _响应(f"【执行室】隔离执行失败 {type(e).__name__}: {e}")


def 执行室工具() -> FunctionTool:
    return FunctionTool(run_command, name="run_command")


if __name__ == "__main__":
    async def _selftest() -> None:
        for cmd in ("python3 -m py_compile 工具/状态存储.py", "python3 -c print(1)", "find . -delete", "npm run build"):
            result = await run_command(cmd, "前端" if cmd.startswith("npm") else "")
            block = result.content[0]
            print(block.get("text", "") if isinstance(block, dict) else getattr(block, "text", str(block)))
    asyncio.run(_selftest())

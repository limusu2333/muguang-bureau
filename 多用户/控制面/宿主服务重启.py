"""Request, switch and verify the three macOS host services."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx


RUNTIME = Path.home() / "Library" / "Application Support" / "XJ Multiuser"
STATUS_FILE = RUNTIME / "restart-status.json"
LOCK_FILE = RUNTIME / "restart.lock"
SWITCH_FILE = RUNTIME / "data" / "host-switch.json"
SWITCH_LOCK_FILE = RUNTIME / "host-switch.lock"
HOST_PYTHON = RUNTIME / "host-python" / "bin" / "python3"
HOST_LAUNCHER = RUNTIME / "launcher" / "host-launcher.py"
CANDIDATE_HOST_ROOT = RUNTIME / "candidate-host"
ACTIVE_LOCK = RUNTIME / "data" / "active-release.json"
LABELS = {
    "control": "com.xj.multiuser.control",
    "admin": "com.xj.multiuser.admin",
    "rerank": "com.xj.multiuser.rerank",
}
LEGACY_LABEL = "com.xj.multiuser.supervisor"
FALLBACK_LABEL = "com.xj.multiuser.formal-fallback"
PORTS = {"control": 37654, "admin": 37655, "rerank": 37657}
ROLE_STATUS = {role: RUNTIME / "status" / f"{role}.json" for role in LABELS}
PLISTS = {
    role: Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    for role, label in LABELS.items()
}
LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
FALLBACK_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{FALLBACK_LABEL}.plist"

# Compatibility for callers which previously restarted one combined process.
LABEL = LABELS["admin"]
_进行中 = {"排队中", "重启中"}
_超时秒 = 5 * 60
_候选编号 = re.compile(r"candidate-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")


def _现在() -> float:
    return time.time()


def _写状态(value: dict[str, Any]) -> dict[str, Any]:
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {**value, "updated_at": _现在()}
    fd, temporary = tempfile.mkstemp(prefix="restart-status-", suffix=".json", dir=RUNTIME)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATUS_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return payload


def _原子写JSON(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = dict(value)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return payload


@contextmanager
def _重启锁():
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _切换锁():
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with SWITCH_LOCK_FILE.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def 读取状态() -> dict[str, Any] | None:
    try:
        value = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("status") not in _进行中:
        return value
    try:
        helper_pid = int(value.get("helper_pid") or 0)
        requested_at = float(value.get("requested_at") or 0)
    except (TypeError, ValueError):
        helper_pid = 0
        requested_at = 0
    if requested_at <= 0 or _现在() - requested_at < 3:
        return value
    helper_alive = False
    if helper_pid > 0:
        try:
            os.kill(helper_pid, 0)
            helper_alive = True
        except (OSError, ProcessLookupError):
            pass
    if helper_alive:
        return value
    return _写状态({
        **value,
        "status": "失败",
        "stage": "后台重启没有开始",
        "message": "负责重启的后台维护程序已经退出",
        "completed_at": _现在(),
        "error": "后台维护程序异常退出；正式版本和账号没有改变",
    })


def 读取宿主切换() -> dict[str, Any] | None:
    try:
        value = json.loads(SWITCH_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == 1 else None


def 宿主助手存活(state: Mapping[str, Any] | None) -> bool:
    """判断异步宿主助手是否仍有机会完成当前切换。

    只有带有真实 PID、未超时且处于 queued/switching/host-ready 的状态才可等待；
    缺少 PID 的旧记录按不可恢复处理，避免把半切换运行面无限挂住。
    """
    if not isinstance(state, Mapping) or state.get("status") not in {
        "queued", "switching", "host-ready",
    }:
        return False
    try:
        pid = int(state.get("helper_pid") or 0)
        started = float(state.get("started_at") or state.get("requested_at") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or started <= 0 or _现在() - started >= _超时秒:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def 标记宿主切换失败(switch_id: str, error: str) -> dict[str, Any]:
    """通知宿主助手取消切换；助手随后负责恢复旧进程。"""
    state = 读取宿主切换()
    if not state or state.get("switch_id") != switch_id:
        raise RuntimeError("宿主切换记录不存在")
    if state.get("status") == "completed":
        return state
    return _原子写JSON(SWITCH_FILE, {
        **state,
        "status": "failed",
        "stage": "宿主切换已取消，等待助手恢复旧运行面",
        "completed_at": _现在(),
        "updated_at": _现在(),
        "error": str(error or "宿主切换失败")[:500],
    })


def 标记宿主切换等待收口(switch_id: str, reason: str) -> dict[str, Any]:
    """记录宿主已经就绪、但发布事务尚缺哪一项事实。"""
    with _切换锁():
        state = 读取宿主切换()
        if not state or state.get("switch_id") != switch_id:
            raise RuntimeError("宿主切换记录不存在")
        if state.get("status") != "host-ready":
            return state
        message = str(reason or "等待发布事务完成")[:500]
        if state.get("closeout_waiting") == message:
            return state
        return _原子写JSON(SWITCH_FILE, {
            **state,
            "stage": f"宿主已就绪，等待收口：{message}"[:500],
            "closeout_waiting": message,
            "updated_at": _现在(),
        })


def _错误文字(exc: BaseException) -> str:
    return (str(exc).strip() or type(exc).__name__)[:300]


def _仍在进行(value: dict[str, Any] | None) -> bool:
    if not value or value.get("status") not in _进行中:
        return False
    started = float(value.get("started_at") or value.get("requested_at") or 0)
    return started > 0 and _现在() - started < _超时秒


def _旧单进程模式() -> bool:
    """Only the explicitly marked legacy agent may use the development fallback."""
    if os.environ.get("XJ_HOST_BOUNDARY_MODE") == "legacy":
        return True
    # A frozen formal fallback has no role variable too, but it must never
    # be mistaken for the still-live development LaunchAgent merely because
    # that old plist is retained for audit/rollback.
    if os.environ.get("XJ_HOST_BOUNDARY_MODE") == "formal-fallback":
        return False
    return not os.environ.get("XJ_HOST_ROLE") and LEGACY_PLIST.is_file()


def _启动任务存在(label: str, *, runner=None) -> bool:
    execute = runner or subprocess.run
    try:
        result = execute(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def 当前正式运行模式(*, runner=None) -> str:
    """Inspect the loaded formal jobs instead of guessing from the caller."""
    fallback = _启动任务存在(FALLBACK_LABEL, runner=runner)
    legacy = _启动任务存在(LEGACY_LABEL, runner=runner)
    formal = {
        role: _启动任务存在(label, runner=runner)
        for role, label in LABELS.items()
    }
    loaded_formal = tuple(role for role, loaded in formal.items() if loaded)
    selected = sum((fallback, legacy, bool(loaded_formal)))
    if selected > 1 or loaded_formal and len(loaded_formal) != len(LABELS):
        raise RuntimeError("正式后台运行状态冲突，不能贸然重启")
    if fallback:
        return "formal-fallback"
    if legacy:
        return "legacy"
    if len(loaded_formal) == len(LABELS):
        return "formal"
    raise RuntimeError("没有找到正在运行的正式后台")


def 正在重启() -> bool:
    return _仍在进行(读取状态())


def _读取活动身份() -> dict[str, str]:
    try:
        value = json.loads(ACTIVE_LOCK.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取外部活动版本指针") from exc
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    result = {
        "version": str(value.get("version") or ""),
        "source_sha256": str(source.get("sha256") or ""),
        "release_path": str(value.get("release_path") or ""),
    }
    release_path = Path(result["release_path"]).expanduser().resolve()
    if (
        value.get("schema") != 3
        or not result["version"]
        or len(result["source_sha256"]) != 64
        or not release_path.is_dir()
        or release_path.parent.name != "published"
    ):
        raise RuntimeError("外部活动版本指针不完整")
    result["release_path"] = str(release_path)
    result["source_path"] = str((release_path / "source").resolve())
    return result


def _启动描述正确(role: str) -> bool:
    path = PLISTS[role]
    try:
        document = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return False
    arguments = document.get("ProgramArguments") if isinstance(document, dict) else None
    environment = document.get("EnvironmentVariables") if isinstance(document, dict) else None
    try:
        role_index = arguments.index("--role") if isinstance(arguments, list) else -1
        declared_role = arguments[role_index + 1] if role_index >= 0 else None
    except (IndexError, TypeError):
        return False
    return bool(
        isinstance(arguments, list)
        and arguments[:2] == [str(HOST_PYTHON), str(HOST_LAUNCHER)]
        and declared_role == role
        and isinstance(environment, dict)
        and environment.get("XJ_RELEASE_LOCK") == str(ACTIVE_LOCK)
    )


def _回退启动描述正确() -> bool:
    try:
        document = plistlib.loads(FALLBACK_PLIST.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return False
    arguments = document.get("ProgramArguments") if isinstance(document, dict) else None
    environment = document.get("EnvironmentVariables") if isinstance(document, dict) else None
    return bool(
        isinstance(document, dict)
        and document.get("Label") == "com.xj.multiuser.formal-fallback"
        and isinstance(arguments, list)
        and arguments[:3] == [str(HOST_PYTHON), str(HOST_LAUNCHER), "serve-legacy"]
        and isinstance(environment, dict)
        and environment.get("XJ_RELEASE_LOCK") == str(ACTIVE_LOCK)
        and environment.get("XJ_PRIVATE_TERMS_FILE") == str(RUNTIME / "private" / "本机私密扫描词.txt")
        and "XJ_DEVELOPMENT_SOURCE" not in environment
    )


def 宿主边界状态() -> dict[str, Any]:
    from ..部署.监督服务命令 import 读取宿主运行环境状态

    runtime_state = 读取宿主运行环境状态()
    private_terms = RUNTIME / "private" / "本机私密扫描词.txt"
    private_terms_ready = private_terms.is_file() and not private_terms.is_symlink()
    runtime_ready = all(path.is_file() and not path.is_symlink() for path in (
        HOST_PYTHON, HOST_LAUNCHER, RUNTIME / "host-python.json",
    ))
    descriptors_ready = (
        all(_启动描述正确(role) for role in LABELS)
        and _回退启动描述正确()
    )
    mode_error = ""
    try:
        # 发布页运行在独立的开发管理进程里，不能根据调用者自己的环境变量
        # 冒充正式后台。必须查看 macOS 当前实际加载了哪组正式服务。
        mode = 当前正式运行模式()
    except RuntimeError as exc:
        mode = "unknown"
        mode_error = str(exc).strip() or "没有找到正在运行的正式后台"
    reason = ""
    if not runtime_ready:
        reason = "独立宿主 Python 环境尚未准备"
    elif not private_terms_ready:
        reason = "独立运行目录中的本机私密扫描词尚未准备"
    elif runtime_state.get("requires_approval"):
        reason = str(runtime_state.get("reason") or "宿主依赖环境未通过核对")
    elif not descriptors_ready:
        reason = "三个正式宿主启动描述尚未准备"
    elif mode == "formal-fallback":
        reason = "当前是激活正式副本的冻结回退模式，三角色宿主尚未恢复"
    elif mode == "unknown":
        reason = mode_error or "正式后台实际运行状态不明"
    return {
        "ready": not reason,
        "mode": mode,
        "migration_required": mode == "legacy",
        "runtime_ready": runtime_ready,
        "private_terms_ready": private_terms_ready,
        "runtime_state": runtime_state,
        "requires_approval": bool(runtime_state.get("requires_approval")),
        "descriptors_ready": descriptors_ready,
        "reason": reason,
    }


def 准备宿主边界() -> dict[str, Any]:
    """Prepare files only; never load or restart a LaunchAgent."""
    from ..部署 import 监督服务命令

    监督服务命令.准备不切换()
    return 宿主边界状态()


def 暂存候选宿主运行面(
    runtime: Mapping[str, Any],
    switch_id: str,
    *,
    previous_version: str = "",
) -> dict[str, Any]:
    from ..部署 import 监督服务命令

    return 监督服务命令.暂存候选正式宿主运行面(
        runtime, switch_id, previous_version=previous_version,
    )


def 恢复候选宿主运行面(switch_id: str) -> dict[str, Any]:
    from ..部署 import 监督服务命令

    return 监督服务命令.恢复暂存候选正式宿主(switch_id)


def 恢复当前正式后台() -> dict[str, Any]:
    """用当前活动封存版重新拉起正式后台；只供开发发布后台做失败恢复。"""
    from ..部署.宿主启动器 import 恢复活动正式后台

    restored = 恢复活动正式后台()
    roles = tuple(str(role) for role in restored.get("roles") or ())
    identities = 核对运行版本(
        str(restored.get("version") or ""),
        str(restored.get("source_sha256") or ""),
        roles=roles,
    )
    return {**restored, "identities": identities}


def 确认候选宿主运行面(switch_id: str) -> dict[str, Any]:
    from ..部署 import 监督服务命令

    if 当前正式运行模式() != "formal":
        raise RuntimeError("三个独立正式宿主尚未恢复，不能把备用模式记为已收口")
    active = _读取活动身份()
    核对运行版本(active["version"], active["source_sha256"])
    return 监督服务命令.确认候选正式宿主已生效(switch_id)


def 核对宿主发布能力() -> dict[str, Any]:
    state = 宿主边界状态()
    if not state["ready"]:
        raise RuntimeError(f"宿主版本边界未就绪：{state['reason']}")
    from ..部署.监督服务命令 import 核对独立运行环境

    try:
        核对独立运行环境()
    except Exception as exc:
        raise RuntimeError(f"宿主依赖环境核对失败：{exc}") from exc
    active = _读取活动身份()
    if state["mode"] == "formal":
        核对运行版本(active["version"], active["source_sha256"])
    elif not LEGACY_PLIST.is_file() or LEGACY_PLIST.is_symlink():
        raise RuntimeError("旧单进程回退启动项不存在，禁止开始首次迁移")
    return {**state, "active": active}


def 核对候选宿主入口(release: Mapping[str, Any]) -> dict[str, Any]:
    """检查固定候选副本，而不是检查当前开发目录是否能启动三角色。"""
    candidate_root = Path(str(release.get("path") or "")).expanduser().resolve()
    source_text = str(release.get("build_context") or "").strip()
    source = Path(source_text).expanduser().resolve() if source_text else candidate_root / "source"
    expected_source = (candidate_root / "source").resolve()
    if not candidate_root.is_dir() or source != expected_source:
        raise RuntimeError("候选宿主源码路径不是固定候选副本")
    from ..部署.宿主启动器 import 核对三角色入口

    result = 核对三角色入口(source)
    return {
        **result,
        "version": str(release.get("version") or ""),
        "candidate_id": str(release.get("candidate_id") or ""),
    }


def 准备候选宿主环境(release: Mapping[str, Any]) -> dict[str, Any]:
    """为一个已获批准的候选建立隔离宿主环境。

    这个环境只供候选影子验收使用。它不会覆盖正在运行的 ``host-python``，
    也不会写 LaunchAgent 或加载任何服务。正式运行环境的变更仍需单独流程。
    """
    candidate_id = str(release.get("candidate_id") or "").strip()
    if not _候选编号.fullmatch(candidate_id):
        raise RuntimeError("候选编号无效，拒绝创建候选宿主环境")
    candidate_root = Path(str(release.get("path") or "")).expanduser().resolve()
    source = (candidate_root / "source").resolve()
    if (
        not candidate_root.is_dir()
        or candidate_root.is_symlink()
        or not source.is_dir()
        or source.is_symlink()
        or str(release.get("build_context") or "").strip()
        and Path(str(release.get("build_context"))).expanduser().resolve() != source
    ):
        raise RuntimeError("候选宿主环境只能从固定候选副本创建")
    requirements = source / "requirements-host.txt"
    launcher_source = source / "多用户" / "部署" / "宿主启动器.py"
    if not requirements.is_file() or requirements.is_symlink():
        raise RuntimeError("候选缺少宿主依赖清单，不能创建隔离环境")
    if not launcher_source.is_file() or launcher_source.is_symlink():
        raise RuntimeError("候选缺少固定宿主启动器，不能创建隔离环境")

    # The candidate shadow process reads this host-local private configuration.
    # Provision it as part of the explicit candidate approval, before any
    # shadow service is started, so the later formal switch cannot discover it
    # only after accounts and the active version have changed.
    from ..部署 import 监督服务命令

    监督服务命令._准备私密扫描词()

    requirements_sha256 = hashlib.sha256(requirements.read_bytes()).hexdigest()
    target = (CANDIDATE_HOST_ROOT / candidate_id).resolve()
    metadata_path = target / "candidate-runtime.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        metadata = None
    if (
        isinstance(metadata, dict)
        and metadata.get("candidate_id") == candidate_id
        and metadata.get("requirements_sha256") == requirements_sha256
        and Path(str(metadata.get("python") or "")).is_file()
        and Path(str(metadata.get("launcher") or "")).is_file()
        and Path(str(metadata.get("manifest") or "")).is_file()
    ):
        return metadata

    CANDIDATE_HOST_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=f".{candidate_id}-", dir=CANDIDATE_HOST_ROOT))
    installed = False
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", "--copies", str(temporary)],
            cwd=source,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        python = temporary / "bin" / "python3"
        subprocess.run(
            [
                str(python), "-m", "pip", "install", "--disable-pip-version-check",
                "--requirement", str(requirements),
            ],
            cwd=source,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        launcher = temporary / "host-launcher.py"
        shutil.copy2(launcher_source, launcher)
        launcher.chmod(0o500)
        result = subprocess.run(
            [str(python), str(launcher), "runtime-identity"],
            cwd=source,
            env={**os.environ, "PYTHONPATH": str(source), "PYTHONNOUSERSITE": "1"},
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError("候选宿主 Python 无法完成身份核对")
        try:
            identity = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("候选宿主 Python 返回了无效身份") from exc
        if not isinstance(identity, dict) or identity.get("schema") != 1:
            raise RuntimeError("候选宿主 Python 身份无效")
        temporary_python = str(python.resolve())
        if identity.get("python_executable") != temporary_python:
            raise RuntimeError("候选宿主 Python 返回了不一致身份")
        # The environment is built in a temporary directory and atomically
        # renamed below.  Store the final path, otherwise every shadow role
        # rejects the manifest after the rename as a different interpreter.
        identity["python_executable"] = str((target / "bin" / "python3").resolve())
        manifest = temporary / "host-python.json"
        _原子写JSON(manifest, identity)
        metadata = {
            "schema": 1,
            "status": "ready",
            "candidate_id": candidate_id,
            "source_sha256": str(
                (release.get("source") or {}).get("sha256")
                if isinstance(release.get("source"), dict) else ""
            ),
            "requirements_sha256": requirements_sha256,
            "python": str(target / "bin" / "python3"),
            "launcher": str(target / "host-launcher.py"),
            "manifest": str(target / "host-python.json"),
            "created_at": _现在(),
        }
        _原子写JSON(temporary / "candidate-runtime.json", metadata)
        if target.exists() or target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        temporary.rename(target)
        installed = True
        return metadata
    except Exception:
        if installed:
            shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _正式助手命令(restart_id: str) -> tuple[list[str], Path, dict[str, str]]:
    mode = 当前正式运行模式()
    source = Path(__file__).resolve().parents[2]
    python = Path(sys.executable).expanduser().resolve()
    if not python.is_file() or not source.is_dir():
        raise RuntimeError("开发管理端的后台维护程序不完整，不能安排重启")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source)
    env["PYTHONNOUSERSITE"] = "1"
    env["XJ_RESTART_MODE"] = mode
    command = [
        str(python), "-m", "多用户.部署.监督服务命令",
        "kickstart", restart_id,
    ]
    if mode == "legacy":
        command.append("--legacy")
    return (
        command,
        source,
        env,
    )


def 请求重启() -> dict[str, Any]:
    with _重启锁():
        current = 读取状态()
        if _仍在进行(current):
            raise RuntimeError("后台已经在重启，请等待它恢复")

        restart_id = f"restart-{int(_现在())}-{secrets.token_hex(4)}"
        status = _写状态({
            "restart_id": restart_id,
            "status": "排队中",
            "stage": "等待正式后台重启",
            "message": "正在安排重新加载当前正式版本",
            "requested_at": _现在(),
            "started_at": 0,
            "completed_at": 0,
            "helper_pid": 0,
            "error": "",
        })
        log_dir = RUNTIME / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = log_dir / f"restart-{restart_id}.log"
        try:
            command, cwd, env = _正式助手命令(restart_id)
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            helper_pid = int(process.pid)
            if helper_pid <= 0:
                raise RuntimeError("后台维护程序没有成功启动")
            status = _写状态({
                **status,
                "helper_pid": helper_pid,
                "mode": env.get("XJ_RESTART_MODE", ""),
            })
        except Exception as exc:
            return _写状态({
                **status,
                "status": "失败",
                "stage": "没有安排成功",
                "message": "三个正式宿主服务没有开始重启",
                "completed_at": _现在(),
                "error": _错误文字(exc),
            })
        return status


def 标记重启中(restart_id: str) -> dict[str, Any] | None:
    current = 读取状态()
    if not current or current.get("restart_id") != restart_id:
        return current
    return _写状态({
        **current,
        "status": "重启中",
        "stage": "正在重新加载三个正式宿主服务",
        "message": "用户入口会短暂断开，正式版本和账号不会改变",
        "started_at": current.get("started_at") or _现在(),
        "error": "",
    })


def 标记失败(restart_id: str, exc: BaseException | str) -> dict[str, Any] | None:
    current = 读取状态()
    if not current or current.get("restart_id") != restart_id:
        return current
    return _写状态({
        **current,
        "status": "失败",
        "stage": "后台重启失败",
        "message": "没有完成三个正式宿主服务重启",
        "completed_at": _现在(),
        "error": _错误文字(exc) if isinstance(exc, BaseException) else str(exc)[:300],
    })


def 标记新进程已恢复() -> dict[str, Any] | None:
    current = 读取状态()
    if not current or current.get("status") not in _进行中:
        return current
    mode = os.environ.get("XJ_HOST_BOUNDARY_MODE", "")
    if mode == "development-admin":
        return current
    if mode == "legacy":
        stage = "旧单进程后台已恢复，宿主版本边界仍待迁移"
    elif mode == "formal-fallback":
        active = _读取活动身份()
        from ..部署.宿主启动器 import _正式角色列表

        roles = _正式角色列表(active["source_path"])
        核对运行版本(active["version"], active["source_sha256"], roles=roles)
        stage = "激活正式副本的冻结回退后台已恢复，三角色迁移仍待收口"
    else:
        active = _读取活动身份()
        if (
            os.environ.get("XJ_HOST_ROLE") != "admin"
            or os.environ.get("XJ_ACTIVE_VERSION") != active["version"]
            or os.environ.get("XJ_RELEASE_SOURCE_SHA256") != active["source_sha256"]
        ):
            return 标记失败(str(current.get("restart_id") or ""), "新管理后台没有运行活动正式版本")
        核对运行版本(
            active["version"], active["source_sha256"], roles=("control", "rerank"),
        )
        stage = "三个正式宿主服务已恢复"
    started = float(current.get("started_at") or current.get("requested_at") or _现在())
    return _写状态({
        **current,
        "status": "完成",
        "stage": stage,
        "message": "多用户后台已重新加载当前正式版本",
        "completed_at": _现在(),
        "elapsed_seconds": round(max(0.0, _现在() - started), 1),
        "error": "",
    })


def _角色集合(roles: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(roles))
    if not selected or any(role not in LABELS for role in selected):
        raise RuntimeError("宿主服务角色集合无效")
    return selected


def 触发角色重启(
    roles: Iterable[str],
    *,
    runner=None,
) -> None:
    execute = runner or subprocess.run
    domain = f"gui/{os.getuid()}"
    for role in _角色集合(roles):
        execute(
            ["/bin/launchctl", "kickstart", "-k", f"{domain}/{LABELS[role]}"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )


def _等待正式后台恢复(started_at: float, *, timeout: float = 90) -> tuple[str, ...]:
    active = _读取活动身份()
    from ..部署.宿主启动器 import _正式角色列表

    roles = _正式角色列表(active["source_path"])
    deadline = time.monotonic() + timeout
    last_error = "正式后台尚未报告运行状态"
    while time.monotonic() < deadline:
        try:
            identities = 核对运行版本(
                active["version"], active["source_sha256"], roles=roles,
            )
            if all(
                float(identity.get("updated_at") or 0) >= started_at
                for identity in identities.values()
            ):
                return roles
            last_error = "正式后台仍在启动"
        except (RuntimeError, TypeError, ValueError) as exc:
            last_error = str(exc).strip() or type(exc).__name__
        time.sleep(0.25)
    raise RuntimeError(f"正式后台没有在 90 秒内恢复：{last_error}")


def _标记重启完成(restart_id: str, roles: Iterable[str]) -> dict[str, Any] | None:
    current = 读取状态()
    if not current or current.get("restart_id") != restart_id:
        return current
    started = float(current.get("started_at") or current.get("requested_at") or _现在())
    selected = _角色集合(roles)
    return _写状态({
        **current,
        "status": "完成",
        "stage": "正式后台已恢复",
        "message": f"当前正式版本已重新加载，{len(selected)} 个后台入口均已恢复",
        "completed_at": _现在(),
        "elapsed_seconds": round(max(0.0, _现在() - started), 1),
        "error": "",
    })


def 触发系统重启(restart_id: str) -> None:
    """Run outside all managed processes and replace admin last."""
    time.sleep(0.8)
    current = 读取状态()
    if not current or current.get("restart_id") != restart_id:
        return
    progress = 标记重启中(restart_id)
    started_at = float((progress or {}).get("started_at") or _现在())
    try:
        mode = os.environ.get("XJ_RESTART_MODE", "")
        if mode == "formal-fallback":
            from ..部署.宿主启动器 import 恢复活动正式后台

            恢复活动正式后台()
        elif mode == "formal":
            触发角色重启(("control", "rerank", "admin"))
        else:
            raise RuntimeError("正式后台运行方式不明确，已停止重启")
        roles = _等待正式后台恢复(started_at)
        _标记重启完成(restart_id, roles)
    except Exception as exc:
        标记失败(restart_id, exc)


def _触发冻结回退重启() -> None:
    """Reload the sealed single-process fallback, never the development agent."""
    subprocess.run(
        [
            "/bin/launchctl", "kickstart", "-k",
            f"gui/{os.getuid()}/{FALLBACK_LABEL}",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )


def 触发旧系统重启(restart_id: str) -> None:
    """Temporary pre-migration restart; one external helper replaces the combined process."""
    time.sleep(0.8)
    current = 读取状态()
    if not current or current.get("restart_id") != restart_id:
        return
    progress = 标记重启中(restart_id)
    started_at = float((progress or {}).get("started_at") or _现在())
    try:
        subprocess.run(
            [
                "/bin/launchctl", "kickstart", "-k",
                f"gui/{os.getuid()}/{LEGACY_LABEL}",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        roles = _等待正式后台恢复(started_at)
        _标记重启完成(restart_id, roles)
    except Exception as exc:
        标记失败(restart_id, exc)


def 构建影子启动命令(
    role: str,
    release: Mapping[str, Any],
    *,
    port: int,
    status_file: Path,
    development_source: Path | None = None,
    host_runtime: Mapping[str, Any] | None = None,
) -> tuple[list[str], dict[str, str]]:
    if role not in LABELS or not 1024 <= int(port) <= 65535:
        raise RuntimeError("影子宿主服务参数无效")
    source = release.get("source") if isinstance(release.get("source"), dict) else {}
    version = str(release.get("version") or "")
    release_path = Path(str(release.get("path") or "")).expanduser().resolve()
    source_text = (
        str(release.get("build_context") or "").strip()
        if release.get("shadow_mode") == "candidate" else ""
    )
    source_path = Path(source_text).expanduser().resolve() if source_text else None
    source_sha256 = str(source.get("sha256") or "")
    if not version or len(source_sha256) != 64:
        raise RuntimeError("影子宿主版本身份不完整")
    runtime = dict(host_runtime or {})
    python = Path(str(runtime.get("python") or HOST_PYTHON)).expanduser().resolve()
    launcher = Path(str(runtime.get("launcher") or HOST_LAUNCHER)).expanduser().resolve()
    manifest = Path(str(runtime.get("manifest") or RUNTIME / "host-python.json")).expanduser().resolve()
    if host_runtime is not None and (not python.is_file() or not launcher.is_file() or not manifest.is_file()):
        raise RuntimeError("候选宿主隔离环境不完整，不能启动影子验收")
    if source_path:
        if not release_path.is_dir() or source_path != (release_path / "source").resolve():
            raise RuntimeError("影子候选源码路径不是固定副本")
        command = [
            str(python), str(launcher), "serve", "--role", role,
            "--source-path", str(source_path), "--version", version,
            "--source-sha256", source_sha256, "--port", str(port),
        ]
    else:
        if not release_path.is_dir():
            raise RuntimeError("影子宿主版本身份不完整")
        command = [
            str(python), str(launcher), "serve", "--role", role,
            "--release-path", str(release_path), "--version", version,
            "--source-sha256", source_sha256, "--allow-staged", "--port", str(port),
        ]
    env = os.environ.copy()
    env.pop("XJ_DEVELOPMENT_SOURCE", None)
    if role == "admin":
        configured = os.environ.get("XJ_DEVELOPMENT_SOURCE", "").strip()
        selected = (
            development_source.expanduser().resolve()
            if development_source is not None
            else Path(configured).expanduser().resolve() if configured else None
        )
        if selected is None:
            raise RuntimeError("管理影子服务缺少明确的开发源目录")
        if not selected.is_dir() or selected.is_symlink():
            raise RuntimeError("管理影子服务缺少明确的开发源目录")
        env["XJ_DEVELOPMENT_SOURCE"] = str(selected)
    env["XJ_HOST_STATUS_FILE"] = str(status_file.expanduser().resolve())
    env["XJ_HOST_RUNTIME_MANIFEST"] = str(manifest)
    return command, env


def _影子开发源(capability: Mapping[str, Any]) -> Path:
    configured = os.environ.get("XJ_DEVELOPMENT_SOURCE", "").strip()
    if configured:
        development_source = Path(configured).expanduser().resolve()
    elif capability.get("mode") == "legacy":
        # The installed legacy LaunchAgent predates XJ_DEVELOPMENT_SOURCE.
        # Its currently imported management module is the one explicit,
        # owner-only development source for the first candidate shadow.
        development_source = Path(__file__).resolve().parents[2]
    else:
        raise RuntimeError("正式管理服务缺少明确的开发源目录，禁止影子验收")
    if not development_source.is_dir() or development_source.is_symlink():
        raise RuntimeError("影子验收的开发源目录无效")
    return development_source


def 验收宿主影子(release: Mapping[str, Any]) -> dict[str, Any]:
    """Run all three roles from one sealed artifact against isolated empty state."""
    version = str(release.get("version") or "")
    source = release.get("source") if isinstance(release.get("source"), dict) else {}
    source_sha256 = str(source.get("sha256") or "")
    release_path = Path(str(release.get("path") or "")).expanduser().resolve()
    source_text = (
        str(release.get("build_context") or "").strip()
        if release.get("shadow_mode") == "candidate" else ""
    )
    source_path = Path(source_text).expanduser().resolve() if source_text else None
    if not version or len(source_sha256) != 64 or not release_path.is_dir():
        raise RuntimeError("影子验收的封存版本身份不完整")
    if source_path and source_path != (release_path / "source").resolve():
        raise RuntimeError("影子候选源码路径不是固定副本")
    host_runtime = release.get("host_runtime") if isinstance(release.get("host_runtime"), dict) else None
    if release.get("shadow_mode") == "candidate":
        if not host_runtime or host_runtime.get("status") != "ready":
            raise RuntimeError("候选尚未获批准建立隔离宿主环境")
        # 候选影子不依赖当前正式宿主的 Python；正式版本仍由正式宿主能力检查。
        capability = 宿主边界状态()
    else:
        capability = 核对宿主发布能力()
    development_source = _影子开发源(capability)
    shadow_id = f"shadow-{int(_现在())}-{secrets.token_hex(4)}"
    shadow_root = RUNTIME / "shadow" / shadow_id
    status_root = shadow_root / "status"
    log_root = RUNTIME / "logs" / shadow_id
    shadow_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    status_root.mkdir(mode=0o700)
    log_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    active_path = shadow_root / "active-release.json"
    _原子写JSON(active_path, {
        "schema": 3,
        "version": version,
        "version_name": str(release.get("version_name") or version),
        "candidate_id": str(release.get("candidate_id") or ""),
        "source": dict(source),
        "release_path": str(release_path),
    })
    ports = {"control": 38654, "admin": 38655, "rerank": 38657}
    processes: dict[str, subprocess.Popen] = {}
    logs = []
    started = time.monotonic()
    try:
        for role in ("control", "admin", "rerank"):
            status_file = status_root / f"{role}.json"
            command, env = 构建影子启动命令(
                role,
                release,
                port=ports[role],
                status_file=status_file,
                development_source=development_source,
                host_runtime=host_runtime,
            )
            env["XJ_HOST_SHADOW_ROOT"] = str(shadow_root)
            env["XJ_RELEASE_LOCK"] = str(active_path)
            log = (log_root / f"{role}.log").open("a", encoding="utf-8")
            logs.append(log)
            processes[role] = subprocess.Popen(
                command,
                cwd=release_path / "source",
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )

        from .凭据 import 读取平台
        rerank_token = os.environ.get("XJ_RERANK_TOKEN", "") or 读取平台("rerank-token")
        deadline = time.monotonic() + 300
        identities: dict[str, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            exited = {
                role: process.returncode
                for role, process in processes.items()
                if process.poll() is not None
            }
            if exited:
                raise RuntimeError(f"影子宿主进程提前退出：{exited}")
            ready = True
            identities = {}
            for role in processes:
                try:
                    identity = 读取角色身份(
                        role, status_file=status_root / f"{role}.json",
                    )
                except RuntimeError:
                    ready = False
                    continue
                module = Path(str(identity.get("module") or "")).expanduser().resolve()
                expected_source = source_path or (release_path / "source").resolve()
                if (
                    identity.get("status") != "running"
                    or identity.get("version") != version
                    or identity.get("source_sha256") != source_sha256
                    or expected_source not in module.parents
                ):
                    ready = False
                    continue
                identities[role] = identity
                try:
                    response = httpx.get(
                        f"http://127.0.0.1:{ports[role]}/healthz",
                        headers=(
                            {"authorization": f"Bearer {rerank_token}"}
                            if role == "rerank" else None
                        ),
                        timeout=8,
                        trust_env=False,
                    )
                    if response.status_code != 200:
                        ready = False
                except httpx.HTTPError:
                    ready = False
            if ready and len(identities) == 3:
                return {
                    "status": "passed",
                    "shadow_id": shadow_id,
                    "version": version,
                    "source_sha256": source_sha256,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "roles": {
                        role: {
                            key: identity.get(key)
                            for key in (
                                "version", "source_sha256", "module",
                                "python_version", "dependency_sha256",
                            )
                        }
                        for role, identity in identities.items()
                    },
                    "logs": str(log_root),
                }
            time.sleep(0.25)
        raise RuntimeError("三个宿主影子服务没有在限时内全部健康")
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
        shutil.rmtree(shadow_root, ignore_errors=True)


def 建立宿主切换计划(
    deployment_id: str,
    version: str,
    source_sha256: str,
    *,
    allow_legacy: bool = False,
    host_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not deployment_id or not version or len(source_sha256) != 64:
        raise RuntimeError("宿主切换目标不完整")
    capability = 宿主边界状态()
    if not capability.get("ready") and not host_runtime:
        raise RuntimeError(f"宿主运行面未准备：{capability.get('reason') or '请先准备'}")
    if capability.get("migration_required") and not (allow_legacy or host_runtime):
        raise RuntimeError("当前仍是旧单进程模式，需先单独确认宿主迁移")
    if allow_legacy and not capability.get("migration_required"):
        raise RuntimeError("首轮宿主引导只允许从旧单进程模式开始")
    capability = (
        {**capability, "active": _读取活动身份(), "candidate_runtime": True}
        if host_runtime
        else 核对宿主发布能力()
        if not capability.get("migration_required")
        else {**capability, "active": _读取活动身份()}
    )
    active = capability["active"]
    if active["version"] != version or active["source_sha256"] != source_sha256:
        raise RuntimeError("已生效版本与宿主切换目标不一致")
    with _切换锁():
        previous = 读取宿主切换()
        if previous and previous.get("status") in {"queued", "switching", "host-ready"}:
            same = (
                previous.get("deployment_id") == deployment_id
                and (previous.get("target") or {}).get("version") == version
                and (previous.get("target") or {}).get("source_sha256") == source_sha256
            )
            if same:
                return previous
            raise RuntimeError("另一次宿主切换尚未收口")
        switch_id = f"switch-{int(_现在())}-{secrets.token_hex(4)}"
        state = _原子写JSON(SWITCH_FILE, {
            "schema": 1,
            "switch_id": switch_id,
            "deployment_id": deployment_id,
            "mode": "legacy-migration" if capability["mode"] == "legacy" else "formal-switch",
            "status": "queued",
            "stage": "等待外部宿主助手切换",
            "target": {
                "version": version,
                "source_sha256": source_sha256,
                "release_path": active["release_path"],
            },
            "host_runtime": dict(host_runtime or {}),
            "requested_at": _现在(),
            "started_at": 0,
            "host_ready_at": 0,
            "completed_at": 0,
            "updated_at": _现在(),
            "error": "",
            "fallback": "",
        })
        return state


def 建立首轮宿主引导计划(
    deployment_id: str,
    version: str,
    source_sha256: str,
    *,
    host_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """为首个三角色候选建立一次性旧单进程 -> 正式宿主切换计划。"""
    capability = 宿主边界状态()
    if not capability.get("migration_required"):
        raise RuntimeError("当前不是旧单进程模式，不能重复执行首轮宿主引导")
    return 建立宿主切换计划(
        deployment_id,
        version,
        source_sha256,
        allow_legacy=True,
        host_runtime=host_runtime,
    )


def 启动宿主切换(switch_id: str) -> dict[str, Any]:
    with _切换锁():
        state = 读取宿主切换()
        if not state or state.get("switch_id") != switch_id:
            raise RuntimeError("宿主切换计划不存在")
        if state.get("status") not in {"queued", "failed"}:
            return state
        log_dir = RUNTIME / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = log_dir / f"host-switch-{switch_id}.log"
        switch_python = HOST_PYTHON
        switch_launcher = HOST_LAUNCHER
        try:
            from ..部署 import 监督服务命令

            runtime_state = json.loads(
                监督服务命令.HOST_RUNTIME_SWITCH.read_text(encoding="utf-8")
            )
            if runtime_state.get("switch_id") == switch_id and runtime_state.get("status") == "staged":
                runtime = runtime_state.get("runtime") if isinstance(runtime_state.get("runtime"), dict) else {}
                switch_python = Path(str(runtime.get("python") or switch_python)).expanduser().resolve()
                switch_launcher = Path(str(runtime.get("launcher") or switch_launcher)).expanduser().resolve()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError):
            pass
        process = None
        try:
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        str(switch_python), str(switch_launcher), "switch",
                        "--switch-id", switch_id,
                    ],
                    cwd=RUNTIME,
                    env={
                        **os.environ,
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "XJ_RELEASE_LOCK": str(ACTIVE_LOCK),
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            state = _原子写JSON(SWITCH_FILE, {
                **state,
                "status": "queued",
                "stage": "宿主助手已启动，等待切换",
                "helper_pid": int(getattr(process, "pid", 0) or 0),
                "started_at": state.get("started_at") or _现在(),
                "updated_at": _现在(),
            })
        except Exception as exc:
            if process is not None:
                try:
                    process.terminate()
                except Exception:
                    pass
            restore_error = ""
            try:
                # Popen can fail after the candidate plist set has already
                # been staged.  Restore it immediately; leaving it in place
                # would make the next ordinary restart launch the candidate
                # even though this deployment never started.
                runtime_state = json.loads(
                    (RUNTIME / "data" / "host-runtime-switch.json").read_text(
                        encoding="utf-8"
                    )
                )
                if (
                    runtime_state.get("switch_id") == switch_id
                    and runtime_state.get("status") == "staged"
                ):
                    恢复候选宿主运行面(switch_id)
            except Exception as restore_exc:
                restore_error = f"；旧宿主运行面恢复失败：{_错误文字(restore_exc)}"
            _原子写JSON(SWITCH_FILE, {
                **state,
                "status": "failed",
                "stage": "外部宿主助手没有启动",
                "completed_at": _现在(),
                "updated_at": _现在(),
                "error": _错误文字(exc) + restore_error,
            })
            raise RuntimeError("外部宿主助手没有启动") from exc
        return state


def 安排宿主切换(
    deployment_id: str,
    version: str,
    source_sha256: str,
) -> dict[str, Any]:
    state = 建立宿主切换计划(deployment_id, version, source_sha256)
    return 启动宿主切换(str(state["switch_id"]))


def 建立首次宿主迁移(
    version: str,
    source_sha256: str,
) -> dict[str, Any]:
    """Create a migration plan from the legacy combined agent; no code imports dev source."""
    from ..部署.宿主启动器 import ROLE_APPLICATIONS, 解析活动发布

    active = 解析活动发布(ACTIVE_LOCK)
    if active["version"] != version or active["source_sha256"] != source_sha256:
        raise RuntimeError("活动版本与迁移目标不一致")
    source = Path(active["source_path"])
    missing = []
    for application in ROLE_APPLICATIONS.values():
        module = application.partition(":")[0]
        path = source.joinpath(*module.split("."))
        if not path.with_suffix(".py").is_file():
            missing.append(str(path.with_suffix(".py")))
    if missing:
        raise RuntimeError("活动正式版本不具备三角色服务，不能迁移：" + "、".join(missing))
    return 建立宿主切换计划(
        f"host-migration-{int(_现在())}-{secrets.token_hex(4)}",
        version,
        source_sha256,
        allow_legacy=True,
    )


def 标记宿主切换已收口(switch_id: str) -> dict[str, Any]:
    with _切换锁():
        state = 读取宿主切换()
        if not state or state.get("switch_id") != switch_id:
            raise RuntimeError("宿主切换记录不存在")
        if state.get("status") == "completed":
            return state
        if state.get("status") != "host-ready":
            raise RuntimeError("三个宿主服务尚未全部就绪")
        target = state.get("target") if isinstance(state.get("target"), dict) else {}
        核对运行版本(
            str(target.get("version") or ""), str(target.get("source_sha256") or ""),
            roles=("control", "rerank"),
        )
        if (
            os.environ.get("XJ_HOST_ROLE") != "admin"
            or os.environ.get("XJ_ACTIVE_VERSION") != str(target.get("version") or "")
            or os.environ.get("XJ_RELEASE_SOURCE_SHA256") != str(target.get("source_sha256") or "")
        ):
            raise RuntimeError("新管理后台身份与宿主切换目标不一致")
        return _原子写JSON(SWITCH_FILE, {
            **state,
            "status": "completed",
            "stage": "宿主运行面切换已收口",
            "completed_at": _现在(),
            "updated_at": _现在(),
            "error": "",
        })


def 读取角色身份(role: str, *, status_file: Path | None = None) -> dict[str, Any]:
    if role not in LABELS:
        raise RuntimeError("宿主服务角色无效")
    path = (status_file or ROLE_STATUS[role]).expanduser().resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 {role} 宿主身份") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{role} 宿主身份格式无效")
    return value


def 核对运行版本(
    version: str,
    source_sha256: str,
    *,
    roles: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    active = _读取活动身份()
    if active["version"] != version or active["source_sha256"] != source_sha256:
        raise RuntimeError("要核对的版本不是当前活动正式版本")
    expected_source = Path(active["source_path"]).expanduser().resolve()
    if roles is None:
        # 恢复当前已发布版时，只核对该封存版本实际拥有的宿主岗位。
        # 老版本发布时还没有本地精排宿主，不能拿新版本的三岗位规则
        # 反过来判定老版本损坏。候选发布仍会在调用处显式要求三岗位。
        from ..部署.宿主启动器 import _正式角色列表

        roles = _正式角色列表(expected_source)
    result: dict[str, dict[str, Any]] = {}
    process_roles: dict[int, str] = {}
    for role in _角色集合(roles):
        identity = 读取角色身份(role)
        pid = identity.get("pid")
        if (
            identity.get("status") != "running"
            or identity.get("role") != role
            or identity.get("fallback")
            or type(pid) is not int
            or pid <= 0
            or identity.get("version") != version
            or identity.get("source_sha256") != source_sha256
            or Path(str(identity.get("source") or "")).expanduser().resolve() != expected_source
            or Path(str(identity.get("module") or "")).expanduser().resolve().is_relative_to(
                expected_source
            ) is False
            or len(str(identity.get("dependency_sha256") or "")) != 64
            or not str(identity.get("python_version") or "")
            or Path(str(identity.get("module") or "")).expanduser().resolve().is_relative_to(
                Path(os.environ.get("XJ_DEVELOPMENT_SOURCE", "/nonexistent")).expanduser().resolve()
            )
        ):
            raise RuntimeError(f"{role} 没有运行指定正式版本")
        headers = None
        if role == "rerank":
            from .凭据 import 读取平台

            headers = {"authorization": f"Bearer {读取平台('rerank-token')}"}
        try:
            response = httpx.get(
                f"http://127.0.0.1:{PORTS[role]}/healthz",
                headers=headers,
                timeout=5,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{role} 正式入口无法访问") from exc
        if response.status_code != 200:
            raise RuntimeError(f"{role} 正式入口没有通过健康检查")
        if pid in process_roles:
            raise RuntimeError(
                f"{role} 与 {process_roles[pid]} 由同一进程承载，不是独立正式宿主"
            )
        process_roles[pid] = role
        result[role] = identity
    return result

"""Prepare and manage three versioned macOS host services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..控制面.凭据 import 读取平台
from ..控制面.宿主服务重启 import 标记失败, 触发旧系统重启, 触发系统重启
from .发布仓库 import LEGACY_LOCK_PATH, LOCK_PATH, 当前已发布版本, 切换当前版本
from .开发源 import 登记唯一开发源, 读取唯一开发源


ROOT = Path(__file__).resolve().parents[2]
BASE_REQUIREMENTS = ROOT / "requirements.txt"
HOST_REQUIREMENTS = ROOT / "requirements-host.txt"
PLATFORM_REQUIREMENTS = ROOT / "多用户" / "requirements.txt"
RUNTIME = Path.home() / "Library" / "Application Support" / "XJ Multiuser"
ACTIVE_LOCK = Path(
    os.environ.get("XJ_ACTIVE_RELEASE_LOCK", str(RUNTIME / "data" / "active-release.json"))
).expanduser().resolve()
HOST = "127.0.0.1"
PORT = 37654
ADMIN_PORT = 37655
RERANK_PORT = 37657
DEVELOPMENT_ADMIN_PORT = 37660
HOST_ENV = RUNTIME / "host-python"
HOST_PYTHON = HOST_ENV / "bin" / "python3"
HOST_IDENTITY = RUNTIME / "host-python.json"
LAUNCHER_SOURCE = Path(__file__).resolve().with_name("宿主启动器.py")
LAUNCHER = RUNTIME / "launcher" / "host-launcher.py"
PRIVATE_TERMS = RUNTIME / "private" / "本机私密扫描词.txt"
RELEASE_STORE = Path(
    os.environ.get("XJ_RELEASE_STORE", str(ROOT.parent / "90_备份" / "发布仓库"))
).expanduser().resolve()
LEGACY_LABEL = "com.xj.multiuser.supervisor"
FALLBACK_LABEL = "com.xj.multiuser.formal-fallback"
DEVELOPMENT_ADMIN_LABEL = "com.xj.multiuser.development-admin"
LABELS = {
    "control": "com.xj.multiuser.control",
    "admin": "com.xj.multiuser.admin",
    "rerank": "com.xj.multiuser.rerank",
}
PORTS = {"control": PORT, "admin": ADMIN_PORT, "rerank": RERANK_PORT}
PLISTS = {
    role: Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    for role, label in LABELS.items()
}
LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
FALLBACK_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{FALLBACK_LABEL}.plist"
DEVELOPMENT_ADMIN_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{DEVELOPMENT_ADMIN_LABEL}.plist"
HOST_RUNTIME_SWITCH = RUNTIME / "data" / "host-runtime-switch.json"

# Compatibility for the existing admin preflight. New code should inspect PLISTS.
LABEL = LABELS["admin"]
PLIST = PLISTS["admin"]


def _宿主工具路径() -> str:
    """LaunchAgent does not read an interactive shell PATH."""
    candidates = (
        HOST_PYTHON.parent,
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
        Path("/Applications/Docker.app/Contents/Resources/bin"),
    )
    return os.pathsep.join(dict.fromkeys(str(path) for path in candidates))


def _启动项文档(
    role: str = "control",
    *,
    host_python: Path = HOST_PYTHON,
    launcher: Path = LAUNCHER,
    manifest: Path = HOST_IDENTITY,
    active_lock: Path = ACTIVE_LOCK,
) -> dict[str, object]:
    if role not in LABELS:
        raise RuntimeError("宿主服务角色无效")
    environment = {
        "PATH": _宿主工具路径(),
        # 新宿主只读独立活动指针；LOCK_PATH 仅供尚未迁移的旧单进程兼容。
        "XJ_RELEASE_LOCK": str(active_lock),
        "XJ_RELEASE_STORE": str(RELEASE_STORE),
        "XJ_PRIVATE_TERMS_FILE": str(PRIVATE_TERMS),
        "XJ_HOST_RUNTIME_MANIFEST": str(manifest),
    }
    return {
        "Label": LABELS[role],
        "ProgramArguments": [
            str(host_python), str(launcher), "serve", "--role", role,
            "--active-lock", str(active_lock),
        ],
        "WorkingDirectory": str(RUNTIME),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
        "StandardOutPath": str(RUNTIME / "logs" / f"{role}.log"),
        "StandardErrorPath": str(RUNTIME / "logs" / f"{role}-error.log"),
    }


def _开发管理启动项文档(
    *,
    host_python: Path = HOST_PYTHON,
    launcher: Path = LAUNCHER,
    manifest: Path = HOST_IDENTITY,
    active_lock: Path = ACTIVE_LOCK,
    development_source: Path | None = None,
    port: int = DEVELOPMENT_ADMIN_PORT,
) -> dict[str, object]:
    """Build the owner-only development management LaunchAgent.

    This is deliberately a separate label and port.  The formal role plists
    remain versioned and can switch during a release without replacing the
    page that the owner uses to manufacture the next candidate.
    """
    if not 1024 <= int(port) <= 65535:
        raise RuntimeError("开发管理后台端口无效")
    source = (development_source or _开发源目录()).expanduser().resolve()
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError("管理员开发目录不存在")
    document = _启动项文档(
        "admin",
        host_python=host_python,
        launcher=launcher,
        manifest=manifest,
        active_lock=active_lock,
    )
    environment = dict(document["EnvironmentVariables"])
    environment.update({
        "XJ_DEVELOPMENT_SOURCE": str(source),
        "XJ_ADMIN_PORT": str(port),
        "XJ_DEVELOPMENT_ADMIN": "1",
        "XJ_HOST_STATUS_FILE": str(RUNTIME / "status" / "development-admin.json"),
    })
    return {
        "Label": DEVELOPMENT_ADMIN_LABEL,
        "ProgramArguments": [
            str(host_python), str(launcher), "serve-development-admin",
            "--active-lock", str(active_lock),
            "--source-path", str(source), "--port", str(port),
        ],
        "WorkingDirectory": str(RUNTIME),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
        "StandardOutPath": str(RUNTIME / "logs" / "development-admin.log"),
        "StandardErrorPath": str(RUNTIME / "logs" / "development-admin-error.log"),
    }


def _冻结正式回退文档(
    *,
    host_python: Path = HOST_PYTHON,
    launcher: Path = LAUNCHER,
    manifest: Path = HOST_IDENTITY,
    active_lock: Path = ACTIVE_LOCK,
) -> dict[str, object]:
    return {
        "Label": FALLBACK_LABEL,
        "ProgramArguments": [
            str(host_python), str(launcher), "serve-legacy",
            "--active-lock", str(active_lock),
        ],
        "WorkingDirectory": str(RUNTIME),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": _宿主工具路径(),
            "XJ_RELEASE_LOCK": str(active_lock),
            "XJ_RELEASE_STORE": str(RELEASE_STORE),
            "XJ_PRIVATE_TERMS_FILE": str(PRIVATE_TERMS),
            "XJ_HOST_RUNTIME_MANIFEST": str(manifest),
        },
        "StandardOutPath": str(RUNTIME / "logs" / "formal-fallback.log"),
        "StandardErrorPath": str(RUNTIME / "logs" / "formal-fallback-error.log"),
    }


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        check=check,
        stdin=subprocess.DEVNULL,
        stdout=None,
        stderr=None,
    )


def _写JSON(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _开发源目录() -> Path:
    """只返回本机已登记的唯一开发区，没有猜测回退。"""
    return 读取唯一开发源(
        record_path=RUNTIME / "data" / "development-source.json",
        release_store=RELEASE_STORE,
    )


def _登记开发源(source: Path, *, replace: bool = False) -> Path:
    """显式登记开发边界；禁止根据当前代码副本自动猜测。"""
    return 登记唯一开发源(
        source,
        record_path=RUNTIME / "data" / "development-source.json",
        release_store=RELEASE_STORE,
        replace=replace,
    )


def _运行环境身份(python: Path, launcher: Path = LAUNCHER_SOURCE) -> dict[str, Any]:
    result = subprocess.run(
        [str(python), str(launcher), "runtime-identity"],
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("独立宿主 Python 环境无法完成身份核对")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("独立宿主 Python 环境返回了无效身份") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("python_executable") != str(python.resolve())
        or len(str(value.get("pip_freeze_sha256") or "")) != 64
    ):
        raise RuntimeError("独立宿主 Python 环境身份无效")
    for requirements in (BASE_REQUIREMENTS, HOST_REQUIREMENTS, PLATFORM_REQUIREMENTS):
        if not requirements.is_file() or requirements.is_symlink():
            raise RuntimeError("缺少宿主运行依赖清单")
    base_digest = hashlib.sha256(BASE_REQUIREMENTS.read_bytes()).hexdigest()
    host_digest = hashlib.sha256(HOST_REQUIREMENTS.read_bytes()).hexdigest()
    platform_digest = hashlib.sha256(PLATFORM_REQUIREMENTS.read_bytes()).hexdigest()
    combined_digest = hashlib.sha256(
        (
            f"requirements.txt:{base_digest}\n"
            f"requirements-host.txt:{host_digest}\n"
            f"多用户/requirements.txt:{platform_digest}\n"
        ).encode("utf-8")
    ).hexdigest()
    return {
        **value,
        "requirements_sha256": combined_digest,
        "base_requirements_sha256": base_digest,
        "host_requirements_sha256": host_digest,
        "platform_requirements_sha256": platform_digest,
    }


def 读取宿主运行环境状态() -> dict[str, Any]:
    """只读查看宿主依赖登记；不会运行 pip、创建 venv 或改写运行目录。"""
    if not HOST_ENV.is_dir() or HOST_ENV.is_symlink() or not HOST_PYTHON.is_file() or not HOST_IDENTITY.is_file():
        return {
            "ready": False,
            "requires_approval": True,
            "reason": "独立宿主 Python 环境尚未准备",
            "requirements_match": False,
        }
    if not PRIVATE_TERMS.is_file() or PRIVATE_TERMS.is_symlink():
        return {
            "ready": False,
            "requires_approval": True,
            "reason": "独立运行目录中的本机私密扫描词尚未准备",
            "requirements_match": True,
            "private_terms_ready": False,
        }
    try:
        expected = json.loads(HOST_IDENTITY.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "ready": False,
            "requires_approval": True,
            "reason": "独立宿主运行环境登记无法读取",
            "requirements_match": False,
        }
    try:
        digests = []
        for requirements in (BASE_REQUIREMENTS, HOST_REQUIREMENTS, PLATFORM_REQUIREMENTS):
            if not requirements.is_file() or requirements.is_symlink():
                raise RuntimeError("缺少宿主运行依赖清单")
            digests.append(hashlib.sha256(requirements.read_bytes()).hexdigest())
        combined = hashlib.sha256(
            (
                f"requirements.txt:{digests[0]}\n"
                f"requirements-host.txt:{digests[1]}\n"
                f"多用户/requirements.txt:{digests[2]}\n"
            ).encode("utf-8")
        ).hexdigest()
    except Exception as exc:
        return {
            "ready": False,
            "requires_approval": True,
            "reason": str(exc) or type(exc).__name__,
            "requirements_match": False,
        }
    match = expected.get("requirements_sha256") == combined
    if not match:
        return {
            "ready": False,
            "requires_approval": True,
            "reason": "宿主依赖清单已变化，需要为候选建立隔离运行环境",
            "requirements_match": False,
            "expected_requirements_sha256": str(expected.get("requirements_sha256") or ""),
            "current_requirements_sha256": combined,
        }
    return {
        "ready": True,
        "requires_approval": False,
        "reason": "",
        "requirements_match": True,
        "private_terms_ready": True,
        "requirements_sha256": combined,
    }


def 核对独立运行环境() -> dict[str, Any]:
    if not HOST_PYTHON.is_file() or not LAUNCHER.is_file() or not HOST_IDENTITY.is_file():
        raise RuntimeError("独立宿主运行环境尚未准备完成")
    try:
        expected = json.loads(HOST_IDENTITY.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("独立宿主运行环境登记无法读取") from exc
    actual = _运行环境身份(HOST_PYTHON, LAUNCHER)
    if expected != actual:
        raise RuntimeError("独立宿主 Python 或依赖已经变化")
    return actual


def _准备独立运行环境() -> dict[str, Any]:
    if HOST_ENV.exists():
        if not HOST_PYTHON.is_file():
            raise RuntimeError("独立宿主 Python 环境不完整")
        actual = _运行环境身份(HOST_PYTHON)
        if HOST_IDENTITY.is_file():
            try:
                expected = json.loads(HOST_IDENTITY.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("独立宿主运行环境登记无法读取") from exc
            if expected != actual:
                if expected.get("requirements_sha256") != actual.get("requirements_sha256"):
                    raise RuntimeError("宿主依赖清单已变化，需先走宿主运行环境升级；禁止覆盖现用环境")
                raise RuntimeError("独立宿主 Python 或依赖已经变化")
        else:
            _写JSON(HOST_IDENTITY, actual)
        return actual
    requirements = HOST_REQUIREMENTS
    if not requirements.is_file() or requirements.is_symlink():
        raise RuntimeError("缺少宿主运行依赖清单")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".host-python-", dir=RUNTIME))
    installed = False
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", "--copies", str(temporary)],
            stdin=subprocess.DEVNULL,
            check=True,
        )
        temporary_python = temporary / "bin" / "python3"
        subprocess.run(
            [
                str(temporary_python), "-m", "pip", "install",
                "--disable-pip-version-check", "--requirement", str(requirements),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                str(temporary_python), "-c",
                (
                    "import fastapi,httpx,uvicorn,mlx_lm,pwdlib; "
                    "from 多用户.控制面 import 服务,管理服务,本地精排服务; "
                    "assert 服务.app and 管理服务.app and 本地精排服务.app; "
                    "print('host-runtime-ok')"
                ),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        temporary.rename(HOST_ENV)
        installed = True
        identity = _运行环境身份(HOST_PYTHON)
        _写JSON(HOST_IDENTITY, identity)
        return identity
    except Exception:
        if installed:
            shutil.rmtree(HOST_ENV, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _准备启动器() -> None:
    if not LAUNCHER_SOURCE.is_file() or LAUNCHER_SOURCE.is_symlink():
        raise RuntimeError("固定宿主启动器源码不存在")
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = LAUNCHER.with_name(f".{LAUNCHER.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(LAUNCHER_SOURCE, temporary)
        temporary.chmod(0o500)
        os.replace(temporary, LAUNCHER)
    finally:
        temporary.unlink(missing_ok=True)


def _准备私密扫描词() -> None:
    if PRIVATE_TERMS.is_file() and not PRIVATE_TERMS.is_symlink():
        return
    candidates: list[Path] = []
    configured = os.environ.get("XJ_PRIVATE_TERMS_SOURCE", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    record_path = RUNTIME / "data" / "development-source.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        record = None
    if isinstance(record, dict):
        recorded_source = Path(str(record.get("source") or "")).expanduser().resolve()
        candidates.append(recorded_source / "本机私密扫描词.txt")
    development = os.environ.get("XJ_DEVELOPMENT_SOURCE", "").strip()
    if development:
        candidates.append(
            Path(development).expanduser().resolve() / "本机私密扫描词.txt"
        )
    candidates.append((ROOT / "本机私密扫描词.txt").resolve())
    source = next(
        (path for path in candidates if path.is_file() and not path.is_symlink()),
        None,
    )
    if source is None:
        raise RuntimeError("缺少首次迁移所需的本机私密扫描词")
    PRIVATE_TERMS.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = PRIVATE_TERMS.with_name(f".{PRIVATE_TERMS.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o600)
        os.replace(temporary, PRIVATE_TERMS)
    finally:
        temporary.unlink(missing_ok=True)


def _迁移活动指针() -> str:
    # 先从现有锁读取当前版本，再把同一份正式副本登记到独立活动指针。
    # 不能继续把旧开发区锁当作新宿主的真源。
    version = 当前已发布版本(LOCK_PATH)
    if not version:
        raise RuntimeError("尚未发布正式版本，不能安装正式宿主运行面")
    if ACTIVE_LOCK != LOCK_PATH:
        if ACTIVE_LOCK.is_file():
            active_version = 当前已发布版本(ACTIVE_LOCK)
            if active_version != version:
                raise RuntimeError("独立活动版本指针与当前正式版本不一致，需先恢复")
        else:
            切换当前版本(version, lock_path=ACTIVE_LOCK)
    elif not ACTIVE_LOCK.is_file():
        切换当前版本(version, lock_path=ACTIVE_LOCK)
    return version


def _环境() -> dict[str, str]:
    """Environment used by explicit maintenance commands, never by live imports."""
    if not PRIVATE_TERMS.is_file() or PRIVATE_TERMS.is_symlink():
        raise RuntimeError(f"缺少独立运行目录中的私密扫描词：{PRIVATE_TERMS}")
    identity = 核对独立运行环境()
    version = 当前已发布版本(ACTIVE_LOCK)
    if not version:
        raise RuntimeError("尚未发布正式版本")
    docker_path = shutil.which("docker") or next(
        (
            str(path)
            for path in (
                Path("/opt/homebrew/bin/docker"),
                Path("/usr/local/bin/docker"),
                Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            )
            if path.is_file()
        ),
        "",
    )
    if not docker_path:
        raise RuntimeError("找不到 Docker CLI")
    return {
        "XJ_REGISTRY_PATH": str(RUNTIME / "data" / "registry.sqlite3"),
        "XJ_INSTANCE_DIR": str(RUNTIME / "instance"),
        "XJ_POLICY_DIR": str(RUNTIME / "policy"),
        "XJ_PRIVATE_TERMS_FILE": str(PRIVATE_TERMS),
        "XJ_COMPANY_IMAGE": f"xj-company:{version}",
        "XJ_RUNNER_IMAGE": f"xj-runner:{version}",
        "XJ_SUPERVISOR_HOST": HOST,
        "XJ_SUPERVISOR_PORT": str(PORT),
        "XJ_ADMIN_PORT": str(ADMIN_PORT),
        "XJ_RERANK_HOST": HOST,
        "XJ_RERANK_PORT": str(RERANK_PORT),
        "XJ_SUPERVISOR_TOKEN": 读取平台("supervisor-token"),
        "XJ_RERANK_TOKEN": 读取平台("rerank-token"),
        "XJ_PUBLIC_URL": 读取平台("public-url").rstrip("/"),
        "XJ_RELEASE_LOCK": str(ACTIVE_LOCK),
        "XJ_RELEASE_STORE": str(RELEASE_STORE),
        "XJ_HOST_RUNTIME_MANIFEST": str(HOST_IDENTITY),
        "XJ_HOST_PYTHON_VERSION": str(identity["python_version"]),
        "XJ_HOST_DEPENDENCY_SHA256": str(identity["pip_freeze_sha256"]),
        "XJ_DOCKER_BIN": docker_path,
        "PATH": _宿主工具路径(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _旧单进程环境() -> dict[str, str]:
    """Compatibility only: keep the already-installed supervisor alive pre-migration."""
    _准备私密扫描词()
    private_terms = PRIVATE_TERMS
    version = 当前已发布版本()
    docker_path = shutil.which("docker") or next(
        (
            str(path)
            for path in (
                Path("/opt/homebrew/bin/docker"),
                Path("/usr/local/bin/docker"),
                Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            )
            if path.is_file()
        ),
        "",
    )
    if not docker_path:
        raise RuntimeError("找不到 Docker CLI")
    return {
        "XJ_REGISTRY_PATH": str(RUNTIME / "data" / "registry.sqlite3"),
        "XJ_INSTANCE_DIR": str(RUNTIME / "instance"),
        "XJ_POLICY_DIR": str(RUNTIME / "policy"),
        "XJ_PRIVATE_TERMS_FILE": str(private_terms),
        "XJ_COMPANY_IMAGE": f"xj-company:{version}" if version else "xj-company:dev",
        "XJ_RUNNER_IMAGE": f"xj-runner:{version}" if version else "xj-runner:dev",
        "XJ_SUPERVISOR_HOST": HOST,
        "XJ_SUPERVISOR_PORT": str(PORT),
        "XJ_ADMIN_PORT": str(ADMIN_PORT),
        "XJ_RERANK_HOST": HOST,
        "XJ_RERANK_PORT": str(RERANK_PORT),
        "XJ_SUPERVISOR_TOKEN": 读取平台("supervisor-token"),
        "XJ_RERANK_TOKEN": 读取平台("rerank-token"),
        "XJ_PUBLIC_URL": 读取平台("public-url").rstrip("/"),
        # 旧单进程仍必须继续读取旧锁，直到显式迁移完成。
        "XJ_RELEASE_LOCK": str(LOCK_PATH),
        "XJ_RELEASE_STORE": str(RELEASE_STORE),
        "XJ_DEVELOPMENT_SOURCE": str(_开发源目录()),
        "XJ_HOST_BOUNDARY_MODE": "legacy",
        "XJ_DOCKER_BIN": docker_path,
        "PATH": _宿主工具路径(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def 服务入口(role: str | None = None) -> None:
    if role is None:
        for path in (
            RUNTIME / "data", RUNTIME / "instance", RUNTIME / "policy", RUNTIME / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = os.environ.copy()
        env.update(_旧单进程环境())
        os.execve(
            sys.executable,
            [sys.executable, "-m", "多用户.控制面", "legacy"],
            env,
        )
    核对独立运行环境()
    env = os.environ.copy()
    env.update(_启动项文档(role)["EnvironmentVariables"])
    os.execve(
        str(HOST_PYTHON),
        [
            str(HOST_PYTHON), str(LAUNCHER), "serve", "--role", role,
            "--active-lock", str(ACTIVE_LOCK),
        ],
        env,
    )


def _候选宿主路径(runtime: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    root = (RUNTIME / "candidate-host").resolve()
    # An ordinary formal switch keeps the already-approved host environment;
    # only an approved candidate supplies a candidate-host mapping.
    if not any(str(runtime.get(key) or "").strip() for key in ("python", "launcher", "manifest")):
        return HOST_PYTHON.resolve(), LAUNCHER.resolve(), HOST_IDENTITY.resolve()
    values = tuple(Path(str(runtime.get(key) or "")).expanduser().resolve() for key in ("python", "launcher", "manifest"))
    if any(not path.is_file() or path.is_symlink() or root not in path.parents for path in values):
        raise RuntimeError("候选宿主环境路径无效，拒绝写入正式启动描述")
    return values  # type: ignore[return-value]


def _写启动项(
    *,
    host_python: Path = HOST_PYTHON,
    launcher: Path = LAUNCHER,
    manifest: Path = HOST_IDENTITY,
    active_lock: Path = ACTIVE_LOCK,
) -> None:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    for role, path in PLISTS.items():
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(
                plistlib.dumps(
                    _启动项文档(
                        role,
                        host_python=host_python,
                        launcher=launcher,
                        manifest=manifest,
                        active_lock=active_lock,
                    ),
                    fmt=plistlib.FMT_XML, sort_keys=True,
                )
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    temporary = FALLBACK_PLIST.with_name(f".{FALLBACK_PLIST.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(
            plistlib.dumps(
                # The emergency fallback is deliberately independent from the
                # candidate interpreter.  Candidate role plists may be
                # replaced during a switch, but this one must always be able
                # to start the currently active sealed version.
                _冻结正式回退文档(
                    host_python=HOST_PYTHON,
                    launcher=LAUNCHER,
                    manifest=HOST_IDENTITY,
                    active_lock=ACTIVE_LOCK,
                ),
                fmt=plistlib.FMT_XML, sort_keys=True,
            )
        )
        temporary.chmod(0o600)
        os.replace(temporary, FALLBACK_PLIST)
    finally:
        temporary.unlink(missing_ok=True)


def _确保冻结正式回退文档() -> None:
    """Ensure a stable fallback descriptor exists before a switch is staged.

    The fallback must use the fixed host runtime and the active release pointer,
    not a candidate interpreter.  It is intentionally written separately so a
    migration can always restore the previous sealed version if the candidate
    host fails before the new admin service comes back.
    """
    if FALLBACK_PLIST.is_symlink():
        raise RuntimeError("冻结正式回退启动项是符号链接，拒绝覆盖")
    if FALLBACK_PLIST.is_file() and FALLBACK_PLIST.stat().st_size:
        try:
            document = plistlib.loads(FALLBACK_PLIST.read_bytes())
            arguments = document.get("ProgramArguments") if isinstance(document, dict) else None
            environment = document.get("EnvironmentVariables") if isinstance(document, dict) else None
            if (
                isinstance(document, dict)
                and document.get("Label") == FALLBACK_LABEL
                and isinstance(arguments, list)
                and arguments[:3] == [str(HOST_PYTHON), str(LAUNCHER), "serve-legacy"]
                and isinstance(environment, dict)
                and environment.get("XJ_RELEASE_LOCK") == str(ACTIVE_LOCK)
                and environment.get("XJ_PRIVATE_TERMS_FILE") == str(PRIVATE_TERMS)
                and "XJ_DEVELOPMENT_SOURCE" not in environment
            ):
                return
        except (OSError, plistlib.InvalidFileException, TypeError, ValueError):
            pass
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (HOST_PYTHON, LAUNCHER, HOST_IDENTITY)
    ):
        raise RuntimeError("固定正式宿主运行环境尚未准备，无法建立回退启动项")
    FALLBACK_PLIST.parent.mkdir(parents=True, exist_ok=True)
    temporary = FALLBACK_PLIST.with_name(
        f".{FALLBACK_PLIST.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_bytes(
            plistlib.dumps(
                _冻结正式回退文档(
                    host_python=HOST_PYTHON,
                    launcher=LAUNCHER,
                    manifest=HOST_IDENTITY,
                    active_lock=ACTIVE_LOCK,
                ),
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        )
        temporary.chmod(0o600)
        os.replace(temporary, FALLBACK_PLIST)
    finally:
        temporary.unlink(missing_ok=True)


def 暂存候选正式宿主运行面(
    runtime: Mapping[str, Any],
    switch_id: str,
    *,
    previous_version: str = "",
) -> dict[str, Any]:
    """备份当前启动描述并写入候选描述；只写文件，不加载服务。"""
    if not switch_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in switch_id):
        raise RuntimeError("宿主切换编号无效")
    host_python, launcher, manifest = _候选宿主路径(runtime)
    backup = (RUNTIME / "data" / "host-runtime-backups" / switch_id).resolve()
    if backup.exists():
        raise RuntimeError("同一宿主切换已经有暂存启动描述")
    try:
        existing = json.loads(HOST_RUNTIME_SWITCH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        existing = None
    # ``host-runtime-switch.json`` records the transaction that installed the
    # current host descriptors.  A successfully closed transaction is the
    # rollback baseline for the next release, not an operation that is still
    # running.  Older releases called that stable state ``active``; current
    # releases call it ``committed``.  Only the three genuinely unfinished
    # states may block a new snapshot.
    if isinstance(existing, dict) and existing.get("status") in {
        "staging", "staged", "host-ready",
    }:
        if existing.get("switch_id") != switch_id:
            raise RuntimeError("已有宿主运行面切换尚未收口")
    # These are host-local prerequisites, not candidate files.  Prepare them
    # before taking the snapshot so a failed switch can always start the old
    # sealed runtime and let its admin process reconcile the deployment.
    _准备私密扫描词()
    # 固定回退入口也必须先拿到当前已经验收过的启动器代码。否则候选切换
    # 失败后，会由磁盘上几天前的旧启动器接管，旧代码可能无法识别现役
    # 封存版本，形成“主切换失败、回退也失败”。
    _准备启动器()
    _确保冻结正式回退文档()
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    paths = {**PLISTS, "fallback": FALLBACK_PLIST}
    active_backup = backup / "active-release.json"
    state: dict[str, Any] = {
        "schema": 1,
        "switch_id": switch_id,
        "status": "staging",
        "runtime": {
            "python": str(host_python),
            "launcher": str(launcher),
            "manifest": str(manifest),
        },
        "backup_dir": str(backup),
        "previous_version": previous_version,
        "active_lock_backup": str(active_backup),
        "staged_at": time.time(),
    }
    # Persist intent before replacing any LaunchAgent file.  A crash in the
    # middle of staging therefore leaves a record that startup recovery can
    # inspect instead of an unexplained half-written runtime.
    _写JSON(HOST_RUNTIME_SWITCH, state)
    try:
        # The launcher refuses to start without this host-local file.  Check
        # again after provisioning so a deleted or replaced file cannot turn
        # into a late failure after the candidate runtime is already staged.
        if not PRIVATE_TERMS.is_file() or PRIVATE_TERMS.is_symlink():
            raise RuntimeError(f"缺少独立运行目录中的私密扫描词：{PRIVATE_TERMS}")
        for role, path in paths.items():
            if path.is_symlink():
                raise RuntimeError(f"{role} 启动描述是符号链接，拒绝覆盖")
            target = backup / f"{role}.plist"
            if path.is_file():
                shutil.copy2(path, target)
            else:
                target.write_bytes(b"")
                target.chmod(0o600)
        # The main active pointer has already moved to the candidate by the
        # time the host switch is staged.  Save the previous formal pointer
        # explicitly so an asynchronous helper failure can restore the same
        # version before it starts the frozen fallback.
        if previous_version and previous_version != "dev":
            切换当前版本(previous_version, lock_path=active_backup)
        elif previous_version == "dev" and LEGACY_LOCK_PATH.is_file() and not LEGACY_LOCK_PATH.is_symlink():
            shutil.copy2(LEGACY_LOCK_PATH, active_backup)
        elif ACTIVE_LOCK.is_file() and not ACTIVE_LOCK.is_symlink():
            shutil.copy2(ACTIVE_LOCK, active_backup)
        else:
            active_backup.write_bytes(b"")
            active_backup.chmod(0o600)
        _写启动项(host_python=host_python, launcher=launcher, manifest=manifest)
        state = {**state, "status": "staged"}
        _写JSON(HOST_RUNTIME_SWITCH, state)
        return state
    except Exception:
        # Restore the exact old descriptions if writing the candidate set failed.
        for role, path in paths.items():
            source = backup / f"{role}.plist"
            if source.stat().st_size == 0 and not path.exists():
                continue
            if source.is_file() and source.stat().st_size:
                shutil.copy2(source, path)
            else:
                path.unlink(missing_ok=True)
        active_backup.unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
        try:
            _写JSON(HOST_RUNTIME_SWITCH, {
                **state,
                "status": "restore-failed",
                "error": "候选宿主运行面暂存失败，已尝试恢复原启动描述",
                "restored_at": time.time(),
            })
        except Exception:
            pass
        raise


def 恢复暂存候选正式宿主(switch_id: str) -> dict[str, Any]:
    try:
        state = json.loads(HOST_RUNTIME_SWITCH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("候选宿主暂存记录无法读取") from exc
    if state.get("schema") != 1 or state.get("switch_id") != switch_id:
        raise RuntimeError("候选宿主暂存记录不匹配")
    backup = Path(str(state.get("backup_dir") or "")).expanduser().resolve()
    allowed = (RUNTIME / "data" / "host-runtime-backups").resolve()
    if allowed not in backup.parents or not backup.is_dir():
        raise RuntimeError("候选宿主回退目录无效")
    paths = {**PLISTS, "fallback": FALLBACK_PLIST}
    for role, path in paths.items():
        source = backup / f"{role}.plist"
        if source.stat().st_size:
            shutil.copy2(source, path)
        else:
            path.unlink(missing_ok=True)
    active_source = backup / "active-release.json"
    if active_source.is_file() and active_source.stat().st_size:
        if ACTIVE_LOCK.is_symlink():
            raise RuntimeError("正式活动版本指针是符号链接，拒绝恢复")
        temporary = ACTIVE_LOCK.with_name(f".{ACTIVE_LOCK.name}.{os.getpid()}.restore")
        try:
            shutil.copy2(active_source, temporary)
            os.replace(temporary, ACTIVE_LOCK)
        finally:
            temporary.unlink(missing_ok=True)
    elif active_source.is_file():
        ACTIVE_LOCK.unlink(missing_ok=True)
    state = {**state, "status": "restored", "restored_at": time.time()}
    _写JSON(HOST_RUNTIME_SWITCH, state)
    return state


def 确认候选正式宿主已生效(switch_id: str) -> dict[str, Any]:
    try:
        state = json.loads(HOST_RUNTIME_SWITCH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("候选宿主暂存记录无法读取") from exc
    if state.get("schema") != 1 or state.get("switch_id") != switch_id:
        raise RuntimeError("候选宿主暂存记录不匹配")
    if state.get("status") not in {"host-ready", "active", "committed"}:
        raise RuntimeError("候选宿主运行面尚未到可收口状态")
    now = time.time()
    committed = {
        **state,
        "status": "committed",
        "activated_at": state.get("activated_at") or now,
        "committed_at": now,
    }
    _写JSON(HOST_RUNTIME_SWITCH, committed)
    return committed


def _角色健康(role: str) -> bool:
    port = PORTS[role]
    headers = (
        {"authorization": f"Bearer {读取平台('rerank-token')}"}
        if role == "rerank" else None
    )
    try:
        response = httpx.get(
            f"http://{HOST}:{port}/healthz",
            headers=headers,
            timeout=5,
            trust_env=False,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def 准备不切换() -> dict[str, Any]:
    """Prepare the fixed runtime and three plists without loading any service."""
    for path in (
        RUNTIME / "data", RUNTIME / "instance", RUNTIME / "policy",
        RUNTIME / "logs", RUNTIME / "status",
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _准备私密扫描词()
    identity = _准备独立运行环境()
    _准备启动器()
    version = _迁移活动指针()
    核对独立运行环境()
    _写启动项()
    return {
        "prepared": True,
        "loaded": False,
        "active_version": version,
        "python_version": identity["python_version"],
        "pip_freeze_sha256": identity["pip_freeze_sha256"],
        "requirements_sha256": identity["requirements_sha256"],
    }


def 安装() -> None:
    准备不切换()
    version = 当前已发布版本(ACTIVE_LOCK)
    if not version:
        raise RuntimeError("没有可安装的活动正式版本")
    active_source = RELEASE_STORE / "published" / version / "source"
    if not (active_source / "多用户" / "部署" / "宿主启动器.py").is_file():
        raise RuntimeError("当前封存版本不具备三角色宿主能力；只能随下一版发布迁移")

    domain = f"gui/{os.getuid()}"
    legacy_was_installed = LEGACY_PLIST.is_file()
    if legacy_was_installed:
        _run(["launchctl", "bootout", f"{domain}/{LEGACY_LABEL}"], check=False)
        _run(["launchctl", "bootout", domain, str(LEGACY_PLIST)], check=False)
    # 冻结正式回退与三角色服务会监听同一组端口。回退启动项必须留在磁盘
    # 供失败恢复，但进入三角色运行面前必须先从 launchd 中卸载。否则两套
    # 服务会互相抢 37654/37655/37657，并被 KeepAlive 无限拉起。
    _run(["launchctl", "bootout", f"{domain}/{FALLBACK_LABEL}"], check=False)
    _run(["launchctl", "bootout", domain, str(FALLBACK_PLIST)], check=False)
    try:
        for role, path in PLISTS.items():
            _run(
                ["launchctl", "bootout", f"{domain}/{LABELS[role]}"],
                check=False,
            )
            _run(["launchctl", "bootout", domain, str(path)], check=False)
            _run(["launchctl", "bootstrap", domain, str(path)])
            _run(["launchctl", "kickstart", "-k", f"{domain}/{LABELS[role]}"])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if all(_角色健康(role) for role in LABELS):
                LEGACY_PLIST.unlink(missing_ok=True)
                print(f"三个正式宿主服务已启动；账户管理：http://{HOST}:{ADMIN_PORT}")
                return
            time.sleep(0.5)
        raise RuntimeError(f"宿主服务未全部恢复，请查看 {RUNTIME / 'logs'}")
    except Exception:
        # 不只停 started：bootstrap 可能已经绑定端口、但还没来得及记入
        # started 就报错。失败时必须把三套描述全部停干净，再恢复唯一回退。
        for role in reversed(tuple(PLISTS)):
            _run(
                ["launchctl", "bootout", f"{domain}/{LABELS[role]}"],
                check=False,
            )
            _run(
                ["launchctl", "bootout", domain, str(PLISTS[role])],
                check=False,
            )
        if FALLBACK_PLIST.is_file() and not FALLBACK_PLIST.is_symlink():
            _run(
                ["launchctl", "bootout", f"{domain}/{FALLBACK_LABEL}"],
                check=False,
            )
            _run(["launchctl", "bootstrap", domain, str(FALLBACK_PLIST)], check=False)
            _run(
                ["launchctl", "kickstart", "-k", f"{domain}/{FALLBACK_LABEL}"],
                check=False,
            )
        elif legacy_was_installed and LEGACY_PLIST.is_file():
            _run(["launchctl", "bootstrap", domain, str(LEGACY_PLIST)], check=False)
            _run(
                ["launchctl", "kickstart", "-k", f"{domain}/{LEGACY_LABEL}"],
                check=False,
            )
        raise


def 安装开发管理(
    source: Path | None = None,
    *,
    replace_development_source: bool = False,
) -> None:
    """Install/reload only the owner's development management service.

    It never rewrites or restarts the formal control, admin, or rerank
    services.  The formal user plane therefore keeps its published version
    while the owner can inspect and manufacture the next development version.
    """
    selected = (source or ROOT).expanduser().resolve()
    source = _登记开发源(
        selected, replace=replace_development_source,
    )
    if not (source / "多用户" / "控制面" / "管理服务.py").is_file():
        raise RuntimeError("管理员开发目录缺少管理服务")
    if not 当前已发布版本(ACTIVE_LOCK):
        raise RuntimeError("没有可供开发管理后台读取的正式版本")
    核对独立运行环境()
    _准备启动器()
    _准备私密扫描词()
    document = _开发管理启动项文档(
        host_python=HOST_PYTHON,
        launcher=LAUNCHER,
        manifest=HOST_IDENTITY,
        active_lock=ACTIVE_LOCK,
        development_source=source,
    )
    DEVELOPMENT_ADMIN_PLIST.parent.mkdir(parents=True, exist_ok=True)
    temporary = DEVELOPMENT_ADMIN_PLIST.with_name(
        f".{DEVELOPMENT_ADMIN_PLIST.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True))
        temporary.chmod(0o600)
        os.replace(temporary, DEVELOPMENT_ADMIN_PLIST)
    finally:
        temporary.unlink(missing_ok=True)

    domain = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", domain, str(DEVELOPMENT_ADMIN_PLIST)], check=False)
    _run(["launchctl", "bootstrap", domain, str(DEVELOPMENT_ADMIN_PLIST)])
    _run([
        "launchctl", "kickstart", "-k",
        f"{domain}/{DEVELOPMENT_ADMIN_LABEL}",
    ])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"http://{HOST}:{DEVELOPMENT_ADMIN_PORT}/healthz",
                timeout=2,
                trust_env=False,
            )
            if response.status_code == 200:
                print(f"开发管理后台已启动：http://{HOST}:{DEVELOPMENT_ADMIN_PORT}")
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"开发管理后台未在 60 秒内恢复，请查看 {RUNTIME / 'logs' / 'development-admin-error.log'}"
    )


def 重启助手(restart_id: str, *, legacy: bool = False) -> None:
    try:
        if legacy:
            触发旧系统重启(restart_id)
        else:
            触发系统重启(restart_id)
    except BaseException as exc:
        标记失败(restart_id, exc)


def 卸载() -> None:
    domain = f"gui/{os.getuid()}"
    for role, path in PLISTS.items():
        _run(["launchctl", "bootout", domain, str(path)], check=False)
        path.unlink(missing_ok=True)
    _run(["launchctl", "bootout", domain, str(LEGACY_PLIST)], check=False)
    LEGACY_PLIST.unlink(missing_ok=True)
    _run(["launchctl", "bootout", domain, str(DEVELOPMENT_ADMIN_PLIST)], check=False)
    DEVELOPMENT_ADMIN_PLIST.unlink(missing_ok=True)
    print("宿主服务已卸载；账户数据、正式版本和独立运行环境均未删除。")


def 状态() -> int:
    results = {role: _角色健康(role) for role in LABELS}
    ok = all(results.values())
    print("运行中" if ok else "异常：" + "、".join(
        role for role, healthy in results.items() if not healthy
    ))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "serve", "prepare", "install", "install-development-admin",
            "uninstall", "status", "kickstart",
        ),
    )
    parser.add_argument("restart_id", nargs="?")
    parser.add_argument("--role", choices=tuple(LABELS))
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--replace-development-source", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        服务入口(args.role)
    elif args.command == "prepare":
        准备不切换()
        print("独立宿主运行环境已准备；尚未安装或重启任何服务。")
    elif args.command == "install":
        安装()
    elif args.command == "install-development-admin":
        安装开发管理(
            args.source,
            replace_development_source=args.replace_development_source,
        )
    elif args.command == "uninstall":
        卸载()
    elif args.command == "status":
        return 状态()
    elif args.command == "kickstart":
        if not args.restart_id:
            parser.error("kickstart 需要 restart_id")
        重启助手(args.restart_id, legacy=args.legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

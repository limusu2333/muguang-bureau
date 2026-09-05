"""本机公司的对外访问开关。

只管理多用户公司的公网入口，不停止本机公司，也不删除任何用户资料。
所有命令均为固定参数，不接受浏览器传入的路径或命令。
"""
from __future__ import annotations

import json
import ipaddress
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


COMPANY = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = COMPANY
PLATFORM_PYTHON = PLATFORM_ROOT / ".venv" / "bin" / "python3"
TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
LOCAL_URL = "http://127.0.0.1:37656"
SUNNY = Path.home() / "Library" / "Application Support" / "小酒" / "公网通道" / "sunnyngrok" / "sunny"
SUNNY_RUNTIME = SUNNY.parent
SUNNY_TOKEN_FILE = SUNNY_RUNTIME / "client-token"
SUNNY_STATE_FILE = SUNNY_RUNTIME / "client-state.json"
SUNNY_LOG_FILE = SUNNY_RUNTIME / "client.log"
SUNNY_PID_FILE = SUNNY_RUNTIME / "client.pid"

_操作锁 = threading.Lock()
_状态锁 = threading.Lock()
_当前操作 = ""
_操作阶段 = ""
_操作说明 = ""
_操作开始时间 = 0.0
_操作步骤: list[str] = []
_操作步骤索引 = 0


class 对外访问错误(RuntimeError):
    pass


def _设置操作(
    value: str,
    *,
    阶段: str = "",
    说明: str = "",
    步骤: tuple[str, ...] | None = None,
    步骤索引: int | None = None,
) -> None:
    global _当前操作, _操作阶段, _操作说明, _操作开始时间, _操作步骤, _操作步骤索引
    with _状态锁:
        _当前操作 = value
        if not value:
            _操作阶段 = ""
            _操作说明 = ""
            _操作开始时间 = 0.0
            _操作步骤 = []
            _操作步骤索引 = 0
            return
        if not _操作开始时间:
            _操作开始时间 = time.time()
        if 阶段:
            _操作阶段 = 阶段
        if 说明:
            _操作说明 = 说明
        if 步骤 is not None:
            _操作步骤 = list(步骤)
        if 步骤索引 is not None:
            _操作步骤索引 = 步骤索引


def _读取操作() -> str:
    with _状态锁:
        return _当前操作


def _读取操作详情() -> dict[str, Any]:
    with _状态锁:
        if not _当前操作:
            return {}
        return {
            "状态": _当前操作,
            "阶段": _操作阶段 or _当前操作,
            "说明": _操作说明 or "正在处理，请稍候。",
            "开始时间": _操作开始时间,
            "步骤": list(_操作步骤),
            "步骤索引": _操作步骤索引,
        }


def _运行(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise 对外访问错误("启动等待超时，请稍后再试。") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "命令执行失败").strip().splitlines()
        raise 对外访问错误(detail[-1][:300] if detail else "命令执行失败")
    return result


def _公网网址() -> str:
    result = _运行([
        "/usr/bin/security", "find-generic-password",
        "-s", "xj-multiuser-platform", "-a", "public-url", "-w",
    ])
    url = result.stdout.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise 对外访问错误("公网网址尚未配置完成。")
    return url


def _公网提供方() -> str:
    result = _运行([
        "/usr/bin/security", "find-generic-password",
        "-s", "xj-multiuser-platform", "-a", "public-provider", "-w",
    ])
    provider = result.stdout.strip()
    if provider not in {"tailscale", "sunny"}:
        raise 对外访问错误("公网入口方式尚未配置完成。")
    return provider


def _网址可用(url: str, timeout: float = 3.0, *, 尝试: int = 1) -> bool:
    for index in range(max(1, 尝试)):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "XJ-Owner-Control/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return 200 <= response.status < 500
        except Exception:  # noqa: BLE001
            if index + 1 < 尝试:
                time.sleep(0.25)
    return False


def _公网可用(url: str, timeout: float = 5.0) -> bool:
    """绕过本机 MagicDNS，按外部用户实际拿到的公网地址检查公网通道。"""
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host:
        return False
    addresses: list[str] = []
    for resolver in ("1.1.1.1", "8.8.8.8"):
        result = _运行(
            ["/usr/bin/dig", f"@{resolver}", "+short", "A", host],
            check=False,
            timeout=8,
        )
        for line in result.stdout.splitlines():
            value = line.strip()
            try:
                if ipaddress.ip_address(value).version == 4 and value not in addresses:
                    addresses.append(value)
            except ValueError:
                continue
        if addresses:
            break
    for address in addresses:
        result = _运行([
            "/usr/bin/curl", "--silent", "--show-error", "--output", "/dev/null",
            "--connect-timeout", "3", "--max-time", str(max(4, int(timeout))),
            "--resolve", f"{host}:443:{address}", "--write-out", "%{http_code}", url,
        ], check=False, timeout=max(8, int(timeout) + 2))
        try:
            code = int(result.stdout.strip())
        except ValueError:
            continue
        if 200 <= code < 500:
            return True
    return False


def _写Sunny令牌() -> None:
    result = _运行([
        "/usr/bin/security", "find-generic-password",
        "-s", "xj-multiuser-platform", "-a", "sunny-token", "-w",
    ])
    token = result.stdout.strip()
    if not token or len(token) > 4096 or any(ord(char) < 32 for char in token):
        raise 对外访问错误("SunnyNgrok 客户端令牌尚未配置完成。")
    SUNNY_RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SUNNY_TOKEN_FILE.is_symlink():
        raise 对外访问错误("SunnyNgrok 令牌文件不是安全的普通文件。")
    temporary = SUNNY_TOKEN_FILE.with_name(f".{SUNNY_TOKEN_FILE.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps({"token": token}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, SUNNY_TOKEN_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _Sunny进程(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    result = _运行(["/bin/ps", "-p", str(pid), "-o", "command="], check=False, timeout=10)
    return result.returncode == 0 and result.stdout.strip().startswith(str(SUNNY))


def _Sunny进程列表() -> list[int]:
    result = _运行(["/bin/ps", "-axo", "pid=,command="], check=False, timeout=10)
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[1].startswith(str(SUNNY)):
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _Sunny运行中() -> bool:
    try:
        pid = int(SUNNY_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0
    if _Sunny进程(pid):
        return True
    return bool(_Sunny进程列表())


def _Sunny日志尾部() -> str:
    try:
        lines = SUNNY_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "；".join(lines[-3:])[:500]


def _确保Sunny() -> None:
    if not SUNNY.is_file() or SUNNY.is_symlink():
        raise 对外访问错误("没有找到 SunnyNgrok 客户端。")
    existing = _Sunny进程列表()
    if existing:
        SUNNY_RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        SUNNY_PID_FILE.write_text(str(existing[0]) + "\n", encoding="utf-8")
        SUNNY_PID_FILE.chmod(0o600)
        return
    _写Sunny令牌()
    SUNNY_RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_handle = None
    try:
        log_handle = SUNNY_LOG_FILE.open("ab")
        process = subprocess.Popen(
            [
                str(SUNNY), "--token-file", str(SUNNY_TOKEN_FILE),
                "--state-file", str(SUNNY_STATE_FILE),
                "--log", "stdout", "--log-level", "info",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise 对外访问错误("SunnyNgrok 客户端启动失败。") from exc
    finally:
        if log_handle is not None:
            log_handle.close()
    SUNNY_PID_FILE.write_text(str(process.pid) + "\n", encoding="utf-8")
    SUNNY_PID_FILE.chmod(0o600)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not _Sunny进程(process.pid):
            detail = _Sunny日志尾部()
            suffix = f"：{detail}" if detail else ""
            raise 对外访问错误(f"SunnyNgrok 客户端已退出{suffix}")
        time.sleep(0.25)


def _停止Sunny() -> None:
    pids = _Sunny进程列表()
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            continue
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(_Sunny进程(pid) for pid in pids):
        time.sleep(0.25)
    for pid in pids:
        if _Sunny进程(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    SUNNY_PID_FILE.unlink(missing_ok=True)


def _tailscale状态() -> dict[str, Any]:
    if not TAILSCALE.is_file():
        return {}
    result = _运行([str(TAILSCALE), "status", "--json"], check=False, timeout=10)
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _通道状态() -> dict[str, Any]:
    if not TAILSCALE.is_file():
        return {}
    result = _运行([str(TAILSCALE), "funnel", "status", "--json"], check=False, timeout=10)
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _通道已开启(value: dict[str, Any]) -> bool:
    allowed = value.get("AllowFunnel")
    if not isinstance(allowed, dict) or not any(allowed.values()):
        return False
    web = value.get("Web")
    if not isinstance(web, dict):
        return False
    for site in web.values():
        if not isinstance(site, dict):
            continue
        handlers = site.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        for handler in handlers.values():
            if isinstance(handler, dict) and handler.get("Proxy") == LOCAL_URL:
                return True
    return False


def 状态(*, 检查公网: bool = True) -> dict[str, Any]:
    操作详情 = _读取操作详情()
    操作 = str(操作详情.get("状态", ""))
    try:
        网址 = _公网网址()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "状态": 操作 or "未配置",
            "网址": "",
            "操作中": bool(操作),
            "说明": str(操作详情.get("说明") or exc),
            "操作说明": str(操作详情.get("说明") or exc),
            "操作阶段": 操作详情.get("阶段", ""),
            "操作开始时间": 操作详情.get("开始时间", 0),
            "已用秒": max(0, int(time.time() - float(操作详情.get("开始时间", time.time())))) if 操作 else 0,
            "操作步骤": 操作详情.get("步骤", []),
            "操作步骤索引": 操作详情.get("步骤索引", 0),
        }

    # 开启/关闭期间只读进度，不重复做外网探测，避免轮询反过来拖慢操作。
    if 操作:
        开始时间 = float(操作详情.get("开始时间") or time.time())
        return {
            "ok": False,
            "状态": 操作,
            "网址": 网址,
            "操作中": True,
            "说明": 操作详情.get("说明", "正在处理，请稍候。"),
            "操作说明": 操作详情.get("说明", "正在处理，请稍候。"),
            "操作阶段": 操作详情.get("阶段", 操作),
            "操作开始时间": 开始时间,
            "已用秒": max(0, int(time.time() - 开始时间)),
            "操作步骤": 操作详情.get("步骤", []),
            "操作步骤索引": 操作详情.get("步骤索引", 0),
        }

    try:
        provider = _公网提供方()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "状态": "异常",
            "网址": 网址,
            "操作中": False,
            "说明": str(exc),
            "操作说明": str(exc),
            "操作阶段": "",
            "操作开始时间": 0,
            "已用秒": 0,
            "操作步骤": [],
            "操作步骤索引": 0,
        }

    本机服务 = _网址可用(LOCAL_URL, timeout=2.0)
    if provider == "sunny":
        通道 = _Sunny运行中()
        公网可用 = _公网可用(网址, timeout=5.0) if 检查公网 and 本机服务 and 通道 else 通道
        在线 = 本机服务 and 通道 and 公网可用
    else:
        ts = _tailscale状态()
        ts在线 = ts.get("BackendState") == "Running" and bool((ts.get("Self") or {}).get("Online"))
        通道 = _通道已开启(_通道状态())
        公网可用 = _公网可用(网址, timeout=5.0) if 检查公网 and 本机服务 and ts在线 and 通道 else 通道
        在线 = 本机服务 and ts在线 and 通道 and 公网可用

    if 在线:
        当前状态, 说明 = "已开启", "其他人现在可以通过这个网址登录。"
    elif 通道 and not 本机服务:
        当前状态, 说明 = "异常", "用户公司没有启动，请重新开启。"
    elif 通道 and not 公网可用:
        当前状态, 说明 = "异常", "公网连接暂时不可用，请重新开启。"
    else:
        当前状态, 说明 = "已关闭", "其他人现在无法访问。"
    return {
        "ok": 在线,
        "状态": 当前状态,
        "网址": 网址,
        "操作中": bool(操作),
        "说明": 说明,
        "本机服务": 本机服务,
        "外网通道": 通道,
        "操作阶段": "",
        "操作开始时间": 0,
        "已用秒": 0,
        "操作步骤": [],
        "操作步骤索引": 0,
    }


def _docker命令() -> str:
    found = shutil.which("docker")
    if found:
        return found
    for path in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if Path(path).is_file():
            return path
    raise 对外访问错误("没有找到 Docker，请确认 Docker 已安装。")


def _确保Docker() -> None:
    docker = _docker命令()
    if _运行([docker, "info"], check=False, timeout=10).returncode == 0:
        return
    if not Path("/Applications/Docker.app").exists():
        raise 对外访问错误("Docker 没有运行，也没有找到 Docker 应用。")
    _运行(["/usr/bin/open", "-gja", "Docker"], timeout=10)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if _运行([docker, "info"], check=False, timeout=10).returncode == 0:
            return
        time.sleep(2)
    raise 对外访问错误("Docker 启动超时，请打开 Docker 看一下是否有提示。")


def _确保用户公司() -> None:
    if not PLATFORM_PYTHON.is_file() or not PLATFORM_ROOT.is_dir():
        raise 对外访问错误("多用户公司的启动文件不完整。")
    if _网址可用(LOCAL_URL, timeout=2.0):
        current = _运行(
            [str(PLATFORM_PYTHON), "-m", "多用户.部署.平台命令", "runtime-current"],
            cwd=PLATFORM_ROOT,
            timeout=60,
            check=False,
        )
        if current.returncode == 0:
            return
    provider = _公网提供方()
    _运行(
        [str(PLATFORM_PYTHON), "-m", "多用户.部署.平台命令", "start", "--provider", provider],
        cwd=PLATFORM_ROOT,
        timeout=900,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _网址可用(LOCAL_URL, timeout=2.0):
            return
        time.sleep(2)
    raise 对外访问错误("用户公司已经启动，但入口没有正常响应。")


def _确保Tailscale() -> None:
    if not TAILSCALE.is_file():
        raise 对外访问错误("没有找到 Tailscale，请确认它已安装。")
    current = _tailscale状态()
    if current.get("BackendState") == "Running" and bool((current.get("Self") or {}).get("Online")):
        return
    _运行(["/usr/bin/open", "-gja", "Tailscale"], timeout=10)
    time.sleep(2)
    result = _运行([str(TAILSCALE), "up"], check=False, timeout=45)
    if result.returncode != 0:
        raise 对外访问错误("Tailscale 需要重新登录，请打开它完成登录后再试。")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = _tailscale状态()
        if current.get("BackendState") == "Running" and bool((current.get("Self") or {}).get("Online")):
            return
        time.sleep(2)
    raise 对外访问错误("Tailscale 没有连上，请打开它看一下是否需要登录。")


def 开启() -> dict[str, Any]:
    if not _操作锁.acquire(blocking=False):
        raise 对外访问错误("正在处理上一次操作，请稍候。")
    步骤 = ("准备检查", "容器服务", "用户公司", "网络连接", "公网通道", "外网验证")
    _设置操作("正在开启", 阶段="准备检查", 说明="正在确认公网网址…", 步骤=步骤, 步骤索引=0)
    try:
        provider = _公网提供方()
        网址 = _公网网址()
        _设置操作("正在开启", 阶段="容器服务", 说明="正在确认 Docker 是否可用…", 步骤索引=1)
        _确保Docker()
        _设置操作("正在开启", 阶段="用户公司", 说明="正在启动或核对用户公司…", 步骤索引=2)
        _确保用户公司()
        if provider == "sunny":
            _设置操作("正在开启", 阶段="网络连接", 说明="正在连接 SunnyNgrok…", 步骤索引=3)
            _确保Sunny()
        else:
            _设置操作("正在开启", 阶段="网络连接", 说明="正在连接 Tailscale…", 步骤索引=3)
            _确保Tailscale()
        _设置操作("正在开启", 阶段="公网通道", 说明="正在开启公网通道…", 步骤索引=4)
        if provider == "tailscale":
            _运行([str(TAILSCALE), "funnel", "--bg", "--yes", "37656"], timeout=45)
        _设置操作("正在开启", 阶段="外网验证", 说明="公网通道已开启，正在验证外部访问…", 步骤索引=5)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            通道 = _Sunny运行中() if provider == "sunny" else _通道已开启(_通道状态())
            if 通道 and _公网可用(网址, timeout=6.0):
                break
            time.sleep(2)
        else:
            raise 对外访问错误("对外通道已经开启，但公网网址暂时无法访问。")
    finally:
        _设置操作("")
        _操作锁.release()
    return 状态()


def 关闭() -> dict[str, Any]:
    if not _操作锁.acquire(blocking=False):
        raise 对外访问错误("正在处理上一次操作，请稍候。")
    步骤 = ("准备关闭", "关闭公网通道", "确认已关闭")
    _设置操作("正在关闭", 阶段="准备关闭", 说明="正在准备关闭公网入口…", 步骤=步骤, 步骤索引=0)
    try:
        provider = _公网提供方()
        if provider == "sunny":
            _设置操作("正在关闭", 阶段="关闭公网通道", 说明="正在关闭 SunnyNgrok 公网通道…", 步骤索引=1)
            _停止Sunny()
        else:
            if not TAILSCALE.is_file():
                raise 对外访问错误("没有找到 Tailscale。")
            _设置操作("正在关闭", 阶段="关闭公网通道", 说明="正在关闭 Tailscale 公网通道…", 步骤索引=1)
            _运行([str(TAILSCALE), "funnel", "reset"], check=False, timeout=30)
        _设置操作("正在关闭", 阶段="确认已关闭", 说明="正在确认其他人已经无法访问…", 步骤索引=2)
        if (provider == "sunny" and _Sunny运行中()) or (provider == "tailscale" and _通道已开启(_通道状态())):
            raise 对外访问错误("对外通道没有成功关闭，请再试一次。")
    finally:
        _设置操作("")
        _操作锁.release()
    return 状态(检查公网=False)

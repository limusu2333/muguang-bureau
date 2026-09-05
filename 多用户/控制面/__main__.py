"""Run one versioned host service, or the temporary legacy three-service host."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

import uvicorn


_角色 = {
    "control": ("多用户.控制面.服务:app", "XJ_SUPERVISOR_HOST", "XJ_SUPERVISOR_PORT", 37654),
    "admin": ("多用户.控制面.管理服务:app", "XJ_SUPERVISOR_HOST", "XJ_ADMIN_PORT", 37655),
    "rerank": ("多用户.控制面.本地精排服务:app", "XJ_RERANK_HOST", "XJ_RERANK_PORT", 37657),
}


def 角色配置(role: str, environ: Mapping[str, str] | None = None) -> tuple[str, str, int]:
    env = environ or os.environ
    if role not in _角色:
        raise RuntimeError("宿主服务角色无效")
    application, host_name, port_name, default_port = _角色[role]
    host = env.get(host_name, "127.0.0.1")
    try:
        port = int(env.get(port_name, str(default_port)))
    except ValueError as exc:
        raise RuntimeError("宿主服务端口无效") from exc
    if host != "127.0.0.1" or not 1024 <= port <= 65535:
        raise RuntimeError("宿主服务只允许监听本机高位端口")
    return application, host, port


def _核对正式来源(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environ or os.environ
    source_text = env.get("XJ_RELEASE_SOURCE", "").strip()
    version = env.get("XJ_ACTIVE_VERSION", "").strip()
    source_sha256 = env.get("XJ_RELEASE_SOURCE_SHA256", "").strip()
    python_version = env.get("XJ_HOST_PYTHON_VERSION", "").strip()
    dependency_sha256 = env.get("XJ_HOST_DEPENDENCY_SHA256", "").strip()
    if (
        not source_text or not version or len(source_sha256) != 64
        or not python_version or len(dependency_sha256) != 64
    ):
        raise RuntimeError("宿主服务缺少正式版本身份")
    source = Path(source_text).expanduser().resolve()
    module = Path(__file__).resolve()
    if not source.is_dir() or source not in module.parents:
        raise RuntimeError("宿主服务没有从活动正式副本加载")
    return {
        "version": version,
        "source": str(source),
        "source_sha256": source_sha256,
        "python_version": python_version,
        "dependency_sha256": dependency_sha256,
        "module": str(module),
    }


def _写状态(value: dict[str, object], environ: Mapping[str, str] | None = None) -> None:
    env = environ or os.environ
    text = env.get("XJ_HOST_STATUS_FILE", "").strip()
    if not text:
        return
    path = Path(text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        payload = {**value, "pid": os.getpid(), "updated_at": time.time()}
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def main(role: str | None = None) -> None:
    selected = role or os.environ.get("XJ_HOST_ROLE", "")
    application, host, port = 角色配置(selected)
    identity = _核对正式来源()
    server = uvicorn.Server(uvicorn.Config(
        application,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=False,
        forwarded_allow_ips="",
    ))
    server.install_signal_handlers = lambda: None
    loop = asyncio.get_running_loop()

    def stop() -> None:
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    base = {**identity, "role": selected, "host": host, "port": port}
    _写状态({**base, "status": "starting"})
    task = asyncio.create_task(server.serve())
    try:
        while not server.started and not task.done():
            await asyncio.sleep(0.05)
        if server.started:
            _写状态({**base, "status": "running"})
        await task
        _写状态({**base, "status": "stopped"})
    except BaseException as exc:
        _写状态({**base, "status": "failed", "error": type(exc).__name__})
        raise


async def 旧单进程入口() -> None:
    """Keep the installed legacy LaunchAgent usable until the audited migration."""
    configured = {
        role: 角色配置(role)
        for role in _角色
    }
    ports = {item[2] for item in configured.values()}
    if len(ports) != len(configured):
        raise RuntimeError("三个旧宿主服务端口不能重复")
    servers = [
        uvicorn.Server(uvicorn.Config(
            application,
            host=host,
            port=port,
            log_level="info",
            proxy_headers=False,
            forwarded_allow_ips="",
        ))
        for application, host, port in configured.values()
    ]
    for server in servers:
        server.install_signal_handlers = lambda: None
    loop = asyncio.get_running_loop()

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    tasks = {asyncio.create_task(server.serve()) for server in servers}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    if any(task.exception() for task in done if not task.cancelled()):
        stop()
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    requested = sys.argv[1] if len(sys.argv) == 2 else None
    if requested == "legacy":
        asyncio.run(旧单进程入口())
    elif requested in _角色:
        asyncio.run(main(requested))
    else:
        raise SystemExit("用法：python -m 多用户.控制面 legacy|control|admin|rerank")

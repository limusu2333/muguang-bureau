"""Loopback control service used by the gateway and instance runners."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from ..版本身份 import 版本身份错误, 读取运行版本身份
from .账户认证 import 认证错误, 账户认证
from .执行器 import 一次性执行器, 执行拒绝
from .生命周期 import 生命周期
from .注册表 import 注册表错误, 账户注册表


_最大控制请求 = 64 * 1024
_最大反馈请求 = 7 * 1024 * 1024


async def _对象请求(request: Request, maximum: int = _最大控制请求) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > maximum:
        raise HTTPException(413, "控制请求过大")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "控制请求不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "控制请求体必须是对象")
    return value


def 控制令牌有效(header: str) -> bool:
    expected = os.environ.get("XJ_SUPERVISOR_TOKEN", "")
    if len(expected) < 32 or len(expected) > 256 or not header.startswith("Bearer "):
        return False
    supplied = header[7:]
    return len(supplied) == len(expected) and hmac.compare_digest(supplied, expected)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not 控制令牌有效("Bearer " + os.environ.get("XJ_SUPERVISOR_TOKEN", "")):
        raise RuntimeError("监督服务内部令牌无效")
    registry_path = Path(os.environ.get("XJ_REGISTRY_PATH", "/var/lib/xj/registry.sqlite3"))
    app.state.registry = 账户注册表(registry_path)
    app.state.auth = 账户认证(registry_path)
    pending = await asyncio.to_thread(app.state.registry.待恢复开通)
    app.state.provision_recovery = {
        "status": "clean",
        "pending": 0,
        "error": "",
    }
    if pending:
        try:
            recovered = await asyncio.to_thread(
                生命周期(app.state.registry).恢复未完成开通
            )
            app.state.provision_recovery = {
                "status": "recovered",
                "pending": 0,
                "recovered": int(recovered.get("recovered") or 0),
                "error": "",
            }
        except Exception as exc:  # 外部资源暂不可用时，不能把登录和控制服务一起杀掉。
            app.state.provision_recovery = {
                "status": "pending",
                "pending": len(pending),
                "error": type(exc).__name__,
            }
    app.state.runner = 一次性执行器()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def 验证控制通道(request: Request, call_next):
    if request.url.path != "/healthz" and not 控制令牌有效(request.headers.get("authorization", "")):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "控制通道未授权"}, status_code=403)
    return await call_next(request)


@app.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    # 未完成开户的外部资源可以等 Docker/平台恢复后再清理；它是需要处理的
    # 运维状态，不应让整个登录与控制入口反复崩溃、被 launchd 无限重启。
    recovery = getattr(
        request.app.state,
        "provision_recovery",
        {"status": "unknown", "pending": 0, "error": ""},
    )
    return {"ok": True, "provision_recovery": recovery}


@app.post("/control/runtime-context")
async def runtime_context() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(读取运行版本身份)
    except 版本身份错误 as exc:
        raise HTTPException(503, "当前正式版本身份无法确认") from exc


@app.post("/control/inspect-invite")
async def inspect_invite(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(request.app.state.auth.看邀请, str(body.get("token") or ""))
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/activate")
async def activate(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(
            request.app.state.auth.使用邀请,
            str(body.get("token") or ""),
            str(body.get("password") or ""),
        )
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/login")
async def login(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(
            request.app.state.auth.登录,
            str(body.get("email") or ""),
            str(body.get("password") or ""),
        )
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/unified-login")
async def unified_login(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    account = str(body.get("account") or "")
    password = str(body.get("password") or "")
    try:
        is_owner = await asyncio.to_thread(request.app.state.auth.管理员账户匹配, account)
        if is_owner:
            result = await asyncio.to_thread(request.app.state.auth.创建管理员登录交接, account, password)
            return {"destination": "management", **result}
        result = await asyncio.to_thread(request.app.state.auth.登录, account, password)
        return {"destination": "company", **result}
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/consume-user-handoff")
async def consume_user_handoff(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(
            request.app.state.auth.使用用户登录交接,
            str(body.get("token") or ""),
        )
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/resolve-session")
async def resolve_session(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(
            request.app.state.auth.解析会话,
            str(body.get("session_token") or ""),
        )
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/logout")
async def logout(request: Request) -> dict[str, bool]:
    body = await _对象请求(request)
    try:
        await asyncio.to_thread(
            request.app.state.auth.退出,
            str(body.get("session_token") or ""),
            str(body.get("csrf_token") or ""),
        )
        return {"ok": True}
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/control/profile")
async def profile(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(
            生命周期(request.app.state.registry).读取资料,
            str(body.get("account_id") or ""),
        )
    except (注册表错误, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/control/update-profile")
async def update_profile(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    changes = body.get("changes")
    if not isinstance(changes, dict):
        raise HTTPException(400, "用户资料内容无效")
    try:
        return await asyncio.to_thread(
            生命周期(request.app.state.registry).更新资料,
            str(body.get("account_id") or ""),
            changes,
        )
    except (注册表错误, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/control/status")
async def status(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    try:
        return await asyncio.to_thread(request.app.state.registry.状态, str(body.get("account_id") or ""))
    except 注册表错误 as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/control/announcements")
async def announcements(request: Request) -> dict[str, Any]:
    await _对象请求(request)
    return {
        "announcements": await asyncio.to_thread(
            request.app.state.registry.公告列表, only_published=True
        )
    }


@app.post("/control/feedback")
async def feedback(request: Request) -> dict[str, Any]:
    body = await _对象请求(request, _最大反馈请求)
    try:
        result = await asyncio.to_thread(
            request.app.state.registry.提交反馈,
            str(body.get("account_id") or ""),
            str(body.get("kind") or ""),
            str(body.get("body") or ""),
            str(body.get("page") or ""),
            str(body.get("screenshot") or ""),
        )
        return {"ok": True, "feedback_id": result["feedback_id"]}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/control/run-check")
async def run_check(request: Request) -> dict[str, Any]:
    body = await _对象请求(request)
    token = str(body.pop("control_token", ""))
    if any(field in body for field in ("account_id", "container", "container_name", "directory", "path")):
        raise HTTPException(400, "不得指定账户、容器或宿主路径")
    registry: 账户注册表 = request.app.state.registry
    account_id: str | None = None
    try:
        account = await asyncio.to_thread(registry.按控制令牌, token)
        account_id = str(account["account_id"])
        result = await asyncio.to_thread(request.app.state.runner.执行, account, body)
        await asyncio.to_thread(
            registry.记审计,
            actor="instance",
            action="run-check",
            account_id=account_id,
            result="ok" if result["exit_code"] == 0 else "failed",
            details={
                "command": str(body.get("command") or "")[:4096],
                "root": body.get("root"),
                "working_directory": body.get("working_directory"),
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
        )
        return result
    except (注册表错误, 执行拒绝) as exc:
        await asyncio.to_thread(
            registry.记审计,
            actor="instance",
            action="run-check",
            account_id=account_id,
            result="deny",
            details={"reason": str(exc), "command": str(body.get("command") or "")[:4096]},
        )
        raise HTTPException(403, str(exc)) from exc
